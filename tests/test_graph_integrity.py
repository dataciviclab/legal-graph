"""Legal Graph — Integrity tests.

Verifica che il grafo legale sia riproducibile, senza duplicati,
e con tutti gli archi puntanti a nodi esistenti.

Usage:
  pytest tests/ -v
"""

from __future__ import annotations

import duckdb
import pytest

# La connessione ai mart è la fixture `con` di `tests/conftest.py`.


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    rows = con.execute("SHOW TABLES").fetchall()
    return any(r[0] == name for r in rows)


# ── Node integrity ──────────────────────────────────────────────

class TestNodes:
    def test_nodes_exist(self, con):
        count = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        assert count > 400_000, f"Expected >400K nodes, got {count}"

    def test_no_duplicate_ids(self, con):
        """`id` è chiave primaria del mart: un duplicato rompe i join MCP.

        Il filtro sui `promovimento:` era un'eccezione per un bug chiuso
        (dedup in build_legal_nodes.py, script poi rimosso): ora vale per tutti.
        """
        dups = con.execute("""
            SELECT id, COUNT(*) as cnt
            FROM nodes
            GROUP BY id
            HAVING cnt > 1
        """).fetchall()
        assert not dups, f"{len(dups)} id duplicati, es. {[d[0] for d in dups[:3]]}"

    def test_all_nodes_have_tipo(self, con):
        missing = con.execute("""
            SELECT COUNT(*) FROM nodes WHERE tipo IS NULL OR tipo = ''
        """).fetchone()[0]
        assert missing == 0, f"{missing} nodes missing tipo"

    def test_all_nodes_have_source(self, con):
        missing = con.execute("""
            SELECT COUNT(*) FROM nodes WHERE source IS NULL OR source = ''
        """).fetchone()[0]
        assert missing == 0, f"{missing} nodes missing source"

    def test_id_namespace_prefixes(self, con):
        """All node IDs should have a recognized namespace prefix."""
        valid_prefixes = (
            "urn:nir:", "costituzione:", "revisione:", "gu:",
            "senato:", "camera:", "sentenza:", "giudice:",
            "norma:", "promovimento:", "celex:", "senatore:",
            "deputato:", "votazione:", "itercost:", "dl:",
        )
        invalid = con.execute(f"""
            SELECT id FROM nodes
            WHERE NOT ({' OR '.join(f"id LIKE '{p}%'" for p in valid_prefixes)})
            LIMIT 10
        """).fetchall()
        assert len(invalid) == 0, f"Nodes with invalid ID prefix: {[r[0] for r in invalid]}"


# ── Edge integrity ──────────────────────────────────────────────

