"""Connessione DuckDB cachata e VIEW sui mart compose.

Tre responsabilità separate in tre punti:
- `_new_con()`     → connessione con i soli settaggi (thread/memoria da env)
- `_bootstrap_views()` → lega le VIEW ai parquet risolti da `paths`
- `_get_con()`     → cache del processo: `_new_con` + `_bootstrap_views`

Tenerle separate permette ai test di esercitare il bootstrap su una
connessione propria, senza resettare la cache globale del server.

Settaggi (env):
- `LEGAL_GRAPH_THREADS`       → thread DuckDB per query (default min(4, cpu))
- `LEGAL_GRAPH_MEMORY_LIMIT`  → memoria per connessione (default 1GB)

I thread contano: una `legal_search` è uno scan completo di `search_keys`
(831k righe, nessun indice utilizzabile per `LIKE '%…%'`), quindi passa da
~2,4s a 1 thread a ~0,9s con 4. Il limite di memoria è un tetto, non una
prenotazione (RSS reale ~350MB). 256MB non bastano: la memoria si accumula
nella connessione tra chiamate, e dopo qualche query pesante i thread
paralleli vanno in OOM — che ora diventa un errore esplicito (`_errore_sk`),
non più una lista vuota.
"""
from __future__ import annotations

import functools
import logging
import os
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

from legal_graph.paths import (
    is_remote,
    resolve_coverage_file,
    resolve_edges_file,
    resolve_edges_in_file,
    resolve_emend_leg_file,
    resolve_massime_file,
    resolve_metadata_file,
    resolve_metrics_file,
    resolve_node_rel_file,
    resolve_nodes_file,
    resolve_search_keys_file,
    resolve_texts_file,
    source_exists,
)

logger = logging.getLogger(__name__)

_default_con: duckdb.DuckDBPyConnection | None = None

_DEFAULT_MEMORY_LIMIT = "1GB"
_MEMORY_RE = re.compile(r"^\d+(?:\.\d+)?\s?(?:KB|MB|GB|TB)$", re.IGNORECASE)


def _default_threads() -> int:
    """Thread per query: 4 bastano a saturare lo scan, oltre costa memoria."""
    return max(1, min(4, os.cpu_count() or 1))


def reset_con() -> None:
    """Drop della connessione cachata (test / rebuild parziale)."""
    global _default_con
    _default_con = None


def _settings() -> tuple[int, str]:
    """Thread e memory limit dalle env, con validazione (errore leggibile)."""
    raw_threads = os.environ.get("LEGAL_GRAPH_THREADS", "").strip()
    raw_memory = os.environ.get("LEGAL_GRAPH_MEMORY_LIMIT", "").strip()
    threads = _default_threads()
    if raw_threads:
        try:
            threads = int(raw_threads)
        except ValueError as exc:
            raise ValueError(
                f"LEGAL_GRAPH_THREADS={raw_threads!r} non intero"
            ) from exc
        if threads < 1:
            raise ValueError(f"LEGAL_GRAPH_THREADS={threads} (atteso >= 1)")
    memory = raw_memory or _DEFAULT_MEMORY_LIMIT
    if not _MEMORY_RE.match(memory):
        raise ValueError(
            f"LEGAL_GRAPH_MEMORY_LIMIT={memory!r} non valido (es. '512MB', '3GB')"
        )
    return threads, memory


def _view_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
        [name],
    ).fetchone()
    return row is not None


def _has_col(con: duckdb.DuckDBPyConnection, view: str, col: str) -> bool:
    """True se `view` espone la colonna `col` (mart legacy senza la colonna)."""
    try:
        rows = con.execute(f"DESCRIBE {view}").fetchall()
    except duckdb.Error:
        return False
    return any(r[0] == col for r in rows)


# DuckDB non è thread-safe su una connessione condivisa: il server MCP serve le
# richieste in concorrenza e due `execute()` interlacciati fanno leggere a una
# chiamata il cursore dell'altra → risultati vuoti spuri, senza errore (misurato:
# 2 su 6 chiamate identiche in parallelo, 0 su 6 sequenziali). Il lock serializza
# l'accesso; è un RLock perché le funzioni decorate possono richiamarsi.
_DB_LOCK = threading.RLock()


@contextmanager
def db_lock() -> Iterator[None]:
    """Tiene il lock della connessione DuckDB condivisa."""
    with _DB_LOCK:
        yield


