"""Legal Graph — bilancio di copertura (mart_legal_coverage).

Il compose scarta le righe che non riesce a collegare: senza questo mart il
grafo sembra completo (0 dangling) mentre una parte delle citazioni upstream
non entra. Questi test proteggono il contratto della tabella e il fatto che
non diverga dal grafo pubblicato.

Usage:
  pytest tests/ -q
"""

from __future__ import annotations

import duckdb
import pytest

from legal_graph.paths import (
    resolve_coverage_file,
    resolve_edges_file,
    source_exists,
)


@pytest.fixture(scope="module")
def con():
    """DuckDB con coverage + edges materializzati (skip se mart assente)."""
    coverage = resolve_coverage_file()
    edges = resolve_edges_file()
    if not source_exists(coverage) or not source_exists(edges):
        pytest.skip("mart coverage/edges non presenti (esegui make run)")
    c = duckdb.connect(":memory:")
    c.execute(f"CREATE TABLE coverage AS SELECT * FROM read_parquet('{coverage}')")
    c.execute(f"CREATE TABLE edges AS SELECT * FROM read_parquet('{edges}')")
    yield c
    c.close()


class TestCoverageContract:
    def test_columns(self, con):
        cols = {r[0] for r in con.execute("DESCRIBE coverage").fetchall()}
        assert {
            "relation",
            "upstream",
            "upstream_rows",
            "natural_pairs",
            "edges",
            "edge_pct",
            "note",
            "formulaic_edges",
            "formulaic_pct",
            "reading_clause_edges",
            "missing_evidence_edges",
            "missing_evidence_pct",
        } <= cols

    def test_one_row_per_relation(self, con):
        dups = con.execute("""
            SELECT relation, COUNT(*) FROM coverage GROUP BY 1 HAVING COUNT(*) > 1
        """).fetchall()
        assert not dups, f"relation duplicate in coverage: {dups}"

    def test_no_relation_missing(self, con):
        """Ogni relazione del grafo deve comparire nel bilancio."""
        missing = con.execute("""
            SELECT DISTINCT e.relation
            FROM edges e
            LEFT JOIN coverage c ON c.relation = e.relation
            WHERE c.relation IS NULL
        """).fetchall()
        assert not missing, f"relazioni senza bilancio: {[m[0] for m in missing]}"

    def test_edges_match_graph(self, con):
        """Il bilancio non può divergere dal mart: edges = COUNT(*) per relazione."""
        diff = con.execute("""
            SELECT c.relation, c.edges, e.n
            FROM coverage c
            JOIN (SELECT relation, COUNT(*) AS n FROM edges GROUP BY 1) e
              ON e.relation = c.relation
            WHERE c.edges <> e.n
        """).fetchall()
        assert not diff, f"coverage divergente da edges: {diff[:5]}"

    def test_edge_pct_range(self, con):
        bad = con.execute("""
            SELECT relation, edge_pct FROM coverage
            WHERE edge_pct IS NOT NULL AND (edge_pct < 0 OR edge_pct > 100)
        """).fetchall()
        assert not bad, f"edge_pct fuori range: {bad}"


