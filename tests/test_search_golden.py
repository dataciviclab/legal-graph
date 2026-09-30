"""Golden tests — ranking search legal-graph.

Proteggono i casi degli agenti (giro 4). Se il mart search manca,
i test skippino con hint `make run`.
"""

from __future__ import annotations

import pytest

import legal_graph.mcp_server as m


@pytest.fixture(scope="module")
def search_ready():
    m._cached_con = None
    con = m._get_con()
    if not m._has_mart_search(con):
        pytest.skip("mart search assente — esegui `make run`")
    return con


@pytest.mark.parametrize(
    "query,expected_substring,limit",
    [
        ("231/2001", "decreto.legislativo:2001-06-08;231", 3),
        ("n. 231 del 2001", "decreto.legislativo:2001-06-08;231", 3),
        ("231", "decreto.legislativo:2001-06-08;231", 3),
        ("responsabilità amministrativa", "decreto.legislativo:2001-06-08;231", 5),
        ("sentenza 2009 151", "sentenza:2009-0151", 3),
        ("ECLI:IT:COST:2009:151", "sentenza:2009-0151", 3),
        ("art. 3 della Costituzione", "costituzione:art:3", 3),
        ("direttiva UE 2019/1937", "2019/1937", 5),
        ("Codice del Terzo settore", "decreto.legislativo:2017-07-03;117", 5),
    ],
)
def test_search_golden(search_ready, query, expected_substring, limit):
    rows = m._impl_search(query, limit=limit)
    assert rows, f"nessun risultato per {query!r}"
    ids = " | ".join(r["id"] for r in rows)
    titles = " | ".join((r.get("title") or "") for r in rows)
    assert expected_substring in ids or expected_substring in titles, (
        f"{query!r}: atteso {expected_substring!r} in top-{limit}, got {ids}"
    )


def test_search_nonsense_empty(search_ready):
    rows = m._impl_search("non esiste questo atto XYZ 999", limit=5)
    assert rows == [] or all("error" in r for r in rows)


def test_sentenza_jurisprudence_parametri(search_ready):
    j = m._impl_node("sentenza:2009-0151", view="jurisprudence")
    assert "error" not in j
    assert j.get("impugna"), "impugna assente"
    assert any("40" in (x.get("id") or "") for x in j["impugna"])
    assert (j.get("n_parametri") or 0) >= 1, "parametri_invocati vuoti"


def test_parliament_counts(search_ready):
    p = m._impl_node("senato:40754", view="parliament")
    assert "error" not in p
    assert (p.get("n_emendamenti") or 0) > 1000
    assert p.get("emendamenti_per_legislatura")


def test_pragma_describe(search_ready):
    pr = m.legal_query("PRAGMA table_info(nodes)", limit=2)
    assert pr and "column_name" in pr[0]
    de = m.legal_query("DESCRIBE nodes", limit=2)
    assert de and "column_name" in de[0]
