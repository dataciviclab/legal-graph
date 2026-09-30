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


def test_search_data_field(search_ready):
    rows = m._impl_search("231", limit=1)
    assert rows and rows[0].get("data"), "campo data assente nei risultati search"


def test_search_hybrid_dlgs(search_ready):
    rows = m._impl_search("D.Lgs 231/2001", limit=3)
    assert any("decreto.legislativo:2001-06-08;231" in r["id"] for r in rows)


def test_search_sentenze_with_source(search_ready):
    rows = m._impl_search("sentenze", source="costituzione", limit=3)
    assert rows, "sentenze+source costituzione vuoto"
    assert all(r["source"] == "costituzione" for r in rows)


def test_search_topic_not_overfiltered(search_ready):
    rows = m._impl_search("responsabilità società reati ambientali", limit=3)
    assert rows, "topic libero over-filtrato a 0"


def test_overview_top_incoming(search_ready):
    ov = m._impl_node("urn:nir:stato:decreto.legislativo:2001-06-08;231", view="overview")
    assert ov.get("top_incoming"), "top_incoming vuoto su 231"
    assert (ov.get("relation_counts") or {}).get("in", {}).get("riferimento", 0) >= 100


def test_groupby_relation_ok(search_ready):
    rows = m.legal_query(
        "SELECT relation, COUNT(*) AS n FROM edges GROUP BY relation ORDER BY n DESC LIMIT 5",
        limit=5,
    )
    assert rows and "relation" in rows[0]


def test_search_norma_nodes(search_ready):
    rows = m._impl_search("legge 194/1978", limit=5)
    assert any("194" in r["id"] or "194" in (r.get("title") or "") for r in rows), (
        f"L.194/1978 non trovata: {[r['id'] for r in rows]}"
    )
    rows2 = m._impl_search("legge 40 2004", limit=5)
    assert any("40" in r["id"] and "2004" in r["id"] for r in rows2) or any(
        "40" in (r.get("title") or "") and "2004" in (r.get("title") or "")
        for r in rows2
    ), f"L.40/2004 non trovata: {[r['id'] for r in rows2]}"


def test_legal_text_sentenza_and_art(search_ready):
    t1 = m._impl_legal_text("sentenza:2009-0151", max_chars=1500)
    # mart texts o error strutturato (se non ancora in compose)
    if "error" in t1:
        assert "mart_legal_texts" in t1["error"] or "non trovato" in t1["error"]
    else:
        assert t1.get("text")
        assert t1.get("via") in {"mart_legal_texts", "github_raw", "locale"}
    t2 = m._impl_legal_text("costituzione:art:3", max_chars=800)
    if "error" not in t2:
        assert "dignità" in (t2.get("text") or "").lower() or "cittadini" in (
            t2.get("text") or ""
        ).lower()


def test_legal_query_texts_massime(search_ready):
    if m._view_exists(m._get_con(), "texts"):
        rows = m.legal_query(
            "SELECT id, kind FROM texts WHERE kind='articolo' LIMIT 3", limit=3
        )
        assert rows and "id" in rows[0]
    if m._view_exists(m._get_con(), "massime"):
        rows = m.legal_query(
            "SELECT sentenza_id, ecli FROM massime LIMIT 2", limit=2
        )
        assert rows and "sentenza_id" in rows[0]


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
