"""Concorrenza sull'MCP: la connessione DuckDB condivisa deve essere serializzata.

Regressione: `db._get_con()` cachea una sola connessione e il server MCP serve
le richieste in concorrenza. Senza lock due `execute()` interlacciati fanno
leggere a una chiamata il cursore dell'altra → risultati **vuoti spuri**,
indistinguibili da un risultato legittimo (misurato prima del fix: 2 vuoti su
6 chiamate identiche in parallelo, 0 su 6 sequenziali).

Il test è deterministico *con* il lock: tutte le chiamate devono restituire lo
stesso risultato. Senza lock fallisce in modo intermittente (è il punto).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

import legal_graph.mcp_server as m

SQL = "SELECT COUNT(*) AS n, SUM(LENGTH(title)) AS s FROM nodes WHERE source='normativa'"


@pytest.fixture(scope="module")
def graph_ready():
    m._reset_con()
    con = m._get_con()
    if not m._has_mart_search(con):
        pytest.skip("mart assente — esegui `make run`")
    return con


def _esito(righe: list[dict]) -> tuple:
    if not righe:
        return ("vuoto",)
    r = righe[0]
    return (r.get("note") or r.get("error"), r.get("n"), r.get("s"))


def test_query_parallele_identiche(graph_ready):
    """8 chiamate concorrenti sulla stessa query: stesso risultato, mai vuoto."""
    with ThreadPoolExecutor(max_workers=8) as ex:
        risultati = list(ex.map(lambda _: m._impl_query(SQL, limit=1), range(8)))
    esiti = [_esito(r) for r in risultati]
    assert len(set(esiti)) == 1, f"risultati divergenti: {esiti}"
    assert esiti[0][0] is None, f"risultato spurio: {esiti[0]}"
    assert esiti[0][1] and esiti[0][1] > 10000, f"conteggio sospetto: {esiti[0]}"


def test_query_parallele_di_tipo_diverso(graph_ready):
    """Ricerche, query e nodi mescolati in concorrenza: nessuno torna vuoto."""
    chiamate = [
        lambda: m._impl_query(SQL, limit=1),
        lambda: m._impl_search("elettroniche", materia="comunicazioni", limit=3),
        lambda: m._impl_node("urn:nir:stato:decreto.legislativo:2003-08-01;259"),
        lambda: m._impl_query("SELECT materia, COUNT(*) AS n FROM nodes GROUP BY materia LIMIT 3", limit=3),
    ] * 2
    with ThreadPoolExecutor(max_workers=8) as ex:
        risultati = list(ex.map(lambda f: f(), chiamate))
    vuoti = [r for r in risultati if not r or (isinstance(r, list) and r and r[0].get("note"))]
    assert not vuoti, f"chiamate tornate vuote: {vuoti}"
