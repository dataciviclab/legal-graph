"""Legal Graph — contratto edge attua_delega.

Controlla la semantica dell'euristica D.Lgs -> legge delega sui dati:
- soglia peso >= 1 (le citazioni recenti valgono 1) e guardia temporale;
- edge deboli conservati con il peso reale (weight < 5);
- sorgenti D.Lgs e bersagli LEGGE, nessun target futuro.

Le soglie nel SQL non sono asserite sul testo del file: `test_weak_edges_preserved`
e `test_no_future_edges` verificano l'effetto, che è ciò che conta.

I test che leggono il mart (`con`) riflettono il mart **pubblicato**: su un
build precedente al cambiamento che stanno verificando falliscono per
costruzione. Non è un test rotto — è atteso che passino solo dopo il build
che le porta in produzione (`pipeline.yml`, non la CI delle PR che fa solo
preflight + lint).
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

    def test_locator_titolo_ancorato_al_bersaglio(self):
        """I tre CTE reali del locator da titolo, sui casi che sbagliavano.

        Titoli veri (dal mart) in cui l'articolo nominato **prima** è di un
        altro atto: senza l'ancoraggio alla citazione della legge bersaglio
        usciva un locator impossibile (`art_55` per la L. 124/2015).
        """
        import re
        from pathlib import Path

        import duckdb

        sql = (Path(__file__).resolve().parent.parent / "sql" / "mart_legal_edges.sql").read_text(
            encoding="utf-8"
        )

        def cte(nome: str) -> str:
            m = re.search(rf"{nome} AS \((.*?)\n\),", sql, re.DOTALL)
            assert m, f"CTE {nome} non trovata nel SQL"
            return m.group(1)

        query = (
            f"WITH delega_citazione AS ({cte('delega_citazione')}),"
            f" delega_clausola AS ({cte('delega_clausola')}),"
            f" delega_locator AS ({cte('delega_locator')})"
            " SELECT locator FROM delega_locator"
        )

        casi = [
            # (target, titolo, atteso)
            (
                "urn:nir:stato:legge:2015-08-07;124",
                (
                    "Modifiche all'articolo 55-quater del decreto legislativo 30 marzo 2001, "
                    "n. 165, ai sensi dell'articolo 17, comma 1, lettera s), della legge 7 agosto "
                    "2015, n. 124, in materia di licenziamento disciplinare. (16G00127)"
                ),
                "art_17-com1",
            ),
            (
                "urn:nir:stato:legge:2015-08-07;124",
                "Disposizioni integrative e correttive, a norma dell'articolo 1, commi 2 e 3, "
                "della legge 1\u00b0 dicembre 2018, n. 132, al decreto legislativo 29 maggio 2017, "
                "n. 95, recante: " + "\u00ab" + "Disposizioni in materia di revisione dei ruoli "
                "delle Forze di polizia, ai sensi dell'articolo 8, comma 1, lettera a), della legge "
                "7 agosto 2015, n. 124" + "\u00bb" + ". (20G00012)",
                "art_8-com1",
            ),
            (
                "urn:nir:stato:legge:2024-03-05;21",
                (
                    "Attuazione della delega di cui all'articolo 19-bis della legge 5 marzo 2024, "
                    "n. 21, per la riforma organica e il riordino delle carriere"
                ),
                "art_19-bis",
            ),
            (
                "urn:nir:stato:legge:2012-12-24;243",
                (
                    "Attuazione della delega conferita dall'articolo 2, comma 22, della legge "
                    "24 dicembre 2012, n. 243"
                ),
                "art_2-com22",
            ),
            # plurale: si tiene il primo comma elencato
            (
                "urn:nir:stato:legge:1997-03-15;59",
                (
                    "Disposizioni integrative emanate a norma dell'articolo 3, commi 1 e 2, "
                    "della legge 15 marzo 1997, n. 59"
                ),
                "art_3-com1",
            ),
            # nessun articolo nominato: nessun locator inventato
            (
                "urn:nir:stato:legge:2022-04-28;46",
                "Disposizioni per il riassetto della legge 28 aprile 2022, n. 46",
                None,
            ),
        ]

        con = duckdb.connect()
        try:
            con.execute(
                "CREATE TABLE grouped(source_id VARCHAR, target_id VARCHAR, relation VARCHAR,"
                " evidence VARCHAR, from_title BOOLEAN)"
            )
            for target, titolo, atteso in casi:
                con.execute("DELETE FROM grouped")
                con.execute(
                    "INSERT INTO grouped VALUES ('src', ?, 'attua_delega', ?, TRUE)",
                    [target, titolo],
                )
                got = con.execute(query).fetchone()
                got = got[0] if got else None
                assert got == atteso, (titolo[:60], got, atteso)
        finally:
            con.close()

    def test_locator_titolo_casi_note(self, con):
        """Regressione sui quattro edge reali che sbagliavano l'articolo."""
        attesi = {
            "urn:nir:stato:decreto.legislativo:2016-06-20;116": "art_17-com1",
            "urn:nir:stato:decreto.legislativo:2017-07-20;118": "art_17-com1",
            "urn:nir:stato:decreto.legislativo:2019-12-27;172": "art_8-com1",
            "urn:nir:stato:decreto.legislativo:2026-06-25;128": "art_19-bis",
        }
        for source, atteso in attesi.items():
            got = con.execute(
                "SELECT evidence_locator FROM edges WHERE relation = 'attua_delega'"
                " AND source_id = ? AND method = 'title'",
                [source],
            ).fetchone()
            assert got, f"edge attua_delega 'title' mancante per {source}"
            assert got[0] == atteso, (source, got[0], atteso)

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


