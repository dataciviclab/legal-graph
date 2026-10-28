"""E2E di contratto sull'MCP — catena, 5 tool, parametri, casi limite.

Copre la superficie che i golden test non toccano: integrità della catena
(nodes ↔ search_keys ↔ metrics, vocabolario `materia`, colonne richieste),
matrice di `legal_search` (intent × filtri × limiti), `legal_query`
(comandi consentiti/rifiutati), `legal_node` (viste ed errori),
`legal_insights` (tutti i report), coerenza cross-tool e campi di payload
documentati.

Nasce da una passata E2E manuale che ha trovato: filtri che annullavano le
query testuali (#32), risultati vuoti spuri in concorrenza (#33), 171 atti
fuori collezione senza `materia` (italia-corpus #83 + fix in
`sql/mart_legal_nodes.sql`). Se il mart manca, i test skippano con hint
`make run`.
"""

from __future__ import annotations

import pytest

import legal_graph.mcp_server as m

MATERIE = {
    "affari-esteri",
    "agricoltura",
    "ambientale",
    "altro",
    "comunicazioni",
    "cultura",
    "difesa",
    "enti-locali",
    "fisco",
    "giustizia",
    "industria",
    "istruzione",
    "lavoro",
    "ordinamento-pa",
    "ordinamento-stato",
    "ordine-pubblico",
    "previdenza",
    "recepimento-ue",
    "sanita",
    "servizi-pubblici",
    "sport",
    "strutturale",
    "terzo-settore",
    "trasporti",
    "turismo",
}

CODICE_COMUNICAZIONI = "urn:nir:stato:decreto.legislativo:2003-08-01;259"


@pytest.fixture(scope="module")
def grafo():
    m._reset_con()
    con = m._get_con()
    if not m._has_mart_search(con):
        pytest.skip("mart assente — esegui `make run`")
    return con


# --- A. Integrità della catena -------------------------------------------------


def test_chain_tabelle_popolate(grafo):
    for tabella in ("nodes", "search_keys", "metrics"):
        n = grafo.execute(f"SELECT COUNT(*) FROM {tabella}").fetchone()[0]
        assert n > 10000, f"{tabella}: {n} righe"


def test_chain_ogni_nodo_normativa_ha_search_key(grafo):
    orfane = grafo.execute(
        """SELECT COUNT(*) FROM (SELECT DISTINCT id FROM nodes WHERE source='normativa') n
           WHERE NOT EXISTS (SELECT 1 FROM search_keys k WHERE k.id = n.id)"""
    ).fetchone()[0]
    assert orfane == 0, f"{orfane} nodi normativa senza chiave di ricerca"


def test_chain_vocabolario_materia(grafo):
    materie = {
        r[0] for r in grafo.execute("SELECT DISTINCT materia FROM nodes WHERE materia IS NOT NULL").fetchall()
    }
    assert materie == MATERIE, f"inattese: {materie - MATERIE} | mancanti: {MATERIE - materie}"


def test_chain_normativa_sempre_classificata(grafo):
    """Nessun nodo normativa senza `materia`: altrimenti è invisibile al filtro.

    Regressione: gli atti fuori collezione (`atti_api`) erano costruiti con
    `NULL::VARCHAR AS materia` → 171 atti (incluse L. 241/1990 e L. 194/1978)
    non comparivano in nessun filtro `materia`.
    """
    senza = grafo.execute(
        "SELECT COUNT(*) FROM nodes WHERE source='normativa' AND (materia IS NULL OR materia='')"
    ).fetchone()[0]
    assert senza == 0, f"{senza} nodi normativa senza materia"


@pytest.mark.parametrize("colonna", ["id", "tipo", "title", "source", "stato"])
def test_chain_colonne_obbligatorie_normativa(grafo, colonna):
    vuote = grafo.execute(
        f"SELECT COUNT(*) FROM nodes WHERE source='normativa' AND ({colonna} IS NULL OR {colonna}='')"
    ).fetchone()[0]
    assert vuote == 0, f"{colonna}: {vuote} righe vuote"


