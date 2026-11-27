"""Test `legal_catena` — i 6 casi di accettazione di docs/SPEC-legal-catena.md.

I casi validano la **forma** del percorso (verso, hop, summary completo) su dati
reali. I conteggi sono espressi come soglie dove un rebuild può spostarli di
qualche unità: il contratto è che un decreto attuativo stia a valle della sua
delega, non che siano esattamente 375.
"""

from __future__ import annotations

import pytest

from legal_graph.mcp.chain import _impl_catena

BASSANINI = "urn:nir:stato:legge:1997-03-15;59"
DL_257_1996 = "urn:nir:stato:decreto.legge:1996-05-10;257"
DDL_771 = "senato:771"
LEGGE_368_1996 = "urn:nir:stato:legge:1996-07-08;368"
DLGS_179_2009 = "urn:nir:stato:decreto.legislativo:2009-12-01;179"


def _summary(res: dict) -> dict[tuple[str, str], int]:
    return {(s["relation"], s["verso"]): s["n"] for s in res["summary"]}


def test_caso1_bassanini_monte():
    """Legge delega: a monte ci sono i DDL, però il summary dice anche i 375
    attuativi a valle — informazione che il percorso a monte non mostra."""
    res = _impl_catena(BASSANINI, direzione="monte")
    assert res["nodo"]["tipo"] == "LEGGE"
    rels = {s["relation"] for s in res["catena"]}
    assert rels <= {"diventa_legge"}, rels
    assert all(s["verso"] == "monte" for s in res["catena"])

    summary = _summary(res)
    assert summary[("attua_delega", "valle")] >= 300
    assert summary.get(("diventa_legge", "monte"), 0) >= 1


def test_caso2_bassanini_valle_tronca_ma_dichiara():
    """375 attuativi: payload limitato, summary completo, troncato dichiarato."""
    res = _impl_catena(BASSANINI, direzione="valle", max_passi=30)
    assert len(res["catena"]) <= 30
    assert all(s["relation"] == "attua_delega" for s in res["catena"])
    assert all(s["verso"] == "valle" for s in res["catena"])
    assert res["troncato"]["si"] is True
    assert _summary(res)[("attua_delega", "valle")] >= 300


def test_caso3_catena_a_due_hop_dal_decreto_legge():
    """DL → DDL di conversione → legge: due hop, due relazioni diverse."""
    res = _impl_catena(DL_257_1996, direzione="valle")
    per_hop = {}
    for step in res["catena"]:
        per_hop.setdefault(step["hop"], set()).add(step["relation"])
    assert 1 in per_hop and "converte_decreto_legge" in per_hop[1]
    assert 2 in per_hop and "diventa_legge" in per_hop[2]
    ids = {s["id"] for s in res["catena"]}
    assert DDL_771 in ids
    assert LEGGE_368_1996 in ids


def test_caso4_modifica_non_e_provenienza(con):
    """d.lgs 179/2009 ha 1.800+ `modifica`: non deve comparire nel percorso."""
    n_mod = con.execute(
        "SELECT count(*) FROM edges WHERE relation = 'modifica' AND source_id = ?",
        [DLGS_179_2009],
    ).fetchone()[0]
    assert n_mod > 1000, "il caso di test ha senso solo con molte modifiche"

    res = _impl_catena(DLGS_179_2009, direzione="entrambe")
    assert "modifica" not in {s["relation"] for s in res["catena"]}
    assert set(_summary(res)) <= {("attua_delega", "monte"), ("attua_delega", "valle"),
                                  ("diventa_legge", "monte"), ("diventa_legge", "valle"),
                                  ("converte_decreto_legge", "monte"),
                                  ("converte_decreto_legge", "valle")}


