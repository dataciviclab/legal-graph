"""Connessione DuckDB cachata e VIEW sui mart compose."""
from __future__ import annotations

import functools
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

from legal_graph.paths import (
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

_cached_con: duckdb.DuckDBPyConnection | None = None


def reset_con() -> None:
    """Drop della connessione cachata (test / rebuild parziale)."""
    global _cached_con
    _cached_con = None


def _view_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    rows = con.execute("SHOW TABLES").fetchall()
    views = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type='VIEW'"
    ).fetchall()
    names = {r[0] for r in rows} | {r[0] for r in views}
    return name in names


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


def _get_con() -> duckdb.DuckDBPyConnection:
    """Connessione DuckDB cachata con VIEW sui mart compose (fallback legacy)."""
    global _cached_con
    if _cached_con is not None:
        return _cached_con
    nodes_file = resolve_nodes_file()
    edges_file = resolve_edges_file()
    metrics_file = resolve_metrics_file()
    search_keys_file = resolve_search_keys_file()
    node_rel_file = resolve_node_rel_file()
    emend_leg_file = resolve_emend_leg_file()
    texts_file = resolve_texts_file()
    massime_file = resolve_massime_file()
    con = duckdb.connect(":memory:")
    con.execute("SET memory_limit='256MB'")
    con.execute("SET threads=1")
    if not source_exists(nodes_file) or not source_exists(edges_file):
        raise FileNotFoundError(
            "Mart legal-graph non disponibili. Esegui `make run` locale "
            "oppure assicurati che GCS sia raggiungibile (paths.resolve)."
        )
    con.execute(f"CREATE OR REPLACE VIEW nodes AS SELECT * FROM read_parquet('{nodes_file}')")
    con.execute(f"CREATE OR REPLACE VIEW edges AS SELECT * FROM read_parquet('{edges_file}')")
    if source_exists(metrics_file):
        con.execute(
            f"CREATE OR REPLACE VIEW metrics AS SELECT * FROM read_parquet('{metrics_file}')"
        )
    if source_exists(search_keys_file):
        con.execute(
            f"CREATE OR REPLACE VIEW search_keys AS SELECT * FROM read_parquet('{search_keys_file}')"
        )
    if source_exists(node_rel_file):
        con.execute(
            f"CREATE OR REPLACE VIEW node_rel AS SELECT * FROM read_parquet('{node_rel_file}')"
        )
    if source_exists(emend_leg_file):
        con.execute(
            f"CREATE OR REPLACE VIEW emend_leg AS SELECT * FROM read_parquet('{emend_leg_file}')"
        )
    if source_exists(texts_file):
        con.execute(
            f"CREATE OR REPLACE VIEW texts AS SELECT * FROM read_parquet('{texts_file}')"
        )
    if source_exists(massime_file):
        con.execute(
            f"CREATE OR REPLACE VIEW massime AS SELECT * FROM read_parquet('{massime_file}')"
        )
    _cached_con = con
    return con


def _ensure_view(
    con: duckdb.DuckDBPyConnection,
    name: str,
    path: str | Path | None,
) -> bool:
    """Crea la VIEW se il parquet esiste (locale o GCS) ma la view no."""
    if not source_exists(path):
        return _view_exists(con, name)
    if _view_exists(con, name):
        return True
    con.execute(
        f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{path}')"
    )
    return True


def _refresh_views(con: duckdb.DuckDBPyConnection) -> None:
    """Riconnette le view mart dopo un rebuild parziale."""
    _ensure_view(con, "nodes", resolve_nodes_file())
    _ensure_view(con, "edges", resolve_edges_file())
    _ensure_view(con, "metrics", resolve_metrics_file())
    _ensure_view(con, "search_keys", resolve_search_keys_file())
    _ensure_view(con, "node_rel", resolve_node_rel_file())
    _ensure_view(con, "emend_leg", resolve_emend_leg_file())
    _ensure_view(con, "texts", resolve_texts_file())
    _ensure_view(con, "massime", resolve_massime_file())