class TestEdges:
    def test_edges_exist(self, con):
        count = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        assert count > 700_000, f"Expected >700K edges, got {count}"

    def test_no_duplicate_triples(self, con):
        dups = con.execute("""
            SELECT source_id, relation, target_id, COUNT(*) as cnt
            FROM edges
            GROUP BY source_id, relation, target_id
            HAVING cnt > 1
        """).fetchall()
        assert len(dups) == 0, f"Found {len(dups)} duplicate edge triples"

    def test_all_edges_have_relation(self, con):
        missing = con.execute("""
            SELECT COUNT(*) FROM edges WHERE relation IS NULL OR relation = ''
        """).fetchone()[0]
        assert missing == 0, f"{missing} edges missing relation"

    def test_edges_in_e_la_stessa_tabella(self, con):
        """`edges_in` è `edges` in un altro ordine fisico, non un grafo diverso.

        Le view MCP leggono `edges_in` per le query in entrata (parquet
        ordinato per `target_id` → row-group pruning): se le due copie
        divergessero, il grafo risponderebbe in modo diverso a seconda della
        direzione della domanda.
        """
        if not _has_table(con, "edges_in"):
            pytest.skip("mart_legal_edges_in assente — esegui make run")
        n_edges, h_edges, n_in, h_in = con.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM edges),
              (SELECT SUM(hash(source_id, relation, target_id, weight)) FROM edges),
              (SELECT COUNT(*) FROM edges_in),
              (SELECT SUM(hash(source_id, relation, target_id, weight)) FROM edges_in)
            """
        ).fetchone()
        assert (n_edges, h_edges) == (n_in, h_in), (
            f"edges_in diverge da edges: {n_edges} righe vs {n_in}"
        )

    def test_source_targets_exist_in_nodes(self, con):
        """Every edge source must exist as a node."""
        missing_src = con.execute("""
            SELECT COUNT(*) FROM edges e
            WHERE NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.source_id)
        """).fetchone()[0]
        missing_tgt = con.execute("""
            SELECT COUNT(*) FROM edges e
            WHERE NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.target_id)
        """).fetchone()[0]
        assert missing_src == 0, f"{missing_src} edges have source_id not in nodes"
        # Zero, non "≤N": i target dangling erano un workaround (il compose
        # scarta le righe non risolte, quindi un target mancante è un bug).
        assert missing_tgt == 0, f"{missing_tgt} dangling target edges"

    def test_relation_type_coverage(self, con):
        """At least 12 distinct relation types should exist."""
        types = con.execute("""
            SELECT DISTINCT relation FROM edges ORDER BY relation
        """).fetchall()
        assert len(types) >= 12, f"Expected >=12 relation types, got {len(types)}"

    def test_weights_positive(self, con):
        bad = con.execute("""
            SELECT COUNT(*) FROM edges WHERE weight <= 0
        """).fetchone()[0]
        assert bad == 0, f"{bad} edges have weight <= 0"

    def test_no_self_loops(self, con):
        """Un atto non è in relazione con sé stesso.

        I rinvii interni tra articoli collassano sul nodo-atto: senza filtro
        l'edge più pesante del grafo è un self-loop (D.Lgs 58/1998, weight 436)
        e `impact_score`/`referenced_by` lo contano come citazione esterna.
        """
        loops = con.execute("""
            SELECT relation, COUNT(*) AS n
            FROM edges
            WHERE source_id = target_id
            GROUP BY relation
        """).fetchall()
        assert not loops, f"self-loop nel grafo: {loops}"


# ── Temporal edge integrity ─────────────────────────────────────

class TestParametriCostituzionali:
    """`invoca_parametro` = parametro costituzionale, non "un articolo qualsiasi".

    Le massime hanno parametri di atti diversi (statuti regionali, codici,
    CEDU, TFUE) e solo `parametro_codice = '25'` è la Costituzione. Prima del
    2026-11 ogni parametro diventava `costituzione:art:N` (art. 145 cod. civ.
    → art. 145 Cost., che non esiste).
    """

    def test_target_sono_articoli_esistenti(self, con):
        bad = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'invoca_parametro'
              AND (
                  target_id NOT LIKE 'costituzione:art:%'
                  OR TRY_CAST(replace(target_id, 'costituzione:art:', '') AS INTEGER)
                     NOT BETWEEN 1 AND 139
              )
        """).fetchone()[0]
        assert bad == 0, f"{bad} edge invoca_parametro fuori dagli articoli 1..139"

    def test_parametro_di_altro_atto_escluso(self, con):
        """Sent. 6/1971: parametro art. 145 del codice civile, non della Costituzione."""
        n = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'invoca_parametro'
              AND source_id = 'sentenza:1971-0006'
              AND target_id = 'costituzione:art:145'
        """).fetchone()[0]
        assert n == 0, "parametro non costituzionale mappato sulla Costituzione"

    def test_evidence_presente(self, con):
        n = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'invoca_parametro'
              AND (evidence IS NULL OR evidence = '')
        """).fetchone()[0]
        assert n == 0, f"{n} edge invoca_parametro senza evidence"


class TestIdentitaSenato:
    """Documento AKN e atto canonico non devono duplicare lo stesso oggetto."""

    def test_no_stub_corpus_duplicato(self, con):
        """Un nodo `senato:X` da corpus non convive con il suo `senato:atto:X`."""
        n = con.execute("""
            SELECT COUNT(*) FROM nodes a
            WHERE a.id LIKE 'senato:%'
              AND a.id NOT LIKE 'senato:%:%'
              AND a.source = 'senato_corpus'
              AND EXISTS (
                  SELECT 1 FROM nodes b
                  WHERE b.id = 'senato:atto:' || substr(a.id, 8)
              )
        """).fetchone()[0]
        assert n == 0, f"{n} atti del corpus duplicati come documento + atto"


