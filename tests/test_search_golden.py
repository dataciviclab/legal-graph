"""Golden tests — ranking search legal-graph.

Proteggono i casi degli agenti (giro 4). Se il mart search manca,
i test skippino con hint `make run`.
"""

from __future__ import annotations

import pytest

import legal_graph.mcp_server as m


@pytest.fixture(scope="module")
def search_ready():
    m._reset_con()
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


def test_ranking_premia_la_norma_vigente(search_ready):
    """La norma in vigore batte quelle abrogate con lo stesso tema.

    Prima il ranking ordinava `referenced_by` prima della vigenza: i codici
    abrogati (163/2006, 50/2016), citati per decenni, battevano il 36/2023
    vigente. La regressione da proteggere è quella.
    """
    rows = m._impl_search("codice dei contratti pubblici", limit=6)
    ids = [r["id"] for r in rows]
    vigente = next((i for i, r in enumerate(ids) if "2023-03-31;36" in r), None)
    assert vigente is not None, f"36/2023 assente dai risultati: {ids}"
    abrogati = [i for i, r in enumerate(ids) if any(y in r for y in ("2006-04-12;163", "2016-04-18;50"))]
    assert all(vigente < i for i in abrogati), (
        f"norma abrogata sopra quella vigente: {ids}"
    )


def test_search_dedup_documento_atto(search_ready):
    """Lo stesso atto del Senato non esce come documento + atto."""
    rows = m._impl_search("responsabilità amministrativa", limit=20)
    ids = {r["id"] for r in rows}
    doppi = {
        i for i in ids
        if i.startswith("senato:atto:") and i.replace("senato:atto:", "senato:") in ids
    }
    assert not doppi, f"stesso atto due volte in search: {doppi}"


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
    # `norma:*` = atti citati dalla Corte assenti dal corpus (3.4k nodi).
    # I nodi che hanno un URN non esistono più (fix 2026-11): prendine uno vivo.
    rows = m.legal_query("SELECT id FROM nodes WHERE id LIKE 'norma:%' LIMIT 1", limit=1)
    assert rows, "nessun nodo norma:* nel mart"
    t = m._impl_legal_text(rows[0]["id"], max_chars=200)
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
    # DDL 53574: usato al posto di 40754 dopo il fix atto_num (issue #21).
    # 40754 aveva 0 emendamenti reali — il vecchio join fase+legislatura
    # produceva falsi positivi.
    p = m._impl_node("senato:53574", view="parliament")
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


def test_search_filter_materia_su_query_testuale(search_ready):
    """Query a testo libero + filtro: i parametri del boost non devono
    finire nei confronti dei filtri (regressione: risultato vuoto)."""
    rows = m._impl_search("elettroniche", materia="comunicazioni", limit=5)
    assert rows, "query testuale + materia vuota"
    for r in rows:
        assert (r.get("materia") or "").lower() == "comunicazioni"
    assert any("decreto.legislativo:2003-08-01;259" in r["id"] for r in rows)


@pytest.mark.parametrize(
    "query,filtro",
    [
        ("elettroniche", {"stato": "vigente"}),
        ("rifiuti", {"materia": "ambientale"}),
        ("pesca", {"materia": "agricoltura"}),
        ("elettroniche", {"materia": "comunicazioni", "stato": "vigente"}),
    ],
)
def test_search_query_testuale_con_filtro_non_vuota(search_ready, query, filtro):
    """Ogni filtro combinato con una query testuale deve restituire righe
    coerenti col filtro (il boost di ordinamento non li deve annullare)."""
    rows = m._impl_search(query, limit=5, **filtro)
    assert rows, f"{query!r} + {filtro} vuoto"
    for r in rows:
        for chiave, atteso in filtro.items():
            assert (r.get(chiave) or "").lower() == atteso


