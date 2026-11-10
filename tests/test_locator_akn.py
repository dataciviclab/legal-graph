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
