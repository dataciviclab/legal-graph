"""Fixture condivise per la suite.

I mart sono VIEW su parquet: ogni chiamata MCP rilegge i file (525k nodi,
525k chiavi di ricerca) e una `legal_search` costa ~1,5s. Materializzando i
mart in tabelle temporanee all'inizio della sessione la suite scende di oltre
metà, senza cambiare cosa viene verificato: restano gli stessi dati, letti una
volta.

La materializzazione mantiene la semantica dell'MCP: le tabelle hanno gli
stessi nomi delle view e `_view_exists` (che guarda `SHOW TABLES`) le vede,
quindi `_ensure_view`/`_refresh_views` non le ricreano.
"""

from __future__ import annotations

import pytest

from legal_graph.mcp import db, node_views, reports, search
from legal_graph.paths import (
    resolve_edges_file,
    resolve_emend_leg_file,
    resolve_massime_file,
    resolve_metrics_file,
    resolve_node_rel_file,
    resolve_nodes_file,
    resolve_search_keys_file,
    resolve_temporal_file,
    resolve_texts_file,
    source_exists,
)

MART = {
    "nodes": resolve_nodes_file,
    "edges": resolve_edges_file,
    "metrics": resolve_metrics_file,
    "search_keys": resolve_search_keys_file,
    "node_rel": resolve_node_rel_file,
    "emend_leg": resolve_emend_leg_file,
    "texts": resolve_texts_file,
    "massime": resolve_massime_file,
    "temporal": resolve_temporal_file,
}


def _materializza(con) -> None:
    """Sostituisce le view con tabelle temporanee equivalenti."""
    con.execute("SET memory_limit='3GB'")
    for nome, resolver in MART.items():
        path = resolver()
        if not source_exists(path) or not db._view_exists(con, nome):
            continue
        con.execute(f"CREATE TEMP TABLE _m_{nome} AS SELECT * FROM read_parquet('{path}')")
        con.execute(f"DROP VIEW {nome}")
        con.execute(f"ALTER TABLE _m_{nome} RENAME TO {nome}")


@pytest.fixture(scope="session", autouse=True)
def _mart_in_memoria():
    """Materializza i mart una volta per connessione creata."""
    originale = db._get_con
    fatti: set[int] = set()

    def wrapper():
        con = originale()
        if id(con) not in fatti:
            _materializza(con)
            fatti.add(id(con))
        return con

    # i moduli importano `_get_con` per nome: va sostituito dove è usato
    from legal_graph import mcp_server

    moduli = [db, search, node_views, reports, mcp_server]
    originali = {mod: mod._get_con for mod in moduli if hasattr(mod, "_get_con")}
    for mod in originali:
        mod._get_con = wrapper
    # i fixture di modulo chiamano `_reset_con()` per avere una connessione
    # fresca: in una sessione di test il mart non cambia, quindi la connessione
    # materializzata resta valida e non si ripaga il setup (5s per modulo)
    reset_originale = mcp_server._reset_con
    mcp_server._reset_con = lambda: None
    try:
        yield
    finally:
        for mod, fn in originali.items():
            mod._get_con = fn
        mcp_server._reset_con = reset_originale
        db.reset_con()


@pytest.fixture(scope="session")
def mart_pronto():
    """Salta i test se i mart non sono stati costruiti (`make run`)."""
    import legal_graph.mcp_server as m

    m._reset_con()
    con = m._get_con()
    if not m._has_mart_search(con):
        pytest.skip("mart assente — esegui `make run`")
    return con