# --- B. legal_query -----------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) AS n FROM nodes",
        "WITH x AS (SELECT 1 AS a) SELECT * FROM x",
        "SELECT materia, COUNT(*) AS n FROM nodes GROUP BY materia",
        "SELECT stato, COUNT(*) AS n FROM nodes GROUP BY stato",
        "SELECT id FROM nodes ORDER BY id LIMIT 3",
        "SELECT COUNT(*) AS n FROM edges",
        "SELECT COUNT(*) AS n FROM nodes n JOIN metrics m ON m.id = n.id",
        "SELECT COUNT(*) AS n FROM nodes WHERE id IN ('urn:nir:stato:legge:1978-05-22;194')",
        "DESCRIBE nodes",
        "SHOW TABLES",
        "PRAGMA table_info('nodes')",
    ],
)
def test_query_comandi_consentiti(grafo, sql):
    out = m._impl_query(sql, limit=5)
    assert out and not out[0].get("error"), f"{sql} → {out}"


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO nodes VALUES (1)",
        "DROP TABLE nodes",
        "SELECT FROM WHERE",
        "SELECT pippo FROM nodes",
        "SELECT COUNT(*) FROM tabella_finta",
    ],
)
def test_query_comandi_rifiutati(grafo, sql):
    out = m._impl_query(sql, limit=5)
    assert out and (out[0].get("error") or out[0].get("note")), f"{sql} non gestita: {out}"


# --- C. legal_search: intent e filtri ----------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "231",
        "231/2001",
        "n. 231 del 2001",
        "1978",
        "urn:nir:stato:legge:1978-05-22;194",
        "ECLI:IT:COST:2009:151",
        "art. 3 della Costituzione",
        "direttiva UE 2019/1937",
        "rifiuti urbani",
        '"codice del terzo settore"',
    ],
)
def test_search_intent(grafo, query):
    rows = m._impl_search(query, limit=5)
    assert rows and not rows[0].get("error"), f"{query!r} → {rows}"


@pytest.mark.parametrize(
    "query,filtro",
    [
        ("rifiuti", {"materia": "ambientale"}),
        ("rifiuti", {"stato": "vigente"}),
        ("rifiuti", {"tipo": "DECRETO LEGISLATIVO"}),
        ("rifiuti", {"anno_min": 2010}),
        ("rifiuti", {"anno_max": 1990}),
        ("costituzione", {"source": "costituzione"}),
        ("codice", {"collezione": "Codici"}),
        ("rifiuti", {"vigenza_fonte": "normattiva"}),
        ("rifiuti", {"min_score": 70}),
    ],
)
def test_search_filtri_singoli(grafo, query, filtro):
    rows = m._impl_search(query, limit=5, **filtro)
    assert rows and not rows[0].get("error"), f"{query!r} + {filtro} → {rows}"


def test_search_filtri_combinati(grafo):
    rows = m._impl_search("rifiuti", materia="ambientale", stato="vigente", anno_min=2000, limit=5)
    assert rows, "filtri combinati vuoti"
    for r in rows:
        assert r["materia"] == "ambientale"
        assert r["stato"] == "vigente"


@pytest.mark.parametrize(
    "chiave,valore",
    [
        ("materia", "ambientale"),
        ("stato", "vigente"),
        ("tipo", "DECRETO LEGISLATIVO"),
        ("vigenza_fonte", "normattiva"),
    ],
)
def test_search_filtro_coerente_sui_risultati(grafo, chiave, valore):
    rows = m._impl_search("rifiuti", limit=10, **{chiave: valore})
    assert rows
    assert all((r.get(chiave) or "").lower() == valore.lower() for r in rows), rows


def test_search_filtro_su_query_testuale_multipla(grafo):
    """Regressione #32: il boost di ordinamento non deve annullare i filtri."""
    rows = m._impl_search("elettroniche", materia="comunicazioni", limit=5)
    assert rows, "query testuale + materia vuota"
    assert all(r["materia"] == "comunicazioni" for r in rows)
    assert any(CODICE_COMUNICAZIONI in r["id"] for r in rows)


