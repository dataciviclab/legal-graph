"""Connessione DuckDB cachata e VIEW sui mart compose.

Tre responsabilità separate in tre punti:
- `_new_con()`     → connessione con i soli settaggi (thread/memoria da env)
- `_bootstrap_views()` → lega le VIEW ai parquet risolti da `paths`
- `_get_con()`     → cache del processo: `_new_con` + `_bootstrap_views`

Tenerle separate permette ai test di esercitare il bootstrap su una
connessione propria, senza resettare la cache globale del server.

Settaggi (env, default = comportamento server prudente):
- `LEGAL_GRAPH_THREADS`       → thread DuckDB per query (default 1)
- `LEGAL_GRAPH_MEMORY_LIMIT`  → memoria per connessione (default 256MB)

Alzare i thread **richiede** di alzare anche la memoria: con 256MB DuckDB va
in OOM su una singola query parallela e `_run_sk` restituisce `[]` (nessun
risultato, non un errore). Per la suite: vedi `tests/conftest.py`.
"""
from __future__ import annotations

import functools
import os
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

from legal_graph.paths import (
    resolve_coverage_file,
    resolve_edges_file,
    resolve_emend_leg_file,
    resolve_massime_file,
    resolve_metrics_file,
    resolve_node_rel_file,
    resolve_nodes_file,
    resolve_search_keys_file,
    resolve_texts_file,
    source_exists,
)

_default_con: duckdb.DuckDBPyConnection | None = None

_DEFAULT_THREADS = 1
_DEFAULT_MEMORY_LIMIT = "256MB"
_MEMORY_RE = re.compile(r"^\d+(?:\.\d+)?\s?(?:KB|MB|GB|TB)$", re.IGNORECASE)


def reset_con() -> None:
    """Drop della connessione cachata (test / rebuild parziale)."""
    global _default_con
    _default_con = None


def _settings() -> tuple[int, str]:
    """Thread e memory limit dalle env, con validazione (errore leggibile)."""
    raw_threads = os.environ.get("LEGAL_GRAPH_THREADS", "").strip()
    raw_memory = os.environ.get("LEGAL_GRAPH_MEMORY_LIMIT", "").strip()
    threads = _DEFAULT_THREADS
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
    for nome, resolver in (
        ("coverage", resolve_coverage_file),
        ("metrics", resolve_metrics_file),
        ("search_keys", resolve_search_keys_file),
        ("node_rel", resolve_node_rel_file),
        ("emend_leg", resolve_emend_leg_file),
        ("texts", resolve_texts_file),
        ("massime", resolve_massime_file),
    ):
        path = resolver()
        if source_exists(path):
            con.execute(
                f"CREATE OR REPLACE VIEW {nome} AS SELECT * FROM read_parquet('{path}')"
            )


def _get_con() -> duckdb.DuckDBPyConnection:
    """Connessione DuckDB cachata con VIEW sui mart compose (fallback legacy)."""
    global _default_con
    if _default_con is not None:
        return _default_con
    con = _new_con()
    _bootstrap_views(con)
    _default_con = con
    return con


def _ensure_view(
    con: duckdb.DuckDBPyConnection,
    name: str,
    path: str | Path | None,
) -> None:
    """Crea la VIEW se il parquet esiste (locale o GCS) ma la view no."""
    if not source_exists(path) or _view_exists(con, name):
        return
    con.execute(
        f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{path}')"
    )


def refresh_views(con: duckdb.DuckDBPyConnection) -> None:
    """Riconnette le view mart dopo un rebuild parziale.

    Fuori dal percorso caldo: `_get_con` fa già il bootstrap completo. Serve
    solo quando `make run` aggiunge una tabella nuova a server avviato.
    """
    _ensure_view(con, "nodes", resolve_nodes_file())
    _ensure_view(con, "edges", resolve_edges_file())
    _ensure_view(con, "coverage", resolve_coverage_file())
    _ensure_view(con, "metrics", resolve_metrics_file())
    _ensure_view(con, "search_keys", resolve_search_keys_file())
    _ensure_view(con, "node_rel", resolve_node_rel_file())
    _ensure_view(con, "emend_leg", resolve_emend_leg_file())
    _ensure_view(con, "texts", resolve_texts_file())
    _ensure_view(con, "massime", resolve_massime_file())
