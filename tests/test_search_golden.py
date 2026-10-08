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


def test_search_n_dot_year(search_ready):
    rows = m._impl_search("n. 231 del 2001", limit=3)
    assert any("decreto.legislativo:2001-06-08;231" in r["id"] for r in rows), (
        f"n. 231 del 2001: {[r['id'] for r in rows]}"
    )


def test_sentenza_alias_no_pad(search_ready):
    j = m._impl_node("sentenza:2009-151", view="overview")
    assert "error" not in j
    assert j["node"]["id"] == "sentenza:2009-0151"
    t = m._impl_legal_text("sentenza:2009-151", max_chars=200)
    assert "error" not in t, t.get("error")
    assert t.get("via") in {"mart_legal_texts", "github_raw", "locale"}


def test_legal_text_norma_hint(search_ready):
    t = m._impl_legal_text("norma:legge:40:2004", max_chars=200)
    # errore onesto o testo se filename IC presente
    assert "error" in t or t.get("text")
    if "error" in t:
        assert "mart_legal_texts" in t["error"] or "norma" in t["error"]


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


# ── Arricchimenti 2026-10: AKN, relatore, firmatari, eiv, filtri ──


_AKN_RELS = {"abroga", "sostituisce", "split", "join", "renumbering"}


def test_chain_modifiche_akn(search_ready):
    """La chain include le modifiche AKN tipizzate (non solo deleghe/leggi)."""
    c = m._impl_node("urn:nir:stato:decreto.legislativo:2024-11-05;173", view="chain")
    assert "error" not in c
    rels = {e["relation"] for e in c.get("chain_forward", [])}
    rels |= {e["relation"] for e in c.get("chain_backward", [])}
    assert rels & _AKN_RELS, f"modifiche AKN assenti dalla chain: {sorted(rels)}"


def test_jurisprudence_relatore(search_ready):
    """Ogni sentenza espone il relatore (edge relatore_sentenza → giudice)."""
    j = m._impl_node("sentenza:1956-0020", view="jurisprudence")
    assert "error" not in j
    rel = j.get("relatore") or []
    assert rel, "relatore assente su sentenza con relatore noto"
    assert rel[0]["id"].startswith("giudice:")


def test_jurisprudence_sentenze_relatore(search_ready):
    """Un giudice espone le sentenze da lui redate (inversa relatore_sentenza)."""
    j = m._impl_node("giudice:Gaetano_Azzariti", view="jurisprudence")
    assert "error" not in j
    sent = j.get("sentenze_relatore") or []
    assert sent, "sentenze_relatore vuoto su giudice con sentenze note"
    assert sent[0]["id"].startswith("sentenza:")


def test_parliament_firmatari(search_ready):
    """Una DDL espone i firmatari (edge incoming firmatario)."""
    p = m._impl_node("senato:54386", view="parliament")
    assert "error" not in p
    assert (p.get("n_firmatari") or 0) >= 1, "n_firmatari vuoto"
    firm = p.get("firmatari") or []
    assert firm and firm[0]["id"].startswith(("senatore:", "deputato:"))
    # weight 2 = primo firmatario (dedup senato: solo weight 1/2)
    assert all(f.get("weight") in (1, 2) for f in firm)


def test_node_eiv_payload(search_ready):
    """eiv (AKN) presente nel payload e tipicamente ≠ data (emanazione)."""
    ov = m._impl_node("urn:nir:stato:decreto.legge:1996-01-24;30", view="overview")
    assert "error" not in ov
    node = ov.get("node") or {}
    assert node.get("eiv"), "eiv assente sul payload nodo"
    assert node["eiv"] != node.get("data"), "eiv identico a data su DL (inaspettato)"


def test_search_filter_materia(search_ready):
    rows = m._impl_search("231", materia="fisco", limit=5)
    assert rows, "filtro materia=fisco su '231' vuoto"
    assert all((r.get("materia") or "").lower() == "fisco" for r in rows)


def test_search_filter_min_score(search_ready):
    rows = m._impl_search("231/2001", min_score=70, limit=3)
    assert rows, "filtro min_score=70 su atto di alta qualità vuoto"
    assert any("decreto.legislativo:2001-06-08;231" in r["id"] for r in rows)


def test_search_filter_collezione(search_ready):
    rows = m._impl_search("costituzione", collezione="Costituzione", limit=5)
    assert rows, "filtro collezione=Costituzione vuoto"
    assert all(r.get("source") == "costituzione" for r in rows)


def test_search_filter_stato_materia_combined(search_ready):
    """Filtri combinati restano coerenti (regressione extra_where)."""
    rows = m._impl_search("231", stato="vigente", materia="fisco", limit=5)
    assert rows, "stato+materia combinati vuoti"
    for r in rows:
        assert (r.get("stato") or "").lower() == "vigente"
        assert (r.get("materia") or "").lower() == "fisco"
