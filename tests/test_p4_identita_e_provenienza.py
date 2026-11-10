"""P4 — firmatari Senato dai deputati e `abroga` con evidenza.

`senato_firmatari` porta due namespace di identità: `senatore_id` e
`deputato_url`. Il secondo era scartato (`WHERE senatore_id IS NOT NULL`):
3.210 DDL restavano senza firmatari e 1.409 atti Senato isolati.

`abroga` dal mart URN di IC è una regex con quote, ma era marcato `inferred`
perché la mappatura guardava solo il prefisso `akn:`.
"""

from __future__ import annotations


class TestP4:
    def test_firmatari_deputati_in_senato(self, con):
        n = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'firmatario'
              AND source_id LIKE 'deputato:%'
              AND target_id LIKE 'senato:%'
        """).fetchone()[0]
        assert n > 15_000, f"firmatari deputato→senato: {n} (attesi >15.000)"

    def test_deputato_firmatario_ha_nodo_persona(self, con):
        bad = con.execute("""
            SELECT COUNT(*) FROM edges e
            WHERE e.relation = 'firmatario'
              AND e.source_id LIKE 'deputato:%'
              AND e.target_id LIKE 'senato:%'
              AND NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.source_id)
        """).fetchone()[0]
        assert bad == 0, f"{bad} firmatari con persona senza nodo"

    def test_evidence_distingue_il_namespace(self, con):
        n = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'firmatario'
              AND source_id LIKE 'deputato:%'
              AND target_id LIKE 'senato:%'
              AND evidence IN ('firmatario_deputato', 'primo_firmatario')
        """).fetchone()[0]
        assert n > 15_000, n

    def test_atto_senato_prima_isolato_ora_ha_firmatario(self, con):
        """`senato:55144` era tra i 22.340 atti Senato isolati prima di P4."""
        n = con.execute(
            "SELECT COUNT(*) FROM edges "
            "WHERE relation = 'firmatario' AND target_id = 'senato:55144'"
        ).fetchone()[0]
        assert n > 0

    def test_abroga_con_quote_e_estratto(self, con):
        rows = dict(
            con.execute(
                "SELECT assertion_type, COUNT(*) FROM edges "
                "WHERE relation = 'abroga' GROUP BY 1"
            ).fetchall()
        )
        assert rows.get("extracted", 0) > 3_000, rows

    def test_nessun_abroga_con_quote_marcato_inferred(self, con):
        bad = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'abroga'
              AND assertion_type = 'inferred'
              AND prov_evidence_kind = 'quote'
        """).fetchone()[0]
        assert bad == 0, f"{bad} abroga con quote ma assertion=inferred"

    def test_abroga_method_segue_la_fonte(self, con):
        """`abroga` ha due fonti: regex sul testo (quote) e lifecycle AKN (label).

        Il metodo non può essere uniforme: 3.364 edge arrivano dal mart URN di
        italia-corpus con una quote, 369 dal lifecycle AKN con un locator.
        """
        rows = dict(
            con.execute(
                "SELECT method, COUNT(*) FROM edges WHERE relation = 'abroga' GROUP BY 1"
            ).fetchall()
        )
        assert rows.get("regex", 0) > 3_000, rows
        assert rows.get("akn_lifecycle", 0) > 300, rows
        quote_akn = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'abroga' AND method = 'akn_lifecycle'
              AND prov_evidence_kind = 'quote'
        """).fetchone()[0]
        assert quote_akn == 0, f"{quote_akn} abroga regex dichiarate akn_lifecycle"
        regex_label = con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE relation = 'abroga' AND method = 'regex'
              AND prov_evidence_kind = 'label'
        """).fetchone()[0]
        assert regex_label == 0, f"{regex_label} abroga AKN dichiarate regex"
