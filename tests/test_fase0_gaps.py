"""Legal Graph — Fase 0 gap: modifica AKN, deputati completi, dangling testo_atto."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from legal_graph.paths import resolve_edges_file, resolve_nodes_file, source_exists

ROOT = Path(__file__).resolve().parents[1]
EDGES_SQL = ROOT / "sql" / "mart_legal_edges.sql"
NODES_SQL = ROOT / "sql" / "mart_legal_nodes.sql"
DATASET_YML = ROOT / "dataset.yml"


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect(":memory:")
    nodes_file = resolve_nodes_file()
    edges_file = resolve_edges_file()
    if source_exists(nodes_file):
        c.execute(f"CREATE TABLE nodes AS SELECT * FROM read_parquet('{nodes_file}')")
    if source_exists(edges_file):
        c.execute(f"CREATE TABLE edges AS SELECT * FROM read_parquet('{edges_file}')")
    yield c
    c.close()


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return any(r[0] == name for r in con.execute("SHOW TABLES").fetchall())


class TestSqlContract:
    def test_modifica_da_akn_insertion(self):
        sql = EDGES_SQL.read_text()
        assert "edges_akn_modifica AS (" in sql
        assert "a.rel_type = 'insertion'" in sql
        assert "'modifica' AS relation" in sql
        assert "SELECT * FROM edges_akn_modifica" in sql

    def test_deputati_da_anagrafica(self):
        sql = NODES_SQL.read_text()
        assert "camera_deputati_legislature" in sql
        assert "camera_firmatari" in sql
        yml = DATASET_YML.read_text()
        assert "name: camera_deputati_legislature" in yml

    def test_corpus_ddl_nodes(self):
        assert "nodes_corpus_ddl AS (" in NODES_SQL.read_text()


class TestModificaEdges:
    def test_edges_exist(self, con):
        if not _has_table(con, "edges"):
            pytest.skip("mart assente")
        n = con.execute("SELECT COUNT(*) FROM edges WHERE relation = 'modifica'").fetchone()[0]
        assert n > 0, "Nessun edge modifica"

    def test_no_self_loop(self, con):
        if not _has_table(con, "edges"):
            pytest.skip("mart assente")
        n = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'modifica' AND source_id = target_id
        """).fetchone()[0]
        assert n == 0

    def test_endpoints_in_nodes(self, con):
        if not _has_table(con, "edges") or not _has_table(con, "nodes"):
            pytest.skip("mart assente")
        n = con.execute("""
            SELECT COUNT(*) FROM edges e
            WHERE e.relation = 'modifica'
              AND NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.source_id)
        """).fetchone()[0]
        assert n == 0


class TestDeputati:
    def test_anagrafica_coperta(self, con):
        if not _has_table(con, "nodes"):
            pytest.skip("mart assente")
        n = con.execute("""
            SELECT COUNT(*) FROM nodes WHERE id LIKE 'deputato:%'
        """).fetchone()[0]
        assert n > 10_000, f"Deputati attesi >10k, trovati {n}"


class TestTestoAtto:
    def test_no_dangling(self, con):
        if not _has_table(con, "edges") or not _has_table(con, "nodes"):
            pytest.skip("mart assente")
        n = con.execute("""
            SELECT COUNT(*) FROM edges e
            WHERE e.relation = 'testo_atto'
              AND NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.target_id)
        """).fetchone()[0]
        assert n == 0, f"{n} target testo_atto dangling"
