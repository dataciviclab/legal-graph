"""Fixture condivise per la suite: una sola connessione ai mart.

I mart sono VIEW su parquet: ogni chiamata MCP rilegge i file (831k righe su
`search_keys`) e una `legal_search` costa ~2s. Materializzandoli una volta in
tabelle temporanee la suite scende di oltre metà, senza cambiare cosa viene
verificato: restano gli stessi dati, letti una volta.

La materializzazione mantiene la semantica dell'MCP: le tabelle hanno gli
stessi nomi delle view e `_view_exists` le vede, quindi `refresh_views` non le
ricrea.

Settaggi: il server MCP usa `min(4, cpu)` thread e 1GB (i mart restano VIEW
sui parquet). Qui servono più thread e più memoria, perché la suite materializza
i mart in tabelle temporanee: dentro lo stesso budget, i thread paralleli non
hanno spazio.
"""

from __future__ import annotations

import os

import pytest

from legal_graph.mcp import db
from legal_graph.paths import (
    resolve_coverage_file,
    resolve_edges_file,
    resolve_edges_in_file,
    resolve_emend_leg_file,
    resolve_entity_alias_file,
    resolve_metrics_file,
    resolve_node_rel_file,
    resolve_nodes_file,
    resolve_relation_type_file,
    resolve_search_keys_file,
    source_exists,
)

# Da impostare prima che `db._get_con()` crei la connessione: `setdefault` per
# lasciare la precedenza a chi lancia la suite con valori propri.
os.environ.setdefault("LEGAL_GRAPH_THREADS", str(min(8, os.cpu_count() or 1)))
os.environ.setdefault("LEGAL_GRAPH_MEMORY_LIMIT", "3GB")

MART = {
    "nodes": resolve_nodes_file,
    "edges": resolve_edges_file,
    "edges_in": resolve_edges_in_file,
    "entity_alias_src": resolve_entity_alias_file,
    "relation_type_src": resolve_relation_type_file,
    "coverage": resolve_coverage_file,
    "metrics": resolve_metrics_file,
    "search_keys": resolve_search_keys_file,
    "node_rel": resolve_node_rel_file,
    "emend_leg": resolve_emend_leg_file,
}

# `texts` (77 MB di testo) e `massime` (11 MB) restano VIEW: la copia
# integrale di `texts` costa ~10 s a freddo, mentre solo `test_legal_text`
# la interroga (5 lookup). Con `row_group_size: 2048` un lookup legge un
# gruppo: più economico del copia-incolla in tabella.
#
# `search_terms` (indice BM25, ~7M righe) resta una VIEW: materializzarlo
# dentro il budget di 3GB sposterebbe la suite in OOM per pochi test che
# lo interrogano. La view costa ~30 ms per query, accettabile qui.

_MART_ASSENTE = "mart assente — esegui `make run` o `make download-gcs`"


def _materializza(con) -> None:
    """Sostituisce le view con tabelle temporanee equivalenti."""
    for nome, resolver in MART.items():
        path = resolver()
        if not source_exists(path) or not db._view_exists(con, nome):
            continue
        # solo le VIEW vanno sostituite: se è già una tabella (materializzazione
        # già avvenuta) lasciala stare, così l'errore lo segnala il test di
        # contratto e non un DROP VIEW opaco qui dentro
        if not _e_view(con, nome):
            continue
        con.execute(f"CREATE TEMP TABLE _m_{nome} AS SELECT * FROM read_parquet('{path}')")
        con.execute(f"DROP VIEW {nome}")
        con.execute(f"ALTER TABLE _m_{nome} RENAME TO {nome}")


def _e_view(con, nome: str) -> bool:
    row = con.execute(
        "SELECT table_type FROM information_schema.tables WHERE table_name = ?",
        [nome],
    ).fetchone()
    return row is not None and row[0] == "VIEW"


@pytest.fixture(scope="session", autouse=True)
def _mart_in_memoria():
    """Materializza i mart una volta, sulla connessione condivisa."""
    originale = db._get_con
    fatto = False

    def wrapper():
        nonlocal fatto
        con = originale()
        if not fatto:
            _materializza(con)
            fatto = True
        return con

    db._get_con = wrapper
    try:
        yield
    finally:
        db._get_con = originale
        db.reset_con()


@pytest.fixture(scope="session")
def con(_mart_in_memoria):
    """Connessione ai mart, materializzata: quella usata da tutti i test.

    In CI (`LEGAL_GRAPH_REQUIRE_MART=1`) un mart mancante è un errore: la
    pipeline costruisce i mart prima dei test, uno skip silenzioso
    nasconderebbe un build rotto.
    """
    try:
        connection = db._get_con()
    except FileNotFoundError:
        connection = None
    if connection is None or not db._view_exists(connection, "search_keys"):
        if os.environ.get("LEGAL_GRAPH_REQUIRE_MART") == "1":
            pytest.fail(_MART_ASSENTE)
        pytest.skip(_MART_ASSENTE)
    return connection


# --- Test che richiedono il mart pubblicato ---------------------------------
# Questi moduli asseriscono valori del corpus (golden search, copertura, URN):
# senza il mart falliscono, e non per un difetto del codice. Sono integrazione,
# non unit. Il mart si costruisce nella pipeline (`make run`): lì girano tutti.
# In CI, su un cambio di codice, girano gli altri (0 byte, nessun download).
#
# Per rigenerare la lista: LEGAL_GRAPH_MART_DIR=/percorso/vuoto pytest -q
# (i 97 test che passano sono gli unit; i moduli che danno errore vanno qui).
MART_MODULES = {
    "test_search_golden",
    "test_massime_date",
    "test_graph_integrity",
    "test_search_terms",
    "test_coverage",
    "test_legal_text",
    "test_tabelle_pubblicate",
    "test_e2e_contratto",
    "test_attua_delega",
    "test_paths_source",
    "test_build_cost",
    "test_freshness",
    "test_locator_akn",
    "test_m1_identity",
    # Usano il mart senza passare dalla fixture `con`: senza marker, in un
    # checkout pulito danno FileNotFoundError invece di essere esclusi.
    "test_concorrenza",
    "test_legal_catena",
}


def pytest_collection_modifyitems(config, items):
    """Marca come `mart` i test che leggono il mart pubblicato."""
    for item in items:
        module = item.module.__name__.rsplit(".", 1)[-1]
        if module in MART_MODULES:
            item.add_marker(pytest.mark.mart)
