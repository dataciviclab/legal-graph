"""Legal Graph — contratto edge attua_delega.

Controlla la semantica dell'euristica D.Lgs -> legge delega:
- soglia peso >= 1 (le citazioni recenti valgono 1) e guardia temporale;
- edge deboli conservati con il peso reale (weight < 5);
- sorgenti D.Lgs e bersagli LEGGE, nessun target futuro.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from legal_graph.paths import resolve_edges_file, resolve_nodes_file, source_exists

SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "mart_legal_edges.sql"


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


class TestAttuaDelegaContract:
    def test_sql_uses_low_threshold_and_temporal_guard(self):
        sql = SQL_FILE.read_text()
        block = sql.split("edges_delega AS (", 1)[1].split("-- Euristica titolo", 1)[0]
        assert "e.weight >= 1" in block
        assert "e.weight >= 5" not in block
        assert "e.source_year >= e.target_year - 1" in block

    def test_normativa_types_includes_atti_api(self):
        """Le leggi fetchate via API devono essere target delle euristiche."""
        sql = SQL_FILE.read_text()
        block = sql.split("normativa_types AS (", 1)[1].split("edges_riferimenti AS (", 1)[0]
        assert "support.atti_api.path" in block
        assert "tipo_descrizione" in block

    def test_edges_exist(self, con):
        if not _has_table(con, "edges"):
            pytest.skip("mart edges assente")
        n = con.execute(
            "SELECT COUNT(*) FROM edges WHERE relation = 'attua_delega'"
        ).fetchone()[0]
        assert n >= 1_000, f"Expected >=1000 attua_delega edges, got {n}"

    def test_weak_edges_preserved(self, con):
        if not _has_table(con, "edges"):
            pytest.skip("mart edges assente")
        n = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'attua_delega' AND weight < 5
        """).fetchone()[0]
        assert n > 0, "Edge deboli (weight<5) assenti: soglia peso non abbassata?"

    def test_source_and_target_types(self, con):
        if not _has_table(con, "edges") or not _has_table(con, "nodes"):
            pytest.skip("mart assente")
        bad = con.execute("""
            SELECT COUNT(*) FROM edges e
            JOIN nodes s ON s.id = e.source_id
            JOIN nodes t ON t.id = e.target_id
            WHERE e.relation = 'attua_delega'
              AND (s.tipo <> 'DECRETO LEGISLATIVO' OR t.tipo <> 'LEGGE')
        """).fetchone()[0]
        assert bad == 0, f"{bad} attua_delega con tipi inattesi"

    def test_no_future_edges(self, con):
        if not _has_table(con, "edges"):
            pytest.skip("mart edges assente")
        bad = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'attua_delega'
              AND source_year IS NOT NULL AND target_year IS NOT NULL
              AND source_year < target_year - 1
        """).fetchone()[0]
        assert bad == 0, f"{bad} attua_delega con delega successiva al D.Lgs"

    def test_targets_exist_in_nodes(self, con):
        if not _has_table(con, "edges") or not _has_table(con, "nodes"):
            pytest.skip("mart assente")
        missing = con.execute("""
            SELECT COUNT(*) FROM edges e
            WHERE e.relation = 'attua_delega'
              AND NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.target_id)
        """).fetchone()[0]
        assert missing == 0, f"{missing} attua_delega con target non in nodes"