def locked(fn):
    """Decorator: esegue `fn` sotto il lock della connessione condivisa.

    Da usare sulle entry point MCP che leggono il grafo (`_impl_query`,
    `_impl_search`, `_impl_node`, `_impl_insights`). Non sulle funzioni che
    fanno I/O di rete: il lock non deve coprire le chiamate HTTP.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _DB_LOCK:
            return fn(*args, **kwargs)

    return wrapper


def _new_con() -> duckdb.DuckDBPyConnection:
    """Connessione DuckDB vuota con i settaggi di processo."""
    threads, memory = _settings()
    con = duckdb.connect(":memory:")
    con.execute(f"SET threads={threads}")
    con.execute(f"SET memory_limit='{memory}'")
    return con


def _bootstrap_views(con: duckdb.DuckDBPyConnection) -> None:
    """Lega le VIEW ai parquet mart (locale o GCS) risolti da `paths`.

    Chiamata una volta per connessione. Non riconnette: per recuperare mart
    comparsi dopo il bootstrap usare `refresh_views`.
    """
    nodes_file = resolve_nodes_file()
    edges_file = resolve_edges_file()
    if not source_exists(nodes_file) or not source_exists(edges_file):
        raise FileNotFoundError(
            "Mart legal-graph non disponibili. Esegui `make run` locale "
            "oppure assicurati che GCS sia raggiungibile (paths.resolve)."
        )
    con.execute(f"CREATE OR REPLACE VIEW nodes AS SELECT * FROM read_parquet('{nodes_file}')")
    con.execute(f"CREATE OR REPLACE VIEW edges AS SELECT * FROM read_parquet('{edges_file}')")
    # Tabelle opzionali: alcune sono pubblicate su GCS solo dal primo run di
    # pipeline (edges_in, texts, massime). `source_exists` dice True anche per
    # una URL che risponde 404 — e DuckDB legge lo schema alla CREATE VIEW,
    # quindi la view fallirebbe subito. `_sorgente_leggibile` fa un probe.
    pubblicate = _tabelle_pubblicate(con)
    for vista, tabella, resolver in _mart_opzionali():
        if pubblicate is not None and tabella not in pubblicate:
            continue
        _ensure_view(con, vista, resolver(), remoto_verificato=pubblicate is not None)


def _get_con() -> duckdb.DuckDBPyConnection:
    """Connessione DuckDB cachata con VIEW sui mart compose (fallback legacy)."""
    global _default_con
    if _default_con is not None:
        return _default_con
    con = _new_con()
    _bootstrap_views(con)
    _default_con = con
    return con


def _mart_opzionali() -> tuple[tuple[str, str, object], ...]:
    """(view, tabella mart, resolver) delle tabelle che possono mancare.

    Costruita a ogni chiamata perché i resolver sono funzioni di modulo:
    risolverli qui rende sostituibili nei test (come il resto del modulo).
    """
    return (
        ("edges_in", "mart_legal_edges_in", resolve_edges_in_file),
        ("coverage", "mart_legal_coverage", resolve_coverage_file),
        ("metrics", "mart_legal_node_metrics", resolve_metrics_file),
        ("search_keys", "mart_legal_search_keys", resolve_search_keys_file),
        ("node_rel", "mart_legal_node_rel", resolve_node_rel_file),
        ("emend_leg", "mart_legal_emend_leg", resolve_emend_leg_file),
        ("texts", "mart_legal_texts", resolve_texts_file),
        ("massime", "mart_legal_massime", resolve_massime_file),
    )


def _tabelle_pubblicate(con: duckdb.DuckDBPyConnection) -> set[str] | None:
    """Tabelle elencate in `metadata.json` del publish, o None se illeggibile.

    Un probe per tabella costa un GET remoto (~0,3s): il metadata è una
    richiesta sola e dice cosa è stato caricato davvero.
    """
    path = resolve_metadata_file()
    if path is None:
        return None
    try:
        rows = con.execute(
            "SELECT t.name FROM (SELECT unnest(tables) AS t FROM read_json_auto(?))",
            [str(path)],
        ).fetchall()
    except duckdb.Error:
        return None
    nomi = {r[0] for r in rows if r[0]}
    return nomi or None


def _sorgente_leggibile(
    con: duckdb.DuckDBPyConnection, path: str | Path | None
) -> bool:
    """True se il parquet è davvero leggibile.

    Per un file locale basta `source_exists`. Per un URL remoto no: una tabella
    non ancora pubblicata passerebbe il controllo e lascerebbe una view che
    fallisce a *ogni* query (peggio di non averla). Qui si fa un probe leggero
    (solo metadati parquet); se fallisce si usa il fallback.
    """
    if not source_exists(path):
        return False
    if not is_remote(path):
        return True
    try:
        con.execute("SELECT 1 FROM read_parquet(?) LIMIT 0", [str(path)])
    except duckdb.Error:
        return False
    return True


def _ensure_view(
    con: duckdb.DuckDBPyConnection,
    name: str,
    path: str | Path | None,
    *,
    remoto_verificato: bool = False,
) -> None:
    """Crea la VIEW se il parquet esiste (locale o GCS) ma la view no.

    `remoto_verificato` salta il probe HTTP: lo usa il bootstrap quando
    `metadata.json` elenca già quella tabella tra quelle pubblicate.
    """
    if _view_exists(con, name) or not source_exists(path):
        return
    if not remoto_verificato and not _sorgente_leggibile(con, path):
        return
    try:
        con.execute(
            f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{path}')"
        )
    except duckdb.Error as exc:
        # `remoto_verificato` è un'informazione, non una garanzia: se il bucket
        # non ha (più) quel parquet la view non è essenziale e la feature
        # degrada (testi da GitHub raw, archi in entrata su `edges`). Su file
        # locale invece l'errore deve restare rumoroso.
        if not is_remote(path):
            raise
        logger.warning("view %s non creata (%s): %s", name, path, exc)


def refresh_views(con: duckdb.DuckDBPyConnection) -> None:
    """Riconnette le view mart dopo un rebuild parziale.

    **Non è più automatica.** Veniva chiamata a ogni tool (~50ms di query di
    catalogo per chiamata) ed è stata tolta dal percorso caldo: `_get_con` fa
    già il bootstrap completo. Conseguenza voluta: un server già avviato non
    vede tabelle comparse dopo `make run` — per quello serve riavviare, oppure
    chiamare questa funzione (la usa il test del bootstrap).
    """
    _ensure_view(con, "nodes", resolve_nodes_file())
    _ensure_view(con, "edges", resolve_edges_file())
    _ensure_view(con, "edges_in", resolve_edges_in_file())
    _ensure_view(con, "coverage", resolve_coverage_file())
    _ensure_view(con, "metrics", resolve_metrics_file())
    _ensure_view(con, "search_keys", resolve_search_keys_file())
    _ensure_view(con, "node_rel", resolve_node_rel_file())
    _ensure_view(con, "emend_leg", resolve_emend_leg_file())
    _ensure_view(con, "texts", resolve_texts_file())
    _ensure_view(con, "massime", resolve_massime_file())