@pytest.mark.parametrize("limit", [1, 0, 100000])
def test_search_limit_clampato(grafo, limit):
    rows = m._impl_search("rifiuti", limit=limit)
    assert rows and len(rows) <= 200, f"limit={limit} → {len(rows)} righe"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": ""},
        {"query": "qwerty"},
        {"query": "rifiuti", "materia": "materia_finta"},
        {"query": "rifiuti", "anno_min": 2020, "anno_max": 1990},
    ],
)
def test_search_casi_limite_gestiti(grafo, kwargs):
    rows = m._impl_search(limit=5, **kwargs)
    assert not rows or rows[0].get("error"), f"{kwargs} → {rows}"


# --- D. legal_node ------------------------------------------------------------


@pytest.mark.parametrize("view", ["overview", "chain", "jurisprudence", "parliament"])
def test_node_viste(grafo, view):
    out = m._impl_node(CODICE_COMUNICAZIONI, view=view, depth=2)
    assert isinstance(out, dict) and not out.get("error"), f"{view} → {out}"


def test_node_id_parziale(grafo):
    out = m._impl_node("decreto.legislativo:2017-07-03;117")
    assert out.get("node", {}).get("id", "").endswith("2017-07-03;117")


@pytest.mark.parametrize(
    "nid,view",
    [
        ("urn:nir:stato:legge:1800-01-01;999999", "overview"),
        (CODICE_COMUNICAZIONI, "vista_inventata"),
    ],
)
def test_node_errori_gestiti(grafo, nid, view):
    out = m._impl_node(nid, view=view)
    assert isinstance(out, dict) and out.get("error"), f"{nid}/{view} → {out}"


def test_node_depth_non_rompe(grafo):
    for depth in (0, 1, 100):
        out = m._impl_node(CODICE_COMUNICAZIONI, view="chain", depth=depth)
        assert not out.get("error"), f"depth={depth} → {out}"


# --- E. legal_text ------------------------------------------------------------


def test_text_normativa(grafo):
    out = m._impl_legal_text(CODICE_COMUNICAZIONI, max_chars=300)
    assert out.get("text"), f"nessun testo: {str(out)[:200]}"
    assert out.get("chars", 0) > 0


def test_text_costituzione(grafo):
    out = m._impl_legal_text("costituzione:art:3", max_chars=300)
    assert out.get("text"), f"nessun testo: {str(out)[:200]}"


def test_text_nodo_inesistente(grafo):
    out = m._impl_legal_text("urn:nir:stato:legge:1800-01-01;999999")
    assert out.get("error")


# --- F. legal_insights --------------------------------------------------------


@pytest.mark.parametrize("report", ["summary", "critical", "obsolete", "complex", "dormant"])
def test_insights_report(grafo, report):
    out = m._impl_insights(report)
    assert isinstance(out, dict) and not out.get("error"), f"{report} → {str(out)[:150]}"


# --- G. Coerenza cross-tool ---------------------------------------------------


def test_coerenza_search_node_materia(grafo):
    rows = m._impl_search("rifiuti", limit=5, source="normativa")
    assert len(rows) >= 3
    for r in rows[:3]:
        nodo = m._impl_node(r["id"])
        assert nodo.get("node", {}).get("materia") == r["materia"], f"disallineato su {r['id']}"


def test_coerenza_search_esegue_in_query(grafo):
    """Un id trovato dalla search deve esistere anche per SQL."""
    rows = m._impl_search("231/2001", limit=1)
    nid = rows[0]["id"]
    trovato = m._impl_query(f"SELECT COUNT(*) AS n FROM nodes WHERE id = '{nid}'", limit=1)
    assert trovato[0]["n"] == 1, f"{nid} non trovato via SQL"


# --- H. Payload: campi documentati -------------------------------------------


def test_payload_search_campi(grafo):
    rows = m._impl_search("231/2001", limit=1)
    assert {"id", "tipo", "title", "data", "anno", "source", "stato", "materia"} <= set(rows[0])


def test_payload_node_campi(grafo):
    nodo = m._impl_node(CODICE_COMUNICAZIONI).get("node", {})
    attesi = {
        "id",
        "tipo",
        "title",
        "data",
        "anno",
        "source",
        "stato",
        "materia",
        "qualita_score",
        "sunsetting_score",
        "vigenza_fonte",
    }
    assert attesi <= set(nodo), f"mancanti: {attesi - set(nodo)}"
