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
                assert kind in ("quote", "none", "locator"), (kind, n)

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

    def test_title_locator_e_l_articolo_del_titolo(self, con):
        """La delega letta dal titolo porta l'articolo che il titolo nomina.

        `evidence` è il titolo del D.Lgs: se il locator non ne deriva, il
        consumer non sa *quale articolo* della legge delega è invocato.
        """
        rows = con.execute("""
            SELECT COUNT(*),
                   COUNT(evidence_locator),
                   COUNT(*) FILTER (WHERE evidence_locator IS NOT NULL
                                      AND evidence_locator NOT LIKE 'art_%')
            FROM edges
            WHERE relation = 'attua_delega' AND method = 'title'
        """).fetchone()
        total, with_loc, bad = rows
        assert total > 0
        assert bad == 0, f"{bad} locator titolo fuori formato"
        assert with_loc / total > 0.99, f"locator dal titolo: {with_loc}/{total}"

    def test_regex_locator_viene_dalla_citazione(self, con):
        """L'articolo del ramo regex è quello della citazione AKN sulla stessa
        coppia, non un valore inventato dal CTE della delega."""
        total, with_loc = con.execute("""
            SELECT COUNT(*), COUNT(evidence_locator) FROM edges
            WHERE relation = 'attua_delega' AND method = 'regex'
        """).fetchone()
        assert with_loc > total * 0.5, f"locator sulle deleghe da citazione: {with_loc}/{total}"

        orfani = con.execute("""
            SELECT COUNT(*) FROM edges a
            WHERE a.relation = 'attua_delega'
              AND a.method = 'regex'
              AND a.evidence_locator IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM edges r
                  WHERE r.relation = 'riferimento'
                    AND r.source_id = a.source_id
                    AND r.target_id = a.target_id
                    AND r.evidence_locator = a.evidence_locator
              )
        """).fetchone()[0]
        assert orfani == 0, f"{orfani} locator senza la citazione corrispondente"

    def test_locator_format(self, con):
        """Un solo formato, lista inclusa: `art_N`, `art_N-comM`, `art_47-bis`."""
        bad = con.execute(r"""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'attua_delega'
              AND evidence_locator IS NOT NULL
              AND NOT regexp_matches(
                  evidence_locator,
                  '^(art_[0-9]+(-[a-z]+)?(-com[0-9]+)?)(,art_[0-9]+(-[a-z]+)?(-com[0-9]+)?)*$'
              )
        """).fetchone()[0]
        assert bad == 0, f"{bad} locator attua_delega fuori formato"

    def test_espressione_locator_titolo(self):
        """L'espressione di `evidence_locator` del ramo titolo, sui casi limite.

        Estratta dal SQL reale (come `test_locator_akn`) e fatta girare su
        tabelle sintetiche: protegge la finestra `[^;]{0,120}` e il plurale
        「commi」 senza dipendere dal mart.
        """
        import re
        from pathlib import Path

        import duckdb

        sql = (Path(__file__).resolve().parent.parent / "sql" / "mart_legal_edges.sql").read_text(
            encoding="utf-8"
        )
        match = re.search(
            r"(CASE\s+WHEN g\.relation <> 'attua_delega'.*?\n    END)\s+AS evidence_locator",
            sql,
            re.DOTALL,
        )
        assert match, "espressione evidence_locator non trovata nel SQL"
        expr = match.group(1)

        con = duckdb.connect()
        try:
            con.execute("CREATE TABLE g(relation VARCHAR, from_title BOOLEAN, evidence VARCHAR)")
            con.execute("CREATE TABLE l(locators VARCHAR)")
            con.execute("INSERT INTO l VALUES (NULL)")
            casi = [
                # (relation, from_title, evidence, locator della citazione, atteso)
                (
                    "attua_delega",
                    True,
                    (
                        "Attuazione della delega conferita dall'articolo 2, comma 22, "
                        "della legge 24 dicembre 2012, n. 243"
                    ),
                    None,
                    "art_2-com22",
                ),
                (
                    "attua_delega",
                    True,
                    (
                        "Riordino della disciplina sanitaria, a norma dell'articolo 1 "
                        "della legge 23 dicembre 1978, n. 833"
                    ),
                    None,
                    "art_1",
                ),
                # 「commi」 plurale: la parte è la prima elencata
                (
                    "attua_delega",
                    True,
                    (
                        "Disposizioni integrative emanate a norma dell'articolo 3, commi 1 e 2, "
                        "della legge 15 marzo 1997, n. 59"
                    ),
                    None,
                    "art_3-com1",
                ),
                # nessun articolo nominato e nessuna citazione: nessun locator inventato
                (
                    "attua_delega",
                    True,
                    "Disposizioni per il riassetto della legge 28 aprile 2022, n. 46",
                    None,
                    None,
                ),
                # nessun articolo nel titolo, ma la citazione ne ha uno: fallback
                (
                    "attua_delega",
                    True,
                    "Disposizioni per il riassetto della legge 28 aprile 2022, n. 46",
                    "art_4",
                    "art_4",
                ),
                # ramo citazione: il locator viene dalla citazione AKN
                ("attua_delega", False, "snippet citante", "art_9", "art_9"),
                ("riferimento", None, "akn:ref", "art_9", "art_9"),
            ]
            for relation, from_title, evidence, locators, atteso in casi:
                con.execute("DELETE FROM g")
                con.execute("DELETE FROM l")
                con.execute("INSERT INTO g VALUES (?, ?, ?)", [relation, from_title, evidence])
                con.execute("INSERT INTO l VALUES (?)", [locators])
                got = con.execute(f"SELECT {expr} FROM g, l").fetchone()[0]
                assert got == atteso, (evidence[:50], got, atteso)
        finally:
            con.close()

    def test_join_locator_mappa_la_citazione(self):
        """Il ramo regex usa il locator della citazione (`citation`) e non
        `attua_delega`, che in AKN non esiste."""
        from pathlib import Path

        sql = (Path(__file__).resolve().parent.parent / "sql" / "mart_legal_edges.sql").read_text(
            encoding="utf-8"
        )
        blocco = sql[sql.index("LEFT JOIN akn_locator l") :]
        blocco = blocco[: blocco.index("ORDER BY g.source_id")]
        assert "WHEN 'attua_delega' THEN 'citation'" in blocco

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

    def test_bfs_non_scarta_la_delega_title_oltre_il_top8(self):
        """Sintetico: 9 regex pesanti + 1 title; la title deve entrare in chain.

        Il `ROW_NUMBER` della BFS limita a 8 candidati per nodo. Se ordina per
        peso, un edge regex molto citato scavalca la delega letta dal titolo
        (peso 5): la vista la perde. Qui si fissa l'ordinamento per `method`.
        """
        import duckdb

        from legal_graph.mcp.node_views import _bfs

        c = duckdb.connect()
        c.execute(
            "CREATE TABLE nodes (id VARCHAR, tipo VARCHAR, title VARCHAR, anno INTEGER)"
        )
        c.execute(
            "CREATE TABLE edges (source_id VARCHAR, relation VARCHAR, target_id VARCHAR, "
            "weight BIGINT, method VARCHAR, assertion_type VARCHAR, "
            "prov_evidence_kind VARCHAR, evidence VARCHAR, evidence_locator VARCHAR)"
        )
        c.execute("INSERT INTO nodes VALUES ('x', 'DECRETO LEGISLATIVO', 'atto', 2020)")
        for i in range(9):
            c.execute("INSERT INTO nodes VALUES (?, 'LEGGE', ?, 2020)", [f"r{i}", f"regex {i}"])
            c.execute(
                "INSERT INTO edges VALUES ('x', 'attua_delega', ?, ?, 'regex', "
                "'inferred', 'none', 'akn:ref', NULL)",
                [f"r{i}", 20 - i],
            )
        c.execute("INSERT INTO nodes VALUES ('title', 'LEGGE', 'delega vera', 2019)")
        c.execute(
            "INSERT INTO edges VALUES ('x', 'attua_delega', 'title', 5, 'title', "
            "'extracted', 'locator', 'titolo', NULL)"
        )
        trovate = _bfs(c, "x", 1, outbound=True)
        target = [t[1] for t in trovate]
        assert "title" in target, f"delega title fuori dai top-8: {target}"
        c.close()


    def test_bfs_preferisce_il_locator_e_riporta_i_nascosti(self):
        """Con lo stesso peso, l'edge con `evidence_locator` viene prima.

        F-2 dello stress test 2026-11-13: l'ordinamento per id nascondeva le
        abrogazioni recenti (con locator) dietro 8 ministeriali.
        """
        import duckdb

        from legal_graph.mcp.node_views import _bfs

        c = duckdb.connect()
        c.execute(
            "CREATE TABLE nodes (id VARCHAR, tipo VARCHAR, title VARCHAR, anno INTEGER)"
        )
        c.execute(
            "CREATE TABLE edges (source_id VARCHAR, relation VARCHAR, target_id VARCHAR, "
            "weight BIGINT, method VARCHAR, assertion_type VARCHAR, "
            "prov_evidence_kind VARCHAR, evidence VARCHAR, evidence_locator VARCHAR)"
        )
        c.execute("INSERT INTO nodes VALUES ('x', 'DECRETO', 'atto', 2020)")
        for i in range(10):
            c.execute(
                "INSERT INTO nodes VALUES (?, 'DECRETO', ?, 2000)", [f"a{i}", f"abroga {i}"]
            )
            loc = "art_1" if i == 9 else None
            c.execute(
                "INSERT INTO edges VALUES ('x', 'abroga', ?, 1, 'akn_lifecycle', "
                "'extracted', 'locator', 'x', ?)",
                [f"a{i}", loc],
            )
        meta: dict[str, int] = {}
        trovate = _bfs(c, "x", 1, outbound=True, meta=meta)
        assert trovate[0][1] == "a9", trovate
        assert trovate[0][10] == "art_1"
        assert meta["hidden"] == 2
        c.close()


    def test_bfs_locator_prima_del_peso(self):
        """Il locator precede il peso, come nell'ORDER BY SQL (review #75).

        Un edge leggero con locator deve stare davanti a uno pesante senza.
        """
        import duckdb

        from legal_graph.mcp.node_views import _bfs

        c = duckdb.connect()
        c.execute(
            "CREATE TABLE nodes (id VARCHAR, tipo VARCHAR, title VARCHAR, anno INTEGER)"
        )
        c.execute(
            "CREATE TABLE edges (source_id VARCHAR, relation VARCHAR, target_id VARCHAR, "
            "weight BIGINT, method VARCHAR, assertion_type VARCHAR, "
            "prov_evidence_kind VARCHAR, evidence VARCHAR, evidence_locator VARCHAR)"
        )
        c.execute("INSERT INTO nodes VALUES ('x', 'DECRETO', 'atto', 2020)")
        c.execute("INSERT INTO nodes VALUES ('pesante', 'DECRETO', 'senza locator', 2000)")
        c.execute("INSERT INTO nodes VALUES ('evidenza', 'DECRETO', 'con locator', 2001)")
        c.execute(
            "INSERT INTO edges VALUES ('x', 'abroga', 'pesante', 100, 'akn_lifecycle', "
            "'extracted', 'locator', 'x', NULL)"
        )
        c.execute(
            "INSERT INTO edges VALUES ('x', 'abroga', 'evidenza', 1, 'akn_lifecycle', "
            "'extracted', 'locator', 'x', 'art_9')"
        )
        for i in range(7):
            c.execute("INSERT INTO nodes VALUES (?, 'DECRETO', ?, 1999)", [f"f{i}", f"f{i}"])
            c.execute(
                "INSERT INTO edges VALUES ('x', 'abroga', ?, 10, 'akn_lifecycle', "
                "'extracted', 'locator', 'x', NULL)",
                [f"f{i}"],
            )
        trovate = _bfs(c, "x", 1, outbound=True)
        assert trovate[0][1] == "evidenza", trovate
        c.close()
