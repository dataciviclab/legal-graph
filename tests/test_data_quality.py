"""Invarianti dai feedback data quality (2026-11-13).

H2: `source_year` non deve contenere la legislatura; la legislatura resta in
`legislatura`. C3: `converte_decreto_legge` dichiara l'esito.
"""

from __future__ import annotations

_PROC = ("firmatario", "vota", "proposta_cost", "diventa_revisione")
_ESITI = {"convertito", "decaduto", "in_esame", "restituito"}


def test_source_year_non_e_una_legislatura(con):
    """H2 — le relazioni procedurali non hanno anno, e gli anni sono plausibili."""
    n = con.execute(
        f"SELECT count(*) FROM edges WHERE relation IN {_PROC} AND source_year IS NOT NULL"
    ).fetchone()[0]
    assert n == 0, f"{n} edge procedurali con source_year"

    # Il corpus parte dal Regno (1861): niente anni 1..30 né 2100+.
    bad = con.execute(
        "SELECT count(*) FROM edges WHERE source_year IS NOT NULL "
        "AND (source_year < 1861 OR source_year > 2100)"
    ).fetchone()[0]
    assert bad == 0, f"{bad} edge con anno fuori 1861..2100"


def test_legislatura_recuperata(con):
    """H2 — la legislatura non è persa: colonna dedicata, range Repubblica."""
    rows = dict(
        con.execute(
            f"SELECT relation, count(legislatura) FROM edges "
            f"WHERE relation IN {_PROC} GROUP BY 1"
        ).fetchall()
    )
    assert rows.get("firmatario", 0) > 300_000
    assert rows.get("vota", 0) > 20_000
    assert rows.get("proposta_cost", 0) > 1_000
    assert rows.get("diventa_revisione", 0) > 100

    bad = con.execute(
        "SELECT count(*) FROM edges WHERE legislatura IS NOT NULL "
        "AND (legislatura < 1 OR legislatura > 19)"
    ).fetchone()[0]
    assert bad == 0, f"{bad} edge con legislatura fuori 1..19"


def test_converte_dichiara_esito(con):
    """C3 — `esito` presente su tutti, dominio noto, coppie univoche."""
    cols = {r[0] for r in con.execute("DESCRIBE edges").fetchall()}
    assert {"esito", "legislatura"} <= cols

    nulli = con.execute(
        "SELECT count(*) FROM edges "
        "WHERE relation = 'converte_decreto_legge' AND esito IS NULL"
    ).fetchone()[0]
    assert nulli == 0

    rows = dict(
        con.execute(
            "SELECT esito, count(*) FROM edges "
            "WHERE relation = 'converte_decreto_legge' GROUP BY 1"
        ).fetchall()
    )
    assert set(rows) <= _ESITI, rows
    assert rows.get("convertito", 0) > sum(
        v for k, v in rows.items() if k != "convertito"
    ), rows

    dup = con.execute(
        "SELECT count(*) - count(DISTINCT source_id || '|' || target_id) "
        "FROM edges WHERE relation = 'converte_decreto_legge'"
    ).fetchone()[0]
    assert dup == 0, f"{dup} coppie DL→atto duplicate"


def test_chain_espone_esito(con):
    """C3 — l'esito arriva alla vista `chain`, non solo a `legal_query`."""
    import legal_graph.mcp_server as m

    src = con.execute(
        "SELECT source_id FROM edges WHERE relation = 'converte_decreto_legge' LIMIT 1"
    ).fetchone()[0]
    node = m.legal_node(src, view="chain", depth=1)
    fwd = [
        e for e in node.get("chain_forward", [])
        if e["relation"] == "converte_decreto_legge"
    ]
    assert fwd, node.get("error") or "nessun converte nella chain"
    assert fwd[0]["esito"] in _ESITI