class TestEvidenzaFormulaica:
    """`is_formulaic`: stesso snippet usato da >50 atti distinti.

    Caso reale: la clausola di pubblicazione che ogni preambolo dedica al
    D.P.R. 1092/1985 (2.727 edge). Il flag è calcolato sulle righe pubblicate,
    quindi ricalcolabile dal mart: compose e consumatori non possono divergere.
    """

    def test_flag_riproducibile(self, con):
        mismatch = con.execute("""
            WITH bp AS (
                SELECT evidence FROM edges
                WHERE relation = 'riferimento'
                  AND evidence IS NOT NULL AND evidence <> 'akn:ref'
                GROUP BY 1 HAVING COUNT(DISTINCT source_id) > 50
            )
            SELECT COUNT(*) FROM edges e
            WHERE e.is_formulaic IS DISTINCT FROM (
                e.relation = 'riferimento' AND e.evidence IN (SELECT evidence FROM bp)
            )
        """).fetchone()[0]
        assert mismatch == 0, f"{mismatch} edge con flag non ricalcolabile dal mart"

    def test_solo_riferimento(self, con):
        altre = con.execute("""
            SELECT DISTINCT relation FROM edges
            WHERE is_formulaic AND relation <> 'riferimento'
        """).fetchall()
        assert not altre, f"flag su relazioni non previste: {altre}"

    def test_reading_clause_solo_riferimento(self, con):
        """`clausola_lettura` è un segnale della fonte sulle citazioni: vale solo
        per `riferimento`. Senza il guard sulla relazione, un `abroga` tra la
        stessa coppia veniva marcato (2 casi nel primo build)."""
        altre = con.execute("""
            SELECT DISTINCT relation FROM edges
            WHERE is_reading_clause AND relation <> 'riferimento'
        """).fetchall()
        assert not altre, f"is_reading_clause su relazioni non previste: {altre}"

    def test_reading_clause_dal_upstream(self, con):
        """Le clausole di lettura vengono dal flag `clausola_lettura` della fonte."""
        n = con.execute("SELECT COUNT(*) FROM edges WHERE is_reading_clause").fetchone()[0]
        assert n > 3_000, f"solo {n} edge con clausola di lettura"

    def test_evidence_kind_partizione(self, con):
        """`evidence_kind` partiziona i `riferimento`; NULL sulle altre relazioni."""
        non_rif = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation <> 'riferimento' AND evidence_kind IS NOT NULL
        """).fetchone()[0]
        assert non_rif == 0, f"{non_rif} edge non-riferimento con evidence_kind"

        kinds = dict(con.execute("""
            SELECT evidence_kind, COUNT(*) FROM edges
            WHERE relation = 'riferimento' GROUP BY 1
        """).fetchall())
        totale = con.execute(
            "SELECT COUNT(*) FROM edges WHERE relation = 'riferimento'"
        ).fetchone()[0]
        assert sum(kinds.values()) == totale, (kinds, totale)
        # `locator` resta possibile per i mart legacy; con il contesto pulito
        # (IC 2026-11-10) le citazioni non hanno più evidenza-URL, quindi il
        # set effettivo è {missing, quote}. La partizione è il contratto.
        assert set(kinds) <= {"missing", "locator", "quote"}, kinds
        assert {"missing", "quote"} <= set(kinds), kinds

        mismatch = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE COALESCE(evidence_kind = 'missing', FALSE)
              IS DISTINCT FROM (
                  relation = 'riferimento'
                  AND (evidence IS NULL OR evidence = '' OR evidence = 'akn:ref')
              )
        """).fetchone()[0]
        assert mismatch == 0, f"{mismatch} edge con evidence_kind 'missing' incoerente"

    def test_metrics_lordo_uscente(self, con):
        """Il filtro vale anche in uscita, ma il lordo (`references + formulaic_out`) resta."""
        if not _has_table(con, "metrics"):
            pytest.skip("metrics non presenti")
        row = con.execute("""
            SELECT id, "references", formulaic_out FROM metrics
            WHERE formulaic_out > 0 ORDER BY formulaic_out DESC LIMIT 1
        """).fetchone()
        assert row, "nessun nodo con uscenti formulaici"
        nid, refs, fout = row
        lordo = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE source_id = ? AND relation IN (
                'riferimento', 'cita_costituzione', 'impugna', 'invoca_parametro',
                'diventa_legge', 'recepisce_direttiva', 'attua_regolamento',
                'evoca_parametro', 'abroga', 'attua_delega'
            )
        """, [nid]).fetchone()[0]
        assert refs + fout == lordo, (refs, fout, lordo)

    def test_metrics_esclude_e_dichiara(self, con):
        """`referenced_by` è al netto; `formulaic_by` rende il lordo ispezionabile."""
        if not _has_table(con, "metrics"):
            pytest.skip("metrics non presenti")
        ref, formulaic, level = con.execute("""
            SELECT referenced_by, formulaic_by, impact_level
            FROM metrics
            WHERE id = 'urn:nir:stato:decreto.del.presidente.della.repubblica:1985-12-28;1092'
        """).fetchone()
        assert formulaic > 2_000, f"formulaic_by={formulaic}"
        assert ref + formulaic > 4_000, "il lordo deve restare visibile"
        assert ref < formulaic, "il D.P.R. 1092 vive di citazioni formulaiche"
        assert level == "critical", "resta critical, ma con un conteggio onesto"


class TestIdentitaNorme:
    """Fallback `norma:*` e duplicati `dl:` vs URN.

    `norma:*` erano creati per *ogni* norma citata da una massima: 5.000 su
    8.439 non erano target di nessun edge. `dl:*` duplicava 213 DL già
    presenti come URN nel corpus.
    """

    def test_norme_senza_archi(self, con):
        n = con.execute("""
            SELECT COUNT(*) FROM nodes n
            WHERE n.id LIKE 'norma:%'
              AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.target_id = n.id)
        """).fetchone()[0]
        assert n == 0, f"{n} nodi norma:* senza edge in ingresso"

    def test_norme_totali_plausibili(self, con):
        n = con.execute("SELECT COUNT(*) FROM nodes WHERE id LIKE 'norma:%'").fetchone()[0]
        assert 0 < n < 6_000, f"{n} nodi norma:* (attesi <6k: solo i target di impugna)"

    def test_dl_non_duplicati(self, con):
        n = con.execute("""
            SELECT COUNT(*) FROM nodes d
            WHERE d.id LIKE 'dl:%'
              AND EXISTS (
                  SELECT 1 FROM nodes u
                  WHERE u.id LIKE 'urn:nir:stato:decreto.legge:%'
                    AND u.anno = TRY_CAST(split_part(substr(d.id, 4), '-', 1) AS INT)
                    AND u.numero = TRY_CAST(split_part(substr(d.id, 4), '-', 2) AS BIGINT)
              )
        """).fetchone()[0]
        assert n == 0, f"{n} DL presenti sia come dl:* sia come URN"


class TestIdentitaEmendamenti:
    """`senato:emend:{leg}:{fase}:{numero}`: l'atto entra nell'id.

    Prima l'id era `senato:emend:{leg}:{numero}`: "1.1" esiste in centinaia di
    atti, quindi un nodo aggregava emendamenti diversi (fino a 427 atti).
    """

    def test_id_contiene_fase(self, con):
        bad = con.execute("""
            SELECT COUNT(*) FROM nodes
            WHERE source = 'senato_emend' AND NULLIF(split_part(id, ':', 4), '') IS NULL
        """).fetchone()[0]
        assert bad == 0, f"{bad} nodi emendamento senza fase nell'id"

    def test_un_atto_una_fase(self, con):
        """Tutti gli emendamenti collegati a un atto appartengono alla sua fase."""
        bad = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'emendamento'
              AND target_id = 'senato:40754'
              AND split_part(source_id, ':', 4) <> 'S.1385'
        """).fetchone()[0]
        assert bad == 0, f"{bad} emendamenti dell'Italicum con fase diversa"

    def test_nessun_nodo_condiviso(self, con):
        """L'id è unico: nessun nodo aggregato su più atti."""
        dup = con.execute("""
            SELECT COUNT(*) FROM (SELECT id FROM nodes GROUP BY 1 HAVING COUNT(*) > 1)
        """).fetchone()[0]
        assert dup == 0


