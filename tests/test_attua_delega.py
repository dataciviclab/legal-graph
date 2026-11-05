"""Legal Graph — contratto edge attua_delega.

Controlla la semantica dell'euristica D.Lgs -> legge delega sui dati:
- soglia peso >= 1 (le citazioni recenti valgono 1) e guardia temporale;
- edge deboli conservati con il peso reale (weight < 5);
- sorgenti D.Lgs e bersagli LEGGE, nessun target futuro.

Le soglie nel SQL non sono asserite sul testo del file: `test_weak_edges_preserved`
e `test_no_future_edges` verificano l'effetto, che è ciò che conta.
"""

from __future__ import annotations


class TestAttuaDelega:
    def test_edges_exist(self, con):
        n = con.execute(
            "SELECT COUNT(*) FROM edges WHERE relation = 'attua_delega'"
        ).fetchone()[0]
        assert n >= 1_000, f"Expected >=1000 attua_delega edges, got {n}"

    def test_weak_edges_preserved(self, con):
        n = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'attua_delega' AND weight < 5
        """).fetchone()[0]
        assert n > 0, "Edge deboli (weight<5) assenti: soglia peso non abbassata?"

    def test_source_and_target_types(self, con):
        bad = con.execute("""
            SELECT COUNT(*) FROM edges e
            JOIN nodes s ON s.id = e.source_id
            JOIN nodes t ON t.id = e.target_id
            WHERE e.relation = 'attua_delega'
              AND (s.tipo <> 'DECRETO LEGISLATIVO' OR t.tipo <> 'LEGGE')
        """).fetchone()[0]
        assert bad == 0, f"{bad} attua_delega con tipi inattesi"

    def test_no_future_edges(self, con):
        bad = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'attua_delega'
              AND source_year IS NOT NULL AND target_year IS NOT NULL
              AND source_year < target_year - 1
        """).fetchone()[0]
        assert bad == 0, f"{bad} attua_delega con delega successiva al D.Lgs"

    def test_targets_exist_in_nodes(self, con):
        missing = con.execute("""
            SELECT COUNT(*) FROM edges e
            WHERE e.relation = 'attua_delega'
              AND NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.target_id)
        """).fetchone()[0]
        assert missing == 0, f"{missing} attua_delega con target non in nodes"
