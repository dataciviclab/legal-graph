"""Legal Graph — contratto edge attua_delega.

Controlla la semantica dell'euristica D.Lgs -> legge delega sui dati:
- soglia peso >= 1 (le citazioni recenti valgono 1) e guardia temporale;
- edge deboli conservati con il peso reale (weight < 5);
- sorgenti D.Lgs e bersagli LEGGE, nessun target futuro.

Le soglie nel SQL non sono asserite sul testo del file: `test_weak_edges_preserved`
e `test_no_future_edges` verificano l'effetto, che è ciò che conta.
"""

from __future__ import annotations

import legal_graph.mcp_server as m


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

    def test_target_atti_api(self, con):
        """Gli atti fuori collezione restano bersaglio dell'euristica.

        `support.atti_api` in `normativa_types` serve a questo: senza, gli edge
        `attua_delega` verso i 171 atti Normattiva API (181 oggi) spariscono.
        La soglia 1.000 di `test_edges_exist` non se ne accorgerebbe (2.683 →
        2.502), quindi serve un'asserzione sul sottoinsieme.
        """
        n = con.execute("""
            SELECT COUNT(*) FROM edges e JOIN nodes n ON n.id = e.target_id
            WHERE e.relation = 'attua_delega' AND n.vigenza_fonte = 'atti_api'
        """).fetchone()[0]
        assert n > 50, f"attua_delega verso atti_api: {n} (attesi >50)"

    def test_provenance_distingue_titolo_da_citazione(self, con):
        """`method`/`assertion_type`/`prov_evidence_kind` sono coerenti.

        Delega dal titolo: estratta, con locator (il titolo nomina la legge).
        Delega da citazione: inferita, con quote (lo snippet citante).
        """
        rows = con.execute("""
            SELECT method, assertion_type, prov_evidence_kind, COUNT(*)
            FROM edges WHERE relation = 'attua_delega'
            GROUP BY 1, 2, 3
        """).fetchall()
        assert rows, "nessun attua_delega"
        for method, assertion, kind, n in rows:
            assert method in ("title", "regex"), (method, n)
            if method == "title":
                assert assertion == "extracted", (assertion, n)
                assert kind == "locator", (kind, n)
            else:
                assert assertion == "inferred", (assertion, n)
                assert kind in ("quote", "none"), (kind, n)

    def test_citation_evidence_non_e_il_titolo_del_bersaglio(self, con):
        """Regressione: l'evidenza era `n2.oggetto` (titolo della legge).

        Ora è lo snippet citante. Se qualcuno la riporta al titolo del target,
        il consumer non può più verificare la citazione.
        """
        bad = con.execute("""
            SELECT COUNT(*) FROM edges e
            JOIN nodes t ON t.id = e.target_id
            WHERE e.relation = 'attua_delega'
              AND e.method = 'regex'
              AND e.evidence IS NOT NULL
              AND TRIM(e.evidence) = TRIM(COALESCE(t.title, ''))
        """).fetchone()[0]
        assert bad == 0, f"{bad} attua_delega con evidenza = titolo del bersaglio"

    def test_chain_espone_la_provenienza(self, con):
        """`view=chain` porta method/assertion/evidence e mette davanti la
        delega letta dal titolo (36/2023 → L. 78/2022)."""
        node = m.legal_node(
            "urn:nir:stato:decreto.legislativo:2023-03-31;36",
            view="chain",
            depth=1,
        )
        forward = node.get("chain_forward") or []
        assert forward, "chain vuota per il Codice dei contratti pubblici"
        assert all("method" in e and "assertion_type" in e for e in forward)
        deleghe = [e for e in forward if e["relation"] == "attua_delega"]
        assert deleghe, "nessun attua_delega nella chain del 36/2023"
        assert deleghe[0]["method"] == "title", deleghe[0]
        assert "2022-06-21;78" in deleghe[0]["target_id"], deleghe[0]