class TestTassonomia:
    """`tipo` canonico: pochi valori, nessun dato grezzo.

    Prima del 2026-11: 457 valori distinti, incluse date (`1996-06-06`) prese
    dal campo `natura` di open-politica, ~30 varianti di leggi regionali,
    `LEGGE` vs `Legge ordinaria`, `DDL` spacciati per `Legge ordinaria`.
    """

    def test_pochi_valori(self, con):
        n = con.execute("SELECT COUNT(DISTINCT tipo) FROM nodes").fetchone()[0]
        # 30 oggi; la soglia lascia spazio a 1-2 aggiunte legittime senza
        # riaprire la porta alla frammentazione (era 457).
        assert n <= 32, f"{n} tipi distinti (atteso <=32)"

    def test_nessun_tipo_data(self, con):
        bad = con.execute("""
            SELECT DISTINCT tipo FROM nodes
            WHERE regexp_matches(tipo, '^\\d{4}-\\d{2}-\\d{2}$')
        """).fetchall()
        assert not bad, f"tipi che sono date: {[b[0] for b in bad]}"

    def test_tipi_attesi_presenti(self, con):
        tipi = {r[0] for r in con.execute("SELECT DISTINCT tipo FROM nodes").fetchall()}
        for atteso in ("LEGGE", "DDL", "EMENDAMENTO", "SENTENZA", "COSTITUZIONE", "VOTAZIONE"):
            assert atteso in tipi, f"manca il tipo canonico {atteso}"

    def test_altro_residuale(self, con):
        altro, tot = con.execute("""
            SELECT SUM(CASE WHEN tipo = 'ALTRO' THEN 1 ELSE 0 END), COUNT(*)
            FROM nodes
        """).fetchone()
        # 0,09% oggi: con l'1% il residuo poteva decuplicare restando verde.
        assert (altro or 0) / tot < 0.005, f"ALTRO {altro}/{tot}"


