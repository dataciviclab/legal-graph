"""Contratto M0.2: viste di modello (act, relation_type, entity_alias, coverage)
e colonne di provenance additive su nodes/edges.

Protegge il contratto pubblico del core minimo: se una vista o una colonna
sparisce, i consumer che leggono `legal_query`/SQL diretto si rompono.
"""

from __future__ import annotations

import pytest

_ALLOWED_ASSERTION = {"extracted", "resolved", "inferred", "verified", "unverifiable", "unknown"}
_ALLOWED_CONF = {"certified", "strong", "weak", "unknown"}
_ALLOWED_PROV_EVIDENCE = {"quote", "locator", "label", "none"}


@pytest.fixture(scope="module")
def core(con):
    return con


def test_relation_type_vocabolario(core):
    rows = core.execute(
        "SELECT relation, family, in_chain, in_impact FROM relation_type"
    ).fetchall()
    assert len(rows) == 25
    rels = {r[0] for r in rows}
    assert {"riferimento", "modifica", "abroga", "attua_delega", "impugna"} <= rels
    assert all(r[1] in {"norm", "proc", "juris", "identity", "doc", "program"} for r in rows)
    flags = {r[0]: (r[2], r[3]) for r in rows}
    assert flags["modifica"] == (True, False)
    assert flags["riferimento"] == (True, True)
    assert flags["firmatario"] == (False, False)


def test_entity_alias_camera(core):
    row = core.execute(
        "SELECT entity_id FROM entity_alias WHERE alias_id = 'camera:atto:17_100'"
    ).fetchone()
    assert row == ("camera:17:100",)
    assert core.execute("SELECT COUNT(*) FROM entity_alias").fetchone()[0] > 0


def test_act_view(core):
    n = core.execute("SELECT COUNT(*) FROM act").fetchone()[0]
    assert n > 10_000
    cols = {r[0] for r in core.execute("DESCRIBE act").fetchall()}
    assert {
        "act_id", "act_type", "status", "status_authority",
        "status_confidence", "snapshot_id",
    } <= cols


def test_coverage_level_l1_l2(core):
    levels = {r[0] for r in core.execute("SELECT DISTINCT level FROM coverage_level").fetchall()}
    assert {"L1", "L2"} <= levels
    rows = core.execute(
        "SELECT level, n_units, n_with_data, pct FROM coverage_level LIMIT 20"
    ).fetchall()
    assert rows
    for level, n_units, n_with_data, pct in rows:
        assert level in {"L1", "L2"}
        assert n_units >= n_with_data >= 0
        assert pct is None or 0 <= pct <= 100


def test_provenance_columns_nodes(core):
    cols = {r[0] for r in core.execute("DESCRIBE nodes").fetchall()}
    assert {
        "assertion_type", "method", "evidence_locator",
        "conf_identity", "conf_extraction", "conf_semantic", "conf_temporal",
        "review_status", "snapshot_id",
    } <= cols
    assert core.execute(
        "SELECT COUNT(*) FROM nodes WHERE assertion_type IS NULL"
    ).fetchone()[0] == 0
    vals = {r[0] for r in core.execute("SELECT DISTINCT assertion_type FROM nodes").fetchall()}
    assert vals <= _ALLOWED_ASSERTION
    confs = {r[0] for r in core.execute(
        "SELECT DISTINCT conf_identity FROM nodes"
    ).fetchall()}
    assert confs <= _ALLOWED_CONF


def test_provenance_columns_edges(core):
    cols = {r[0] for r in core.execute("DESCRIBE edges").fetchall()}
    assert {
        "prov_evidence_kind", "assertion_type", "method", "evidence_locator",
        "conf_identity", "conf_extraction", "conf_semantic", "conf_temporal",
        "review_status", "snapshot_id",
    } <= cols
    assert core.execute(
        "SELECT COUNT(*) FROM edges WHERE assertion_type IS NULL"
    ).fetchone()[0] == 0
    vals = {r[0] for r in core.execute("SELECT DISTINCT assertion_type FROM edges").fetchall()}
    assert vals <= _ALLOWED_ASSERTION
    ev = {r[0] for r in core.execute(
        "SELECT DISTINCT prov_evidence_kind FROM edges"
    ).fetchall()}
    assert ev <= _ALLOWED_PROV_EVIDENCE
    # `evidence_kind` legacy resta NULL fuori da `riferimento`.
    assert core.execute(
        "SELECT COUNT(*) FROM edges WHERE relation <> 'riferimento' AND evidence_kind IS NOT NULL"
    ).fetchone()[0] == 0
    assert core.execute(
        "SELECT COUNT(*) FROM edges WHERE review_status <> 'unreviewed'"
    ).fetchone()[0] == 0
    assert core.execute(
        "SELECT COUNT(*) FROM edges WHERE snapshot_id IS NOT NULL"
    ).fetchone()[0] == 0
