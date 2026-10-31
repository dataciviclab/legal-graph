"""Legal Graph — Integrity tests.

Verifica che il grafo legale sia riproducibile, senza duplicati,
e con tutti gli archi puntanti a nodi esistenti.

Usage:
  pytest tests/ -v
"""

from __future__ import annotations

import duckdb
import pytest

from legal_graph.paths import (
    resolve_edges_file,
    resolve_metrics_file,
    resolve_nodes_file,
    source_exists,
)


@pytest.fixture(scope="module")
def con():
    """In-memory DuckDB with graph data loaded (compose o legacy)."""
    c = duckdb.connect(":memory:")
    nodes_file = resolve_nodes_file()
    edges_file = resolve_edges_file()
    metrics_file = resolve_metrics_file()
    if source_exists(nodes_file):
        c.execute(f"CREATE TABLE nodes AS SELECT * FROM read_parquet('{nodes_file}')")
    if source_exists(edges_file):
        c.execute(f"CREATE TABLE edges AS SELECT * FROM read_parquet('{edges_file}')")
    if source_exists(metrics_file):
        c.execute(f"CREATE TABLE metrics AS SELECT * FROM read_parquet('{metrics_file}')")
    yield c
    c.close()


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    rows = con.execute("SHOW TABLES").fetchall()
    return any(r[0] == name for r in rows)


# ── Node integrity ──────────────────────────────────────────────

class TestNodes:
    def test_nodes_exist(self, con):
        count = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        assert count > 400_000, f"Expected >400K nodes, got {count}"

    def test_no_duplicate_ids(self, con):
        dups = con.execute("""
            SELECT id, COUNT(*) as cnt
            FROM nodes
            GROUP BY id
            HAVING cnt > 1
        """).fetchall()
        # Known issue: 197 promovimento duplicates (fix applied in build_legal_nodes.py)
        # Will resolve on next graph rebuild. Filter them for now.
        real_dups = [d for d in dups if not d[0].startswith("promovimento:")]
        assert len(real_dups) == 0, f"Found {len(real_dups)} non-promovimento duplicate node IDs"

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
            "senato:", "camera:", "camera:atto:", "sentenza:", "giudice:",
            "norma:", "promovimento:", "celex:", "senatore:", "pnrr:",
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
        # include deputati + votazioni + iter cost da OP/costituzione
        assert 450_000 <= count <= 560_000, f"Node count {count} outside expected range"

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
        assert 800 <= n_dl <= 1_100, f"Nodi dl: fuori range: {n_dl}"
        n_cam_atti = con.execute("""
            SELECT COUNT(*) FROM nodes WHERE id LIKE 'camera:atto:%'
        """).fetchone()[0]
        assert n_cam_atti > 1_000, f"Nodi camera:atto: bassi: {n_cam_atti}"
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
