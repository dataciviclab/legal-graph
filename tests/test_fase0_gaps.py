"""Legal Graph — Fase 0 gap: modifica AKN, deputati completi, dangling testo_atto.

I contratti sono verificati sul mart (dati), non sul testo del SQL che lo
produce: le asserzioni su `sql/*.sql` si rompevano a ogni refactor legittimo
senza dire nulla che le query qui sotto non dicano.
"""

from __future__ import annotations


class TestModificaEdges:
    def test_edges_exist(self, con):
        n = con.execute("SELECT COUNT(*) FROM edges WHERE relation = 'modifica'").fetchone()[0]
        assert n > 0, "Nessun edge modifica"

    def test_no_self_loop(self, con):
        n = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'modifica' AND source_id = target_id
        """).fetchone()[0]
        assert n == 0

    def test_endpoints_in_nodes(self, con):
        n = con.execute("""
            SELECT COUNT(*) FROM edges e
            WHERE e.relation = 'modifica'
              AND NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.source_id)
        """).fetchone()[0]
        assert n == 0


class TestDeputati:
    def test_anagrafica_coperta(self, con):
        n = con.execute("""
            SELECT COUNT(*) FROM nodes WHERE id LIKE 'deputato:%'
        """).fetchone()[0]
        assert n > 10_000, f"Deputati attesi >10k, trovati {n}"


class TestTestoAtto:
    def test_no_dangling(self, con):
        n = con.execute("""
            SELECT COUNT(*) FROM edges e
            WHERE e.relation = 'testo_atto'
              AND NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.target_id)
        """).fetchone()[0]
        assert n == 0, f"{n} target testo_atto dangling"
