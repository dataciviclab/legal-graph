"""Locator AKN (#67): dal `detail` al fragment `art_N` / `art_N-comM`."""

import re
from pathlib import Path

import duckdb

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _pattern() -> str:
    text = (SQL_DIR / "mart_legal_edges.sql").read_text(encoding="utf-8")
    match = re.search(r"regexp_extract\(detail, '(#[^']+)'", text)
    assert match, "espressione locator non trovata in mart_legal_edges.sql"
    return match.group(1)


def test_locator_fragment():
    pattern = _pattern()
    con = duckdb.connect()
    try:
        rows = con.execute(
            "select regexp_extract(detail, '" + pattern + "', 1) from (values "
            "('/akn/it/act/x/!main#art_47-com1-leta'), "
            "('/akn/it/act/x/!main#art_23'), "
            "('src=None;dest=urn:nir:stato:legge:1990-08-07;241')) t(detail)"
        ).fetchall()
    finally:
        con.close()
    assert rows[0][0] == "art_47-com1"
    assert rows[1][0] == "art_23"
    assert rows[2][0] == ""


def test_cte_aggregata_e_ordinata(tmp_path):
    """Il CTE reale (letto dal mart) aggrega, ordina ed esclude i non-`#art_`."""
    import pandas as pd

    pd.DataFrame(
        [
            {"fonte_urn": "A", "target_urn": "B", "rel_type": "citation", "detail": "/akn/...#art_47"},
            {"fonte_urn": "A", "target_urn": "B", "rel_type": "citation", "detail": "/akn/...#art_3"},
            {"fonte_urn": "A", "target_urn": "B", "rel_type": "insertion", "detail": "src=None;dest=urn:x"},
            {"fonte_urn": "A", "target_urn": "C", "rel_type": "citation", "detail": "/akn/...#art_47-com1-leta"},
        ]
    ).to_parquet(tmp_path / "akn.parquet")

    sql = (SQL_DIR / "mart_legal_edges.sql").read_text(encoding="utf-8")
    match = re.search(r"akn_locator AS \((.*?)\n\),", sql, re.DOTALL)
    assert match, "CTE akn_locator non trovato"
    body = match.group(1).replace("{support.akn_relations.path}", str(tmp_path / "akn.parquet"))
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"WITH akn_locator AS ({body}) SELECT target_urn, locators FROM akn_locator ORDER BY target_urn"
        ).fetchall()
    finally:
        con.close()

    assert rows == [("B", "art_3,art_47"), ("C", "art_47-com1")]
