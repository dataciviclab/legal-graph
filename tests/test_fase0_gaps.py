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


class TestCitazioniFuoriCorpus:
    """Le citation AKN verso atti fuori corpus non si perdono (2026-11).

    Prima l'upstream scartava le citation con bersaglio fuori dalle collezioni
    (IC #86) e legal-graph non poteva agganciarle: L. 241/1990 aveva 0 edge.
    """

    def test_riferimento_alla_241(self, con):
        n = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'riferimento'
              AND target_id = 'urn:nir:stato:legge:1990-08-07;241'
        """).fetchone()[0]
        assert n > 0, "L. 241/1990 senza citazioni: bersagli fuori corpus persi"

    def test_target_sempre_nel_grafo(self, con):
        n = con.execute("""
            SELECT COUNT(*) FROM edges e
            WHERE e.relation = 'riferimento'
              AND NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.target_id)
        """).fetchone()[0]
        assert n == 0, f"{n} riferimenti con target dangling"

    def test_citazioni_alla_costituzione(self, con):
        """Le citation all'atto-Costituzione (`...;const`) hanno un nodo loro.

        Non sono article-level: 4.971 righe puntavano a un URN senza numero e
        venivano scartate. Il nodo `costituzione` (atto intero) le raccoglie,
        senza mapparle su `costituzione:art:N`.
        """
        n = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'riferimento' AND target_id = 'costituzione:atto'
        """).fetchone()[0]
        assert n > 4_000, f"citazioni alla Costituzione: {n}"
        nodo = con.execute(
            "SELECT COUNT(*) FROM nodes WHERE id = 'costituzione:atto'"
        ).fetchone()[0]
        assert nodo == 1, f"nodo 'costituzione' presente {nodo} volte"


class TestNodiCatalogo:
    """Nodi `source='catalogo'` per gli atti citati assenti dal corpus.

    Costruiti da `atti_index` filtrato sulle citazioni non risolte: solo
    identità (nessun testo), quindi `source` dedicato e fuori dagli invarianti
    dei nodi di corpus.
    """

    def test_cardinalita(self, con):
        n = con.execute("""
            SELECT COUNT(*) FROM nodes WHERE source = 'catalogo'
        """).fetchone()[0]
        assert n > 10_000, f"nodi catalogo attesi >10k, trovati {n}"

    def test_nessun_doppione_con_normativa(self, con):
        n = con.execute("""
            SELECT COUNT(*) FROM nodes c
            WHERE c.source = 'catalogo'
              AND EXISTS (
                  SELECT 1 FROM nodes m
                  WHERE m.id = c.id AND m.source <> 'catalogo'
              )
        """).fetchone()[0]
        assert n == 0, f"{n} id catalogo duplicati in un'altra fonte"

    def test_quasi_tutti_con_un_edge(self, con):
        """Il nodo nasce da una citazione: deve avere l'edge.

        Dopo la canonicalizzazione URN di italia-corpus (#121) i residui sono
        223 su 10.852 (2,1%): citazioni ad atti che **non sono nel catalogo**
        Normattiva (`decretolegislativo:2010-12-13;129` dove in `atti_index`
        c'è solo `legge:2010-08-13;129`) più la guardia temporale. Prima
        erano 763 (7,0%) per gli URN non canonici.
        """
        tot, senza = con.execute("""
            SELECT
                COUNT(*),
                COUNT(*) FILTER (
                    WHERE NOT EXISTS (
                        SELECT 1 FROM edges e WHERE e.target_id = n.id
                    )
                )
            FROM nodes n WHERE n.source = 'catalogo'
        """).fetchone()
        assert senza / tot < 0.05, f"{senza}/{tot} nodi catalogo senza edge"

    def test_materia_dal_catalogo(self, con):
        """`materia` arriva da atti_index: senza, i nodi sono invisibili ai
        filtri tematici di `legal_search`."""
        tot, senza, non_altro = con.execute("""
            SELECT
                COUNT(*),
                COUNT(*) FILTER (WHERE materia IS NULL OR materia = ''),
                COUNT(*) FILTER (WHERE materia <> 'altro')
            FROM nodes WHERE source = 'catalogo'
        """).fetchone()
        assert senza == 0, f"{senza} nodi catalogo senza materia"
        assert non_altro / tot > 0.50, (
            f"solo {non_altro}/{tot} nodi catalogo con materia specifica"
        )


class TestDeputati:
    def test_anagrafica_coperta(self, con):
        """Deputati della **Repubblica 13-19** (perimetro del grafo).

        Prima erano 11.277: includevano Regno (1-30) e Costituente, tutti
        nodi isolati (le relazioni `firmatario`/`relatore` coprono 13-19).
        Dal 2026-11 il perimetro è quello di `open-politica/persone`: 2.732
        deputati. Se scende sotto 2.500 è una regressione del compose.
        """
        n = con.execute("""
            SELECT COUNT(*) FROM nodes WHERE id LIKE 'deputato:%'
        """).fetchone()[0]
        assert n >= 2_500, f"Deputati attesi >=2.500 (13-19), trovati {n}"
        # nessun deputato fuori perimetro: `anno` è la legislatura
        antichi = con.execute("""
            SELECT COUNT(*) FROM nodes
            WHERE id LIKE 'deputato:%' AND (anno IS NULL OR anno NOT BETWEEN 13 AND 19)
        """).fetchone()[0]
        assert antichi == 0, f"{antichi} deputati fuori da Leg13-19"


class TestTestoAtto:
    def test_no_dangling(self, con):
        n = con.execute("""
            SELECT COUNT(*) FROM edges e
            WHERE e.relation = 'testo_atto'
              AND NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.target_id)
        """).fetchone()[0]
        assert n == 0, f"{n} target testo_atto dangling"