class TestEmendamenti:
    """Emendamenti → atto: il target è il nodo del grafo, non solo `senato_ddl`.

    Leg 19: 486 atti negli emendamenti, 15 in `senato_ddl` (l'export
    open-politica si ferma a id 55687), 486 nel corpus AKN. Il join stretto
    lasciava 103.829 emendamenti su 248.127 senza arco (42%).
    """

    def test_nodi_senza_archi(self, con):
        """Nodi emendamento isolati: solo un segnale, non la quota di copertura.

        La copertura vera è in `coverage.edge_pct` (denominatore = emendamenti
        distinti): `senato:emend:{leg}:{emend_id}` aggrega più atti, quindi
        contare i nodi sottostima gli emendamenti orfani di ~5×.
        """
        tot, orfani = con.execute("""
            SELECT COUNT(*) AS tot,
                   SUM(CASE WHEN NOT EXISTS (
                       SELECT 1 FROM edges e WHERE e.source_id = n.id
                   ) THEN 1 ELSE 0 END) AS orfani
            FROM nodes n WHERE n.source = 'senato_emend'
        """).fetchone()
        assert tot > 200_000, f"attesi >200k nodi emendamento, trovati {tot}"
        assert orfani / tot < 0.15, f"{orfani}/{tot} nodi emendamento isolati"

    def test_target_sono_atti_senato(self, con):
        """Il target è l'atto risolto via `fase` (nodo `senato:*`).

        Il fallback su `atto_num` (nodi `senato_corpus`) vale solo per le fasi
        fuori da open-politica con directory unica: deve restare minoritario.
        """
        righe = dict(con.execute("""
            SELECT n.source, COUNT(*) FROM edges e
            JOIN nodes n ON n.id = e.target_id
            WHERE e.relation = 'emendamento' GROUP BY 1
        """).fetchall())
        assert righe.get("senato", 0) > 450_000, righe
        assert righe.get("senato_corpus", 0) < 20_000, righe


# ── Metrics integrity ───────────────────────────────────────────

class TestMetrics:
    """Metriche dal compose (mart_legal_node_metrics, richiede make run)."""

    def test_metrics_exist(self, con):
        if not _has_table(con, "metrics"):
            pytest.skip("metrics non presenti (esegui make run)")
        count = con.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        assert count > 400_000, f"Expected >400K metric rows, got {count}"

    def test_metrics_match_nodes(self, con):
        """Every node should have a metrics row."""
        if not _has_table(con, "metrics"):
            pytest.skip("metrics non presenti")
        node_count = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        metric_count = con.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        assert metric_count >= node_count * 0.95, (
            f"Metrics ({metric_count}) should cover >=95% of nodes ({node_count})"
        )

    def test_impact_levels_populated(self, con):
        if not _has_table(con, "metrics"):
            pytest.skip("metrics non presenti")
        critical = con.execute("""
            SELECT COUNT(*) FROM metrics WHERE impact_level = 'critical'
        """).fetchone()[0]
        assert critical > 0, "No critical nodes found"

    def test_no_negative_age(self, con):
        if not _has_table(con, "metrics"):
            pytest.skip("metrics non presenti")
        bad = con.execute("""
            SELECT COUNT(*) FROM metrics WHERE age_years < 0
        """).fetchone()[0]
        # Known issue: 1 norma node with anno=2105 (bad massime data)
        assert bad <= 1, f"{bad} nodes have negative age_years (expected <=1)"


# ── Cardinality snapshot ────────────────────────────────────────