class TestDelegaConfEvidenza:
    """`conf_evidence` misura l'evidenza; `conf_extraction` resta il metodo.

    Il campo è additivo e popolato solo su `attua_delega`: per le altre
    relazioni non esiste una regola misurata. I test sul mart riflettono il
    build pubblicato (vedi il docstring del modulo).
    """

    CASI = (
        "urn:nir:stato:legge:2026-03-17;36",
        "urn:nir:stato:legge:2025-08-08;120",
        "urn:nir:stato:legge:2024-07-04;102",
        "urn:nir:stato:legge:2024-02-21;15",
    )

    def test_ogni_delega_ha_un_valore_ammesso(self, con):
        total, with_value, bad = con.execute("""
            SELECT COUNT(*),
                   COUNT(conf_evidence),
                   COUNT(*) FILTER (
                       WHERE conf_evidence NOT IN ('strong', 'medium', 'weak', 'review')
                   )
            FROM edges
            WHERE relation = 'attua_delega'
        """).fetchone()
        assert total > 0
        assert bad == 0, f"{bad} conf_evidence fuori vocabolario"
        assert with_value == total, f"conf_evidence mancante su {total - with_value} archi"

    def test_le_altre_relazioni_restano_null(self, con):
        bad = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation <> 'attua_delega' AND conf_evidence IS NOT NULL
        """).fetchone()[0]
        assert bad == 0, f"{bad} archi non-delega con conf_evidence"

    def test_non_e_una_copia_del_metodo(self, con):
        """Se `conf_evidence` ripetesse `conf_extraction` non aggiungerebbe nulla.

        Il ramo regex è `weak` per metodo, ma la maggior parte dei suoi quote ha
        una citazione completa: la colonna deve dire quello, non il metodo.
        """
        n = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'attua_delega'
              AND conf_extraction = 'weak'
              AND conf_evidence IN ('strong', 'medium')
        """).fetchone()[0]
        assert n > 1_000, f"solo {n} archi 'weak' con citazione: la regola non gira?"

    def test_i_quattro_casi_w3(self, con):
        placeholders = ",".join("?" for _ in self.CASI)
        rows = {
            target: (strong, medium, review)
            for target, strong, medium, review in con.execute(
                f"""
                SELECT target_id,
                       COUNT(*) FILTER (WHERE conf_evidence = 'strong'),
                       COUNT(*) FILTER (WHERE conf_evidence = 'medium'),
                       COUNT(*) FILTER (WHERE conf_evidence = 'review')
                FROM edges
                WHERE relation = 'attua_delega' AND target_id IN ({placeholders})
                GROUP BY 1
                """,
                list(self.CASI),
            ).fetchall()
        }
        assert len(rows) == 4, rows
        # Tre leggi hanno solo citazioni complete con marker.
        for urn in self.CASI[:1] + self.CASI[2:]:
            assert rows[urn][2] == 0, (urn, rows[urn])
        assert rows["urn:nir:stato:legge:2026-03-17;36"][0] > 0
        # L. 120/2025: un arco cita la legge che *modifica* la delega, senza
        # marker nel quote → medium, ed è il caso che resta da leggere.
        assert rows["urn:nir:stato:legge:2025-08-08;120"][1] >= 1, rows

    def test_espressione_conf_evidenza(self):
        """Il CTE reale sui quote limite, senza dipendere dal mart."""
        import re
        from pathlib import Path

        import duckdb

        sql = (Path(__file__).resolve().parent.parent / "sql" / "mart_legal_edges.sql").read_text(
            encoding="utf-8"
        )
        match = re.search(r"delega_conf_evidenza AS \((.*?)\n\),", sql, re.DOTALL)
        assert match, "CTE delega_conf_evidenza non trovata nel SQL"

        target = "urn:nir:stato:legge:2015-08-07;124"
        casi = [
            # (target, quote, atteso)
            (
                target,
                (
                    "Attuazione della delega di cui all'articolo 17, comma 1, della legge "
                    "7 agosto 2015, n. 124"
                ),
                "strong",
            ),
            (
                target,
                "Modifiche alla legge 7 agosto 2015, n. 124 in materia di organizzazione",
                "medium",
            ),
            (target, "come previsto dalla L. 124/2015", "weak"),
            (target, "akn:ref", "review"),
            # un'altra legge citata per esteso non basta
            (
                target,
                "a norma dell'articolo 1 della legge 1° dicembre 2018, n. 132",
                "review",
            ),
            # forma inversa, con marker
            (
                target,
                "in attuazione della legge n. 124 del 7 agosto 2015",
                "strong",
            ),
            (target, None, "review"),
        ]

        con = duckdb.connect()
        try:
            con.execute(
                "CREATE TABLE grouped(source_id VARCHAR, target_id VARCHAR, "
                "relation VARCHAR, evidence VARCHAR)"
            )
            for law, quote, atteso in casi:
                con.execute("DELETE FROM grouped")
                con.execute(
                    "INSERT INTO grouped VALUES ('src', ?, 'attua_delega', ?)", [law, quote]
                )
                got = con.execute(
                    f"WITH delega_conf_evidenza AS ({match.group(1)}) "
                    "SELECT conf_evidence FROM delega_conf_evidenza"
                ).fetchone()
                assert got and got[0] == atteso, (quote, got, atteso)
        finally:
            con.close()


