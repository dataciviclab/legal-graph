"""Invarianti dai feedback data quality (2026-11-13).

H2: `source_year` non deve contenere la legislatura (1..30) sulle relazioni
parlamentari. C3: `converte_decreto_legge` deve dichiarare l'esito.
"""

from __future__ import annotations


def test_source_year_non_e_una_legislatura(con):
    """H2 — 344.316 edge avevano `source_year` 1..30 (legislatura Camera)."""
    n = con.execute(
        "SELECT count(*) FROM edges WHERE source_year BETWEEN 1 AND 30"
    ).fetchone()[0]
    assert n == 0, f"{n} edge con source_year che è una legislatura"


def test_converte_dichiara_esito(con):
    """C3 — 240/933 `converte` non erano conversioni: ora c'è `esito`."""
    cols = {r[0] for r in con.execute("DESCRIBE edges").fetchall()}
    assert "esito" in cols
    rows = dict(
        con.execute(
            "SELECT esito, count(*) FROM edges "
            "WHERE relation = 'converte_decreto_legge' GROUP BY 1"
        ).fetchall()
    )
    assert rows.get("convertito", 0) > 0
    assert sum(rows.get(k, 0) for k in ("decaduto", "in_esame", "restituito")) > 0
