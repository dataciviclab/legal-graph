"""Contratto M1: identità Camera, alias, vocabolario canonico, nuovi tool.

Protegge i cambi pubblici di M1:
- `camera:atto:{leg}_{id}` non è più un nodo; resta alias in `entity_alias`;
- `relation_type` è una tabella con `canonical_relation` (24→15) e i flag
  letti da metrics/chain;
- `legal_neighbors` e `legal_coverage` rispondono senza liste hardcoded.
"""

from __future__ import annotations

import duckdb
import pytest

from legal_graph.mcp import db
from legal_graph.mcp.node_views import _chain_rels, _impl_node
from legal_graph.mcp.reports import _impl_coverage, _impl_neighbors

_CANONICAL = {
    "cita", "modifica", "abroga", "riordino", "attua", "conversione",
    "iter_costituzionale", "impugna", "parametro", "relatore", "firmatario",
    "emendamento", "voto", "documento", "identita",
}
# Relazioni che il vecchio elenco hardcoded di mart_legal_node_metrics
# contava come impatto. M1 non cambia i numeri: la tabella deve dire lo stesso.
_IMPACT = {
    "riferimento", "cita_costituzione", "impugna", "invoca_parametro",
    "diventa_legge", "evoca_parametro", "abroga", "attua_delega",
}
_CHAIN_VIEW = {
    "diventa_legge", "attua_delega", "cita_costituzione",
    "converte_decreto_legge", "abroga", "sostituisce", "split", "join",
    "renumbering",
}


def test_camera_atto_nodes_removed(con):
    n = con.execute(
        "SELECT COUNT(*) FROM nodes WHERE id LIKE 'camera:atto:%'"
    ).fetchone()[0]
    assert n == 0


def test_entity_alias_camera_survives_node_removal(con):
    row = con.execute(
        "SELECT entity_id FROM entity_alias WHERE alias_id = 'camera:atto:17_100'"
    ).fetchone()
    assert row == ("camera:17:100",)
    n = con.execute(
        "SELECT COUNT(*) FROM entity_alias WHERE alias_id LIKE 'camera:atto:%'"
    ).fetchone()[0]
    assert n > 1_000
    # ogni alias punta a un nodo reale
    dangling = con.execute("""
        SELECT COUNT(*) FROM entity_alias a
        LEFT JOIN nodes n ON n.id = a.entity_id
        WHERE n.id IS NULL
    """).fetchone()[0]
    assert dangling == 0


def test_relation_type_canonical_24_to_15(con):
    rows = con.execute(
        "SELECT relation, canonical_relation FROM relation_type"
    ).fetchall()
    assert len(rows) == 24
    assert {r[1] for r in rows} == _CANONICAL
    assert all(r[1] for r in rows)


def test_relation_type_maps_every_edge(con):
    n = con.execute("""
        SELECT COUNT(*) FROM edges e
        LEFT JOIN relation_type rt ON rt.relation = e.relation
        WHERE rt.relation IS NULL
    """).fetchone()[0]
    assert n == 0


def test_relation_type_impact_parity(con):
    rels = {
        r[0]
        for r in con.execute(
            "SELECT relation FROM relation_type WHERE in_impact"
        ).fetchall()
    }
    assert rels == _IMPACT


def test_chain_view_rels_from_vocabulary(con):
    assert set(_chain_rels(con)) == _CHAIN_VIEW
    # il vocabolario esiste e la vista chain non include le citazioni massive
    assert "riferimento" not in _chain_rels(con)


def test_camera_relatore_edges_target_canonical_act(con):
    n_bad = con.execute("""
        SELECT COUNT(*) FROM edges
        WHERE relation = 'relatore' AND target_id LIKE 'camera:atto:%'
    """).fetchone()[0]
    assert n_bad == 0
    n = con.execute("""
        SELECT COUNT(*) FROM edges
        WHERE relation = 'relatore' AND source_id LIKE 'deputato:%'
    """).fetchone()[0]
    assert n > 500
    dangling = con.execute("""
        SELECT COUNT(*) FROM edges e
        LEFT JOIN nodes n ON n.id = e.target_id
        WHERE e.relation = 'relatore' AND e.source_id LIKE 'deputato:%'
          AND n.id IS NULL
    """).fetchone()[0]
    assert dangling == 0