class TestFunnel:
    """Le relazioni con un funnel reale devono avere numeri propri."""

    @pytest.mark.parametrize(
        "relation",
        ["riferimento", "abroga", "diventa_legge", "invoca_parametro", "emendamento"],
    
    )
    def test_funnel_declared(self, con, relation):
        row = con.execute(
            """SELECT upstream, upstream_rows, natural_pairs, edge_pct
               FROM coverage WHERE relation = ?""",
            [relation],
        ).fetchone()
        assert row is not None, f"{relation} assente dal bilancio"
        upstream, upstream_rows, natural_pairs, edge_pct = row
        assert upstream, f"{relation}: upstream non dichiarato"
        assert upstream_rows and upstream_rows > 0, f"{relation}: upstream_rows vuoto"
        assert natural_pairs and natural_pairs > 0, f"{relation}: natural_pairs vuoto"
        assert natural_pairs <= upstream_rows, (
            f"{relation}: coppie distinte ({natural_pairs}) > righe ({upstream_rows})"
        )
        assert edge_pct is not None, f"{relation}: edge_pct assente"

    def test_emendamenti_quota_collegati(self, con):
        """Quota emendamenti collegati all'atto, sul denominatore giusto.

        `emendamento` non è 1:1: gli emendamenti distinti sono
        (legislatura, atto, emend_id) = 789.361, mentre i nodi sono 248.127
        perché `emend_id` è il numero *dentro* l'atto ("1.1" esiste in
        centinaia di atti). Contare i nodi dava 9% di orfani invece di 15,7%.
        """
        pct = con.execute(
            "SELECT edge_pct FROM coverage WHERE relation = 'emendamento'"
        ).fetchone()[0]
        assert pct is not None, "emendamento senza funnel dichiarato"
        assert pct >= 80, f"emendamenti collegati al {pct}% (atteso >=80%)"

    def test_formulaici_dichiarati(self, con):
        """Gli edge formulaici restano nel conteggio, ma sono dichiarati.

        Il funnel non deve sembrare migliore solo perché un edge è etichettato:
        `edges` è il lordo, `formulaic_edges` dice quanti sono di preambolo.
        """
        edges, formulaic = con.execute(
            "SELECT edges, formulaic_edges FROM coverage WHERE relation = 'riferimento'"
        ).fetchone()
        ricalcolati = con.execute(
            "SELECT COUNT(*) FROM edges WHERE is_formulaic"
        ).fetchone()[0]
        assert formulaic == ricalcolati, (formulaic, ricalcolati)
        assert 0 < formulaic < edges, (formulaic, edges)

        missing = con.execute(
            "SELECT missing_evidence_edges FROM coverage WHERE relation = 'riferimento'"
        ).fetchone()[0]
        ricalcolati_missing = con.execute(
            "SELECT COUNT(*) FROM edges WHERE evidence_kind = 'missing'"
        ).fetchone()[0]
        assert missing == ricalcolati_missing, (missing, ricalcolati_missing)

        reading = con.execute(
            "SELECT reading_clause_edges FROM coverage WHERE relation = 'riferimento'"
        ).fetchone()[0]
        ricalcolate = con.execute(
            "SELECT COUNT(*) FROM edges WHERE is_reading_clause"
        ).fetchone()[0]
        assert reading == ricalcolate, (reading, ricalcolate)

    def test_citation_funnel_not_collapsing(self, con):
        """Guardrail: le citazioni non devono perdere più della metà delle coppie.

        Se il mapping URN o la guardia temporale peggiorano, questo test lo
        segnala prima che il calo arrivi a un'analisi pubblica.
        """
        pct = con.execute(
            "SELECT edge_pct FROM coverage WHERE relation = 'riferimento'"
        ).fetchone()[0]
        assert pct >= 50, f"copertura citazioni al {pct}% (atteso >=50%)"

    def test_only_declared_funnels_have_denominator(self, con):
        """Nessun denominatore inventato sulle relazioni 1:1."""
        rels = {
            r[0]
            for r in con.execute(
                "SELECT relation FROM coverage WHERE upstream_rows IS NOT NULL"
            ).fetchall()
        }
        extra = rels - {
            "riferimento",
            "abroga",
            "diventa_legge",
            "invoca_parametro",
            "emendamento",
        }
        assert not extra, f"relazioni con denominatore non dichiarato: {extra}"

    @pytest.mark.parametrize(
        "relation,baseline",
        [
            ("riferimento", 200_080),
            ("abroga", 8_374),
            ("diventa_legge", 7_478),
            ("emendamento", 763_690),
        ],
    )
    def test_upstream_numerators_stable(self, con, relation, baseline):
        """Drift delle righe upstream: banda ±20% sui numeratori dichiarati.

        Il funnel replica tre filtri del compose (`r.risolto`, guardia
        temporale, join DDL/leggi). Se il compose cambia un filtro e il funnel
        no, `upstream_rows` resta fermo mentre `edges` si muove: il rapporto
        scivola senza che nessun test su `edges` lo veda. Una banda (non il
        valore esatto) intercetta il drift strutturale senza fallire a ogni
        aggiornamento legittimo della fonte.
        """
        valore = con.execute(
            "SELECT upstream_rows FROM coverage WHERE relation = ?", [relation]
        ).fetchone()[0]
        basso, alto = baseline * 0.8, baseline * 1.2
        assert basso <= valore <= alto, (
            f"{relation}: upstream_rows {valore} fuori banda [{basso:.0f}, {alto:.0f}] "
            f"(baseline {baseline}) — il filtro del funnel è cambiato nel compose?"
        )