class TestDelegaPreambolo:
    """La delega confermata dal preambolo (italia-corpus #174).

    I test sul mart riflettono il build pubblicato: finché `riferimenti` non
    porta `rel_type='delega'` valgono le guardie di seguito.
    """

    def test_preambolo_non_aggiunge_archi(self, con):
        """Il join col preambolo decora, non moltiplica né aggiunge righe."""
        total, distinct = con.execute("""
            SELECT COUNT(*), COUNT(DISTINCT (source_id, target_id))
            FROM edges WHERE relation = 'attua_delega'
        """).fetchone()
        assert total == distinct, f"{total} archi vs {distinct} coppie: fan-out"

    def test_non_confermati_non_sono_strong(self, con):
        n_pre = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'attua_delega' AND method = 'preambolo'
        """).fetchone()[0]
        if n_pre == 0:
            return  # mart IC senza `rel_type='delega'`: regola non applicabile
        bad = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'attua_delega'
              AND method <> 'preambolo'
              AND conf_evidence = 'strong'
        """).fetchone()[0]
        assert bad == 0, f"{bad} archi non confermati restano 'strong'"

    def test_preambolo_il_caso_noto(self, con):
        row = con.execute("""
            SELECT method, assertion_type, conf_evidence
            FROM edges
            WHERE relation = 'attua_delega'
              AND source_id = 'urn:nir:stato:decreto.legislativo:2025-05-15;71'
              AND target_id = 'urn:nir:stato:legge:2025-03-14;26'
        """).fetchone()
        assert row == ('preambolo', 'extracted', 'strong'), row