class TestCardinality:
    """Verify expected cardinality ranges. These act as regression guards."""

    def test_node_count_range(self, con):
        count = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        # include deputati, votazioni, iter cost e un nodo per emendamento
        # (legislatura, fase, numero): ~561k emendamenti distinti.
        assert 750_000 <= count <= 900_000, f"Node count {count} outside expected range"

    def test_edge_count_range(self, con):
        count = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        # include relatore + firmatari + votazioni + iter cost
        assert 1_000_000 <= count <= 1_450_000, f"Edge count {count} outside expected range"

    def test_normativa_nodes_range(self, con):
        count = con.execute("""
            SELECT COUNT(*) FROM nodes WHERE source = 'normativa'
        """).fetchone()[0]
        assert 20_000 <= count <= 25_000, f"Normativa nodes {count} outside expected range"

    def test_qualita_ic_columns(self, con):
        """Colonne qualità IC presenti su nodi normativa."""
        if not _has_table(con, "nodes"):
            pytest.skip("nodes non presenti")
        cols = {r[0] for r in con.execute("DESCRIBE nodes").fetchall()}
        for c in ("stato", "materia", "qualita_score", "sunsetting_score",
                  "vigenza_fonte"):
            assert c in cols, f"Colonna qualità mancante: {c}"
        n_stato = con.execute("""
            SELECT COUNT(*) FROM nodes
            WHERE source = 'normativa' AND stato IS NOT NULL AND stato <> ''
        """).fetchone()[0]
        assert n_stato > 10_000, f"Poiché nodi normativa con stato: {n_stato}"
        n_fonte = con.execute("""
            SELECT COUNT(*) FROM nodes
            WHERE source = 'normativa'
              AND vigenza_fonte IS NOT NULL AND vigenza_fonte <> ''
        """).fetchone()[0]
        assert n_fonte > 10_000, f"Poiché nodi normativa con vigenza_fonte: {n_fonte}"
        # Tutti i nodi normativa devono avere una fonte: gli atti fuori
        # collezione IC (fetch dettaglio-atto-urn) → 'atti_api' (non NULL).
        n_null = con.execute("""
            SELECT COUNT(*) FROM nodes
            WHERE source = 'normativa'
              AND (vigenza_fonte IS NULL OR vigenza_fonte = '')
        """).fetchone()[0]
        assert n_null == 0, f"Nodi normativa senza vigenza_fonte: {n_null}"
        n_api = con.execute("""
            SELECT COUNT(*) FROM nodes
            WHERE source = 'normativa' AND vigenza_fonte = 'atti_api'
        """).fetchone()[0]
        assert n_api > 0, "Attesi nodi atti_api con vigenza_fonte='atti_api'"

    def test_dedup_stato_conflitto_vigenza(self, con):
        """Regressione #67: URN con ORIGINALE=vigente e VIGENZA=abrogato
        (tombstone-only, nessun override AKN) → nodo `abrogato`.

        Il dedup per URN deve preferire lo snapshot VIGENZA. Da italia-corpus
        #88 la scelta è upstream (`is_current`), non più il workaround
        `stato_per_urn` (ROW_NUMBER/FIRST + CASE su '_VIGENZA_').
        """
        if not _has_table(con, "nodes"):
            pytest.skip("nodes non presenti")
        conflitti = [
            "urn:nir:stato:decreto.legislativo:1991-08-08;256",
            "urn:nir:stato:decreto.legislativo:1991-08-15;277",
            "urn:nir:stato:decreto.legislativo:1991-11-23;392",
        ]
        for urn in conflitti:
            row = con.execute(
                "SELECT stato, source_filename FROM nodes WHERE id = ?", [urn]
            ).fetchone()
            assert row is not None, f"nodo assente: {urn}"
            assert row[0] == "abrogato", f"{urn} → {row[0]} (atteso abrogato)"
            # il nodo serve l'espressione corrente (`is_current`): lo snapshot
            # VIGENZA, non l'ORIGINALE che si marcava 'vigente'
            assert "_VIGENZA_" in (row[1] or ""), f"{urn} serve {row[1]} (atteso VIGENZA)"

    def test_ponti_op_presenti(self, con):
        """Ponti open-politica: deputati + relazioni relatore/firmatario."""
        if not _has_table(con, "nodes"):
            pytest.skip("nodes non presenti")
        n_dep = con.execute("""
            SELECT COUNT(*) FROM nodes WHERE id LIKE 'deputato:%'
        """).fetchone()[0]
        assert n_dep > 1_000, f"Nodi deputato bassi: {n_dep}"
        if _has_table(con, "edges"):
            n_rel = con.execute("""
                SELECT COUNT(*) FROM edges WHERE relation = 'relatore'
            """).fetchone()[0]
            n_firm = con.execute("""
                SELECT COUNT(*) FROM edges WHERE relation = 'firmatario'
            """).fetchone()[0]
            n_vota = con.execute("""
                SELECT COUNT(*) FROM edges WHERE relation = 'vota'
            """).fetchone()[0]
            n_prop = con.execute("""
                SELECT COUNT(*) FROM edges WHERE relation = 'proposta_cost'
            """).fetchone()[0]
            n_rev = con.execute("""
                SELECT COUNT(*) FROM edges WHERE relation = 'diventa_revisione'
            """).fetchone()[0]
            assert n_rel > 1_000, f"Edge relatore bassi: {n_rel}"
            assert n_firm > 100_000, f"Edge firmatario bassi: {n_firm}"
            assert n_vota > 10_000, f"Edge vota bassi: {n_vota}"
            assert n_prop > 200, f"Edge proposta_cost bassi: {n_prop}"
            assert n_rev > 100, f"Edge diventa_revisione bassi: {n_rev}"
            # ponti 2026-10: relatore Camera (atto) + DL conversione
            n_rel_cam = con.execute("""
                SELECT COUNT(*) FROM edges
                WHERE relation = 'relatore' AND source_id LIKE 'deputato:%'
            """).fetchone()[0]
            n_converte = con.execute("""
                SELECT COUNT(*) FROM edges WHERE relation = 'converte_decreto_legge'
            """).fetchone()[0]
            assert n_rel_cam > 500, f"Edge relatore Camera bassi: {n_rel_cam}"
            assert n_converte > 800, f"Edge converte_decreto_legge bassi: {n_converte}"
        n_dl = con.execute("""
            SELECT COUNT(*) FROM nodes WHERE id LIKE 'dl:%'
        """).fetchone()[0]
        # Dal 2026-11 i DL citati hanno il nodo **catalogo** (URN reale): il
        # fallback `dl:*` resta solo per i non citati (12 nel build). Il
        # contratto è "l'identità del DL esiste", non "esiste come dl:*".
        n_dl_catalogo = con.execute("""
            SELECT COUNT(*) FROM nodes
            WHERE source = 'catalogo' AND id LIKE 'urn:nir:stato:decreto.legge:%'
        """).fetchone()[0]
        assert 550 <= n_dl + n_dl_catalogo <= 2_500, (
            f"Nodi DL fuori range: dl:*={n_dl} catalogo={n_dl_catalogo}"
        )
        # M1: l'atto Camera è `camera:{leg}:{id}` (stessa identità di
        # camera_ddl). Il vecchio `camera:atto:{leg}_{id}` non è più un nodo:
        # resta solo come alias in entity_alias.
        n_cam_atti = con.execute("""
            SELECT COUNT(*) FROM nodes WHERE id LIKE 'camera:atto:%'
        """).fetchone()[0]
        assert n_cam_atti == 0, f"Nodi camera:atto: non rimossi: {n_cam_atti}"
        n_cam_alias = con.execute("""
            SELECT COUNT(*) FROM entity_alias
            WHERE alias_id LIKE 'camera:atto:%'
        """).fetchone()[0]
        assert n_cam_alias > 1_000, f"Alias camera:atto: bassi: {n_cam_alias}"
        n_iter = con.execute("""
            SELECT COUNT(*) FROM nodes WHERE id LIKE 'itercost:%'
        """).fetchone()[0]
        assert n_iter > 2_000, f"Nodi itercost bassi: {n_iter}"