def test_legal_node_resolves_old_camera_id(con):
    out = _impl_node("camera:atto:17_100")
    assert out["node"]["id"] == "camera:17:100"
    assert out.get("matched_by") == "alias"


def test_legal_neighbors_in_and_canonical_filter(con):
    out = _impl_neighbors("camera:atto:17_100", direction="in", limit=5)
    assert out["node_id"] == "camera:17:100"
    assert out.get("matched_by") == "alias"
    assert out["returned"] > 0
    assert all(n["direction"] == "in" for n in out["neighbors"])
    assert all("evidence_locator" in n for n in out["neighbors"])
    assert all(n["canonical_relation"] for n in out["neighbors"])

    filt = _impl_neighbors(
        "camera:17:100", direction="in",
        canonical_relation="relatore", limit=5,
    )
    assert filt["returned"] > 0
    assert {n["canonical_relation"] for n in filt["neighbors"]} == {"relatore"}


def test_legal_neighbors_direction_validation(con):
    out = _impl_neighbors("camera:17:100", direction="sideways")
    assert "error" in out


def test_legal_coverage_relations_and_levels(con):
    out = _impl_coverage(limit=5)
    assert out["totals"]["edges"] > 0
    assert out["totals"]["nodes"] > 0
    assert out["relations"]
    assert all(r["canonical_relation"] in _CANONICAL for r in out["relations"])
    assert {r["level"] for r in out["levels"]} == {"L1", "L2"}
    assert out["families"]


def test_legal_coverage_relation_filter(con):
    out = _impl_coverage(relation="riferimento")
    assert [r["relation"] for r in out["relations"]] == ["riferimento"]


def test_conversione_view(con):
    rows = dict(
        con.execute(
            "SELECT conversion_type, COUNT(*) FROM conversione GROUP BY 1"
        ).fetchall()
    )
    assert rows.get("ddl_to_law", 0) > 1_000
    assert rows.get("dl_to_law", 0) > 500
    # la vista non inventa conversioni: solo le due relazioni canoniche
    rels = {
        r[0] for r in con.execute("SELECT DISTINCT relation FROM conversione").fetchall()
    }
    assert rels == {"converte_decreto_legge", "diventa_legge"}


def test_relation_type_fallback_matches_mart(con):
    """Il fallback legacy e la tabella non devono divergere sui flag M1.

    Esegue davvero il fallback in una connessione in-memory (non referenzia
    tabelle) e confronta le righe con il mart: una divergenza futura tra i due
    contratti diventa un test rosso.
    """
    cols = (
        "relation, family, in_chain, in_impact, in_chain_view, "
        "canonical_relation, description"
    )
    fallback_con = duckdb.connect()
    try:
        fallback_con.execute(db._RELATION_TYPE_FALLBACK_SQL)
        fallback = fallback_con.execute(
            f"SELECT {cols} FROM relation_type ORDER BY relation"
        ).fetchall()
    finally:
        fallback_con.close()
    mart = con.execute(f"SELECT {cols} FROM relation_type ORDER BY relation").fetchall()
    assert fallback == mart
    assert len(mart) == 24


def test_refresh_views_recreates_alias_and_vocabulary(con):
    """`refresh_views` deve ricreare anche le view opzionali M1.

    Regressione vista in review: dopo un rebuild su una connessione già
    avviata, `entity_alias_src` non veniva ricreata e la view ricadeva sul
    fallback legacy (0 alias Camera sul nuovo mart).
    """
    fresh = db._new_con()
    try:
        db._bootstrap_views(fresh)
        for vista in ("entity_alias_src", "relation_type_src"):
            assert db._view_exists(fresh, vista)
            fresh.execute(f"DROP VIEW {vista}")
        db.refresh_views(fresh)
        assert db._view_exists(fresh, "entity_alias_src")
        assert db._view_exists(fresh, "relation_type_src")
        n = fresh.execute(
            "SELECT COUNT(*) FROM entity_alias WHERE alias_id LIKE 'camera:atto:%'"
        ).fetchone()[0]
        assert n > 1_000
    finally:
        fresh.close()


@pytest.mark.parametrize("relation", sorted(_IMPACT))
def test_impact_relations_have_edges(con, relation):
    n = con.execute(
        "SELECT COUNT(*) FROM edges WHERE relation = ?", [relation]
    ).fetchone()[0]
    assert n > 0
