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
    resolve_entity_alias_file,
    resolve_massime_file,
    resolve_metadata_file,
    resolve_metrics_file,
    resolve_node_rel_file,
    resolve_nodes_file,
    resolve_relation_type_file,
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


_META_VIEWS_SQL = (
    # act: view tipizzata sugli atti; le entità di processo restano in nodes.
    """
    CREATE OR REPLACE VIEW act AS
    SELECT
        id AS act_id,
        CASE WHEN id LIKE 'urn:nir:%' THEN id END AS urn,
        tipo AS act_type,
        numero AS number,
        anno AS year,
        data AS enacted_at,
        NULL::VARCHAR AS published_at,
        eiv AS in_force_from,
        CASE
            WHEN id LIKE 'senato:%' THEN 'senato'
            WHEN id LIKE 'camera:%' THEN 'camera'
        END AS chamber,
        CASE
            WHEN id LIKE 'camera:%' THEN TRY_CAST(split_part(id, ':', 2) AS INTEGER)
        END AS legislature,
        title,
        stato AS status,
        vigenza_fonte AS status_authority,
        CASE
            WHEN stato IS NULL THEN 'unknown'
            WHEN vigenza_fonte IN ('normattiva', 'atti_api') THEN 'strong'
            ELSE 'weak'
        END AS status_confidence,
        NULL::VARCHAR AS status_from,
        NULL::VARCHAR AS status_to,
        NULL::VARCHAR AS snapshot_id
    FROM nodes
    WHERE tipo NOT IN (
        'EMENDAMENTO', 'SOMMARIO', 'RISOLUZIONE', 'VOTAZIONE',
        'DEPUTATO', 'SENATORE', 'GIUDICE', 'PNRR'
    )
    """,
    # coverage_level: L1-L2 per fonte/periodo/tipo/granularità.
    """
    CREATE OR REPLACE VIEW coverage_level AS
    WITH units AS (
        SELECT id, source, anno AS period, tipo AS act_type, 'act' AS granularity
        FROM nodes
        WHERE tipo NOT IN (
            'EMENDAMENTO', 'SOMMARIO', 'RISOLUZIONE', 'VOTAZIONE',
            'DEPUTATO', 'SENATORE', 'GIUDICE', 'PNRR'
        )
    ),
    touched AS (
        SELECT DISTINCT source_id AS id FROM edges
        UNION
        SELECT DISTINCT target_id AS id FROM edges
    )
    SELECT 'L1' AS level, source, period, act_type, granularity,
           COUNT(*) AS n_units, COUNT(*) AS n_with_data, 100.0 AS pct
    FROM units
    GROUP BY 1, 2, 3, 4, 5
    UNION ALL
    SELECT 'L2', u.source, u.period, u.act_type, u.granularity,
           COUNT(*) AS n_units,
           COUNT(t.id) AS n_with_data,
           100.0 * COUNT(t.id) / COUNT(*) AS pct
    FROM units u
    LEFT JOIN touched t ON t.id = u.id
    GROUP BY 2, 3, 4, 5
    """,
)

# Fallback per mart legacy privi di `mart_legal_relation_type` /
# `mart_legal_entity_alias`: congelano il contratto M0. La sorgente primaria
# resta la tabella mart; questi blocchi non vanno estesi con nuove relazioni
# o alias (si aggiorna il compositore, non il fallback).
_RELATION_TYPE_FALLBACK_SQL = """
    CREATE OR REPLACE VIEW relation_type AS
    SELECT * FROM (VALUES
        ('riferimento','norm','act','act','article',false,false,true,'weak',true,true,false,'cita','citazione testuale'),
        ('cita_costituzione','norm','act','provision','article',false,false,true,'strong',true,true,true,'cita','citazione Costituzione'),
        ('modifica','norm','act','act','article',true,true,true,'strong',true,false,false,'modifica','modifica attiva'),
        ('abroga','norm','act','act','article',true,true,true,'strong',true,true,true,'abroga','abrogazione'),
        ('sostituisce','norm','act','act','article',true,true,true,'strong',true,false,true,'modifica','sostituzione'),
        ('split','norm','act','act','article',true,true,true,'strong',true,false,true,'riordino','split AKN'),
        ('join','norm','act','act','article',true,true,true,'strong',true,false,true,'riordino','join AKN'),
        ('renumbering','norm','act','act','article',true,true,true,'strong',true,false,true,'riordino','rinumerazione AKN'),
        ('attua_delega','norm','act','act','article',true,false,true,'weak',true,true,true,'attua','attuazione di delega (euristica)'),
        ('converte_decreto_legge','proc','act','act','act',false,false,false,'strong',true,false,true,'conversione','conversione tentata'),
        ('diventa_legge','proc','act','act','act',false,false,false,'strong',true,true,true,'conversione','DDL → legge'),
        ('impugna','juris','act','act','article',false,false,false,'strong',true,true,false,'impugna','impugnazione'),
        ('invoca_parametro','juris','act','provision','article',false,false,false,'strong',true,true,false,'parametro','parametro costituzionale'),
        ('evoca_parametro','juris','act','provision','article',false,false,false,'strong',true,true,false,'parametro','parametro da promovimento'),
        ('relatore_sentenza','juris','act','person','act',false,false,false,'strong',false,false,false,'relatore','relatore sentenza'),
        ('emendamento','proc','document','act','act',false,false,false,'strong',false,false,false,'emendamento','emendamento'),
        ('firmatario','proc','person','act','act',false,false,false,'strong',false,false,false,'firmatario','firma'),
        ('relatore','proc','person','act','act',false,false,false,'strong',false,false,false,'relatore','relatore'),
        ('vota','proc','vote','act','act',false,false,false,'strong',false,false,false,'voto','voto'),
        ('intervento','proc','document','person','act',false,false,false,'strong',false,false,false,'documento','intervento'),
        ('testo_atto','doc','document','act','act',false,false,false,'strong',false,false,false,'documento','documento → atto'),
        ('proposta_cost','proc','act','act','act',false,false,false,'strong',false,false,false,'iter_costituzionale','proposta costituzionale'),
        ('diventa_revisione','proc','act','act','act',false,false,false,'strong',false,false,false,'iter_costituzionale','proposta → revisione'),
        ('collega','identity','person','person','act',false,false,false,'strong',false,false,false,'identita','ponte persona'),
        ('referenzia_pnrr','program','act','program','act',false,false,false,'weak',false,false,false,'cita','riferimento PNRR')
    ) AS t(relation, family, domain, range, granularity, directed, temporal,
           inferable, reliability, in_chain, in_impact, in_chain_view,
           canonical_relation, description)
"""