def test_search_filter_vigenza_fonte(search_ready):
    """Filtro vigenza_fonte: solo la fonte richiesta."""
    rows = m._impl_search("231", vigenza_fonte="esclusione", limit=5)
    assert rows, "vigenza_fonte=esclusione vuoto"
    for r in rows:
        assert (r.get("vigenza_fonte") or "").lower() == "esclusione"


def test_overview_espone_vigenza_fonte(search_ready):
    """La card nodo espone vigenza_fonte (origine di stato)."""
    ov = m._impl_node("urn:nir:stato:decreto.legislativo:2001-06-08;231", view="overview")
    assert "vigenza_fonte" in ov["node"], ov["node"].keys()


# ── Regressioni eval cittadino 2026-10-17 ──────────────────────────


def test_search_articolo_bare_costituzione(search_ready):
    """'articolo N' senza keyword Costituzione → nodo Cost., non D.Lgs a caso."""
    rows = m._impl_search("articolo 21", limit=5)
    assert any("costituzione:art:21" in r["id"] for r in rows), [r["id"] for r in rows]
    rows2 = m._impl_search("articolo 32 salute", limit=5)
    assert any("costituzione:art:32" in r["id"] for r in rows2), [r["id"] for r in rows2]
    rows3 = m._impl_search("articolo 48", limit=5)
    assert any("costituzione:art:48" in r["id"] for r in rows3), [r["id"] for r in rows3]


def test_search_articolo_with_atto_not_cost(search_ready):
    """'articolo N' + tipo atto resta sull'atto, non ruba la Costituzione."""
    rows = m._impl_search("decreto legislativo 231 articolo 3", limit=5)
    assert rows, "query atto+articolo vuota"
    # non deve risolvere SOLO su Costituzione
    assert any("costituzione:art:" not in r["id"] for r in rows)


def test_search_sentenza_num_first(search_ready):
    """'sentenza 316 2004' (numero prima dell'anno, con parole extra)."""
    rows = m._impl_search("sentenza 316 2004 corte costituzionale", limit=5)
    assert any("sentenza:2004-0316" in r["id"] for r in rows), [r["id"] for r in rows]
    # formato classico anno-prima resta protetto (golden esistente)


def test_search_codici_sistematici_fuori_copertura(search_ready):
    """c.c./c.p. → errore strutturato, mai atti a caso. Codici tematici ok."""
    rows = m._impl_search("codice civile articolo 1173", limit=5)
    assert rows and "error" in rows[0], rows
    assert "copertura" in rows[0]["error"].lower()
    rows2 = m._impl_search("codice penale articolo 646", limit=5)
    assert rows2 and "error" in rows2[0], rows2
    # codici NON sistematici coperti dal grafo devono restare cercabili
    rows3 = m._impl_search("codice del terzo settore", limit=5)
    assert any("2017-07-03;117" in (r.get("id") or "") for r in rows3), rows3
    rows4 = m._impl_search("codice della strada", limit=5)
    assert any("1992-04-30;285" in (r.get("id") or "") for r in rows4), rows4


def test_search_tipo_hint_filtra_tutta_la_or(search_ready):
    """Il filtro tipo_hints vale su tutta la catena OR (precedenza SQL)."""
    rows = m._impl_search("legge 180", limit=8)
    assert rows, "legge 180 vuoto"
    assert all((r.get("tipo") or "").upper() == "LEGGE" for r in rows), [
        (r.get("id"), r.get("tipo")) for r in rows
    ]


def test_search_alias_culturali(search_ready):
    """Alias mediatici → ancoraggio numerico (citizen language)."""
    rows = m._impl_search("mani puliti", limit=5)
    assert any("decreto.legislativo:2001-06-08;231" in r["id"] for r in rows), [
        r["id"] for r in rows
    ]
    rows2 = m._impl_search("legge 180 follicelli", limit=5)
    assert any("180:1978" in r["id"] for r in rows2), [r["id"] for r in rows2]
    rows3 = m._impl_search("basaglia", limit=5)
    assert any("180:1978" in r["id"] for r in rows3), [r["id"] for r in rows3]
