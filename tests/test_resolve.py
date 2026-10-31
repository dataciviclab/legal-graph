"""Resolver id — il parziale ambiguo non sceglie (regressione 2026-10).

Prima ogni call site faceva `WHERE id LIKE '%x%' ORDER BY length(id) LIMIT 1`:
su 300 id troncati, 253 restituivano un atto diverso senza errore
(es. `senato:5907` → `senato:59077`). Questi test proteggono il contratto:
exact → alias → parziale **solo se unico**.
"""
from __future__ import annotations

import duckdb
import pytest

from legal_graph.paths import resolve_nodes_file, source_exists
from legal_graph.resolve import resolve_row


@pytest.fixture(scope="module")
def con():
    path = resolve_nodes_file()
    if not source_exists(path):
        pytest.skip("mart nodes assente — esegui make run")
    c = duckdb.connect(":memory:")
    c.execute(
        f"CREATE TABLE nodes AS "
        f"SELECT id, source, title, anno FROM read_parquet('{path}')"
    )
    yield c
    c.close()


def _resolve(con, node_id):
    return resolve_row(
        con, source="nodes", cols="id, source, title, anno", node_id=node_id
    )


class TestExact:
    def test_exact_vince(self, con):
        r = _resolve(con, "senato:59070")
        assert r.row is not None
        assert r.row[0] == "senato:59070"
        assert r.match == "exact"

    def test_assente(self, con):
        r = _resolve(con, "urn:nir:stato:non:esiste;0")
        assert r.row is None
        assert not r.candidates
        assert r.match == "none"

    def test_alias_sentenza_zero_pad(self, con):
        r = _resolve(con, "sentenza:2009-151")
        assert r.row is not None
        assert r.row[0] == "sentenza:2009-0151"
        assert r.match == "alias"


class TestParziale:
    def test_ambiguo_non_sceglie(self, con):
        """`senato:5907` è prefisso di 59070, 59077, 59079…: nessuna scelta."""
        r = _resolve(con, "senato:5907")
        assert r.row is None
        assert r.match == "ambiguous"
        assert len(r.candidates) > 1
        assert all("senato:5907" in c for c in r.candidates)

    def test_parziale_unico_risolve_ma_si_dichiara(self, con):
        """Un parziale con un solo candidato si risolve, marcato come `partial`."""
        r = _resolve(con, "decreto.legislativo:2017-07-03;117")
        assert r.row is not None
        assert r.match == "partial"
        assert r.row[0].endswith(";117")

    def test_ambiguita_non_restituisce_riga(self, con):
        """Invariante: riga valorizzata ⇒ id esatto o unico candidato."""
        ids = [
            row[0]
            for row in con.execute(
                "SELECT id FROM nodes WHERE length(id) > 20 LIMIT 150"
            ).fetchall()
        ]
        for nid in ids:
            r = _resolve(con, nid[: len(nid) - 1])
            if r.row is not None:
                assert r.match in {"exact", "alias", "partial"}
            else:
                assert r.match in {"ambiguous", "none"}