class TestTemporalSanity:
    """Coerenza temporale degli archi di modifica (guard nel compose AKN)."""

    def test_no_modifiche_temporalmente_impossibili(self, con):
        """Nessun abroga/sostituisce/split/join da atto anteriore al target.

        Rumore noto: ~30 passiveModification AKN estratte al contrario
        (es. atto 2006 che 'abroga' un codice 2017) — filtrato nel SQL.
        """
        if not (_has_table(con, "edges") and _has_table(con, "nodes")):
            pytest.skip("edges/nodes non presenti")
        n = con.execute("""
            SELECT COUNT(*) FROM edges e
            JOIN nodes ns ON ns.id = e.source_id
            JOIN nodes nt ON nt.id = e.target_id
            WHERE e.relation IN ('abroga', 'sostituisce', 'split', 'join', 'renumbering')
              AND ns.anno IS NOT NULL AND nt.anno IS NOT NULL
              AND ns.anno < nt.anno
        """).fetchone()[0]
        assert n == 0, f"{n} archi di modifica con source anteriore al target"


# ── Dimensione atto (open-politica `atti`, 2026-11) ─────────────

class TestAttiCameraKey:
    """La chiave Camera deve includere la legislatura: `camera:{leg}:{id}`.

    `camera_ddl.id_ddl` riparte ogni legislatura: 6.328 valori su 7.614
    compaiono in più legislature (es. l'atto n.1 esiste in tutte e 7). Fino a
    2026-11 il compose deduplicava PARTITION BY id_ddl e i 34.315 atti Camera
    diventavano 7.614 nodi (26.699 atti persi, con la riga più recente a
    sopravvivere). Ora il nodo viene da open-politica `atti`, che espone
    `atto_id` namespaced.
    """

    def test_camera_node_ids_hanno_legislatura(self, con):
        if not _has_table(con, "nodes"):
            pytest.skip("nodes non presente")
        bad = con.execute("""
            SELECT COUNT(*) FROM nodes
            WHERE source = 'camera_ddl'
              AND NOT regexp_matches(id, '^camera:[0-9]+:[0-9]+$')
        """).fetchone()[0]
        assert bad == 0, f"{bad} nodi camera_ddl senza legislatura nell'id"

    def test_camera_atti_completi(self, con):
        """34.315 atti Camera reali (Leg13-19): sotto soglia = collasso."""
        if not _has_table(con, "nodes"):
            pytest.skip("nodes non presente")
        n = con.execute(
            "SELECT COUNT(*) FROM nodes WHERE source = 'camera_ddl'"
        ).fetchone()[0]
        assert n >= 34_000, f"solo {n} nodi camera_ddl (collasso su id_ddl?)"

    def test_firmatario_camera_coerente_con_legislatura(self, con):
        """Un firmatario non firma un atto di un'altra legislatura."""
        if not _has_table(con, "edges"):
            pytest.skip("edges non presente")
        bad = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'firmatario'
              AND target_id LIKE 'camera:%'
              AND target_id NOT LIKE 'camera:' || CAST(source_year AS VARCHAR) || ':%'
        """).fetchone()[0]
        assert bad == 0, f"{bad} edge firmatario con legislatura incoerente"


class TestDlDaAtti:
    """I DL vengono da `atti.dl_numero/dl_anno` (assorbe compose/decreti-legge).

    Prima esistevano due support (`decreti_legge` clean + lookup URN nel
    compose); ora la dimensione DL è una sola, in open-politica.
    """

    def test_converte_dl_ha_esito_in_evidenza(self, con):
        """L'evidenza porta esito e, se convertito, i giorni.

        La vecchia concatenazione produceva NULL quando `giorni_conversione`
        era NULL (decaduti/restituiti/in esame): l'esito spariva.
        """
        if not _has_table(con, "edges"):
            pytest.skip("edges non presente")
        senza = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'converte_decreto_legge' AND evidence IS NULL
        """).fetchone()[0]
        assert senza == 0, f"{senza} edge converte_decreto_legge senza evidenza"

    def test_converte_dl_un_edge_per_atto(self, con):
        """Un DL con iter Camera+Senato produce un edge per ciascun atto."""
        if not _has_table(con, "edges"):
            pytest.skip("edges non presente")
        n = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'converte_decreto_legge'
              AND target_id NOT LIKE 'senato:%' AND target_id NOT LIKE 'camera:%'
        """).fetchone()[0]
        assert n == 0, f"{n} edge converte_decreto_legge con target non-atto"


class TestChiaveAttoNeiTarget:
    """Nessun arco può puntare a un id Camera senza legislatura.

    `camera:{num}` è ambiguo — lo stesso numero esiste in tutte e 7 le
    legislature — quindi un target così è sempre un call-site dimenticato
    (2026-11: `edges_itercost_atto` azzerava `proposta_cost` Camera, 1.276
    edge persi). Il test guarda entrambi i lati dell'arco, tutte le relazioni.
    """

    def test_nessun_id_camera_senza_legislatura(self, con):
        if not _has_table(con, "edges"):
            pytest.skip("edges non presente")
        bad = con.execute("""
            SELECT COUNT(*) FROM (
                SELECT source_id AS id FROM edges
                UNION ALL
                SELECT target_id FROM edges
            ) WHERE regexp_matches(id, '^camera:[0-9]+$')
        """).fetchone()[0]
        assert bad == 0, f"{bad} id camera senza legislatura negli archi"

    def test_proposta_cost_copre_entrambi_i_rami(self, con):
        """L'iter Costituzionale esiste su Camera e Senato: entrambi gli edge."""
        if not _has_table(con, "edges"):
            pytest.skip("edges non presente")
        rami = dict(con.execute("""
            SELECT split_part(target_id, ':', 1) AS ramo, COUNT(*)
            FROM edges WHERE relation = 'proposta_cost'
            GROUP BY 1
        """).fetchall())
        assert rami.get("camera", 0) >= 1_200, f"proposta_cost Camera: {rami.get('camera', 0)}"
        assert rami.get("senato", 0) >= 1_400, f"proposta_cost Senato: {rami.get('senato', 0)}"
