"""Locator AKN (#67, #142): `articolo` prima, fallback sul `detail`."""

import re
from pathlib import Path

import duckdb

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _pattern() -> str:
    text = (SQL_DIR / "mart_legal_edges.sql").read_text(encoding="utf-8")
    match = re.search(r"regexp_extract\(\s*detail,\s*'(#[^']+)'", text)
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
            "('/akn/it/act/x/!main#art_47-bis'), "
            "('/akn/it/act/x/!main#art_5-terdecies'), "
            "('src=None;dest=urn:nir:stato:legge:1990-08-07;241')) t(detail)"
        ).fetchall()
    finally:
        con.close()
    assert rows[0][0] == "art_47-com1"
    assert rows[1][0] == "art_23"
    # il suffisso cambia l'identità: il fallback non deve troncarlo
    assert rows[2][0] == "art_47-bis"
    assert rows[3][0] == "art_5-terdecies"
    assert rows[4][0] == ""


def _cte_body(tmp_path) -> str:
    sql = (SQL_DIR / "mart_legal_edges.sql").read_text(encoding="utf-8")
    match = re.search(r"akn_locator AS \((.*?)\n\),", sql, re.DOTALL)
    assert match, "CTE akn_locator non trovato"
    return match.group(1).replace(
        "{support.akn_relations.path}", str(tmp_path / "akn.parquet")
    )


def test_cte_aggregata_e_ordinata(tmp_path):
    """Il CTE reale: `articolo` (anche liste), fallback `detail`, esclusioni."""
    import pandas as pd

    pd.DataFrame(
        [
            # fallback sul detail (righe/citazioni senza `articolo`)
            {"fonte_urn": "A", "target_urn": "B", "rel_type": "citation",
             "detail": "/akn/...#art_47", "articolo": None},
            {"fonte_urn": "A", "target_urn": "B", "rel_type": "citation",
             "detail": "/akn/...#art_3", "articolo": "art_3"},
            # lista di articoli sullo stesso edge
            {"fonte_urn": "A", "target_urn": "B", "rel_type": "insertion",
             "detail": "src=None;dest=urn:x", "articolo": "art_3,art_47"},
            # suffisso conservato
            {"fonte_urn": "A", "target_urn": "C", "rel_type": "citation",
             "detail": "/akn/...#art_47-bis", "articolo": "art_47-bis"},
            # nessuna delle due fonti → esclusa
            {"fonte_urn": "A", "target_urn": "C", "rel_type": "citation",
             "detail": "src=None;dest=urn:x", "articolo": None},
            # repeal: solo `articolo`, il detail non ha fragment
            {"fonte_urn": "A", "target_urn": "D", "rel_type": "repeal",
             "detail": "src=None;dest=urn:y", "articolo": "art_8-ter"},
        ]
    ).to_parquet(tmp_path / "akn.parquet")

    body = _cte_body(tmp_path)
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"WITH akn_locator AS ({body}) "
            "SELECT target_urn, rel_type, locators FROM akn_locator "
            "ORDER BY target_urn, rel_type"
        ).fetchall()
    finally:
        con.close()

    assert rows == [
        ("B", "citation", "art_3,art_47"),
        ("B", "insertion", "art_3,art_47"),
        ("C", "citation", "art_47-bis"),
        ("D", "repeal", "art_8-ter"),
    ]