_ENTITY_ALIAS_FALLBACK_SQL = """
    CREATE OR REPLACE VIEW entity_alias AS
    SELECT
        id AS alias_id,
        'camera:' || split_part(split_part(id, ':', 3), '_', 1)
            || ':' || split_part(split_part(id, ':', 3), '_', 2) AS entity_id,
        'old_id' AS alias_type,
        NULL::VARCHAR AS valid_from,
        NULL::VARCHAR AS valid_to
    FROM nodes
    WHERE regexp_matches(id, '^camera:atto:[0-9]+_[0-9]+$')
    UNION ALL
    SELECT d.id, u.id, 'old_id', NULL::VARCHAR, NULL::VARCHAR
    FROM nodes d
    JOIN nodes u
      ON u.id LIKE 'urn:nir:stato:decreto.legge:%'
     AND regexp_extract(d.id, 'dl:(\\d{4})-(\\d+)$', 1)
         = regexp_extract(u.id, 'urn:nir:stato:decreto.legge:(\\d{4})-\\d{2}-\\d{2};(\\d+)$', 1)
     AND regexp_extract(d.id, 'dl:(\\d{4})-(\\d+)$', 2)
         = regexp_extract(u.id, 'urn:nir:stato:decreto.legge:(\\d{4})-\\d{2}-\\d{2};(\\d+)$', 2)
    WHERE d.id LIKE 'dl:%'
    UNION ALL
    SELECT n.id, u.id, 'old_id', NULL::VARCHAR, NULL::VARCHAR
    FROM nodes n
    JOIN nodes u
      ON u.id LIKE 'urn:nir:stato:decreto.legge:%'
     AND regexp_extract(n.id, 'norma:decreto legge:(\\d+):(\\d{4})$', 1)
         = regexp_extract(u.id, 'urn:nir:stato:decreto.legge:(\\d{4})-\\d{2}-\\d{2};(\\d+)$', 2)
     AND regexp_extract(n.id, 'norma:decreto legge:(\\d+):(\\d{4})$', 2)
         = regexp_extract(u.id, 'urn:nir:stato:decreto.legge:(\\d{4})-\\d{2}-\\d{2};(\\d+)$', 1)
    WHERE n.id LIKE 'norma:decreto legge:%'
"""

_CONVERSIONE_SQL = """
    CREATE OR REPLACE VIEW conversione AS
    SELECT
        e.source_id,
        e.target_id,
        e.relation,
        e.weight,
        e.source_year,
        e.target_year,
        CASE e.relation
            WHEN 'converte_decreto_legge' THEN 'dl_to_law'
            WHEN 'diventa_legge' THEN 'ddl_to_law'
        END AS conversion_type
    FROM edges e
    JOIN relation_type rt ON rt.relation = e.relation
    WHERE rt.canonical_relation = 'conversione'
"""


def _relation_type_view_sql(con: duckdb.DuckDBPyConnection) -> str:
    if _view_exists(con, "relation_type_src"):
        return "CREATE OR REPLACE VIEW relation_type AS SELECT * FROM relation_type_src"
    return _RELATION_TYPE_FALLBACK_SQL


def _entity_alias_view_sql(con: duckdb.DuckDBPyConnection) -> str:
    if _view_exists(con, "entity_alias_src"):
        return "CREATE OR REPLACE VIEW entity_alias AS SELECT * FROM entity_alias_src"
    return _ENTITY_ALIAS_FALLBACK_SQL


def _bootstrap_meta_views(con: duckdb.DuckDBPyConnection) -> None:
    """Crea le view di contratto (act, relation_type, entity_alias, coverage).

    Se una view non è compatibile con un mart legacy, la si salta con un
    warning: il resto dell'MCP deve continuare a funzionare.
    """
    candidates = [
        _META_VIEWS_SQL[0],  # act
        _relation_type_view_sql(con),
        _entity_alias_view_sql(con),
        _META_VIEWS_SQL[1],  # coverage_level
    ]
    for sql in candidates:
        try:
            con.execute(sql)
        except duckdb.Error as exc:
            logger.warning("view di contratto non creata: %s", exc)
    if _view_exists(con, "relation_type"):
        try:
            con.execute(_CONVERSIONE_SQL)
        except duckdb.Error as exc:
            logger.warning("view di contratto non creata: %s", exc)


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
    _bootstrap_meta_views(con)


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
        ("entity_alias_src", "mart_legal_entity_alias", resolve_entity_alias_file),
        ("relation_type_src", "mart_legal_relation_type", resolve_relation_type_file),
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
    # stesse view opzionali del bootstrap: una lista sola (`_mart_opzionali`),
    # così una tabella nuova non resta fuori dal refresh.
    for vista, _tabella, resolver in _mart_opzionali():
        _ensure_view(con, vista, resolver())
    _bootstrap_meta_views(con)