def test_caso5_nodo_senza_lineage(con):
    """Un nodo senza archi di lineage: lista vuota e nota esplicita."""
    # Anti-join su un set calcolato una volta: il NOT EXISTS correlato con
    # `source_id = n.id OR target_id = n.id` costringeva un scan di 1,5M edge
    # per nodo (5 s). Qui gli edge di lineage si leggono una volta sola.
    row = con.execute(
        """
        WITH lineage AS (
            SELECT source_id AS id FROM edges
            WHERE relation IN ('diventa_legge','converte_decreto_legge','attua_delega')
            UNION
            SELECT target_id FROM edges
            WHERE relation IN ('diventa_legge','converte_decreto_legge','attua_delega')
        )
        SELECT n.id FROM nodes n
        ANTI JOIN lineage l ON l.id = n.id
        WHERE n.id LIKE 'urn:nir:%'
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        pytest.skip("nessun nodo senza lineage nel mart")
    res = _impl_catena(row[0], direzione="entrambe")
    assert res["catena"] == []
    assert res["summary"] == []
    assert any("nessuna relazione di provenienza" in n for n in res["note"])


def test_caso6_id_ambiguo_non_sceglie():
    """Un id parziale ambiguo restituisce i candidati, non un atto a caso."""
    res = _impl_catena("1996", direzione="monte")
    assert res.get("ambiguous") is True
    assert res.get("candidates")
    assert "catena" not in res


def test_direzione_non_valida():
    res = _impl_catena(BASSANINI, direzione="su")
    assert "error" in res


def test_copertura_e_as_of_presenti():
    """`copertura` è un campo della risposta, non un tool da ricordare."""
    res = _impl_catena(BASSANINI, direzione="monte")
    cov = {c["relation"]: c for c in res["copertura"]}
    assert "diventa_legge" in cov
    assert cov["diventa_legge"]["pct"] == pytest.approx(81.5, abs=1.0)
    assert "build_at" in res


def test_mart_legacy_senza_conf_evidence(monkeypatch):
    """Su un mart pre-2026-11-19 `conf_evidence` non esiste: NULL, non crash.

    È il criterio di accettazione di legal-graph#85 applicato a questo tool:
    la compatibilità passa da `_has_col`, non da un caso limite.
    """
    import duckdb

    from legal_graph.mcp import db as _db

    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE nodes (
            id VARCHAR, tipo VARCHAR, title VARCHAR, data VARCHAR, anno INTEGER,
            source VARCHAR, length_chars INTEGER, length_words INTEGER,
            celex VARCHAR, collezione VARCHAR, source_filename VARCHAR,
            stato VARCHAR, materia VARCHAR, qualita_score INTEGER,
            sunsetting_score INTEGER
        )
        """
    )
    con.execute(
        """
        CREATE TABLE edges (
            relation VARCHAR, source_id VARCHAR, target_id VARCHAR,
            weight INTEGER, method VARCHAR, prov_evidence_kind VARCHAR,
            evidence_locator VARCHAR
        )
        """
    )
    con.execute(
        "INSERT INTO nodes (id, tipo, title, anno) VALUES "
        "('a', 'LEGGE', 'Delega X', 1997), ('b', 'DDL', 'Delega X (DDL)', 1996)"
    )
    con.execute(
        "INSERT INTO edges VALUES ('diventa_legge', 'b', 'a', 1, 'join', 'quote', 'art_1')"
    )

    monkeypatch.setattr(_db, "_get_con", lambda: con)
    res = _impl_catena("a", direzione="monte")
    assert [s["id"] for s in res["catena"]] == ["b"]
    assert res["catena"][0]["evidence"]["conf"] is None
    assert res["catena"][0]["affidabile"] is True


def test_step_senza_evidenza_e_marcato(con):
    """Un hop con prov_evidence_kind='none' deve arrivare con affidabile=false."""
    row = con.execute(
        """
        SELECT source_id FROM edges
        WHERE relation = 'diventa_legge' AND prov_evidence_kind = 'none' LIMIT 1
        """
    ).fetchone()
    if row is None:
        pytest.skip("nessun edge diventa_legge senza evidenza")
    res = _impl_catena(row[0], direzione="valle")
    senza = [s for s in res["catena"] if s["evidence"]["kind"] == "none"]
    assert senza, res["catena"]
    assert all(s["affidabile"] is False for s in senza)
    assert any("senza citazione" in n for n in res["note"])
