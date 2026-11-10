"""`norma_data` dai mart di costituzione-italiana (#42): dd/mm/yyyy o ISO.

La regressione che ha portato a 0 edge `impugna` era un cambio di formato non
dichiarato: il test legge l'espressione reale dai mart e la valuta sui tre
casi (dd/mm/yyyy, ISO, stringa vuota).
"""

import re
from pathlib import Path

import duckdb
import pytest

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _expr(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"\(CASE WHEN regexp_matches\(norma_data.*?END\)", text, re.DOTALL)
    assert match, f"espressione data non trovata in {path.name}"
    return match.group(0)


@pytest.mark.parametrize("name", ["mart_legal_edges.sql", "mart_legal_nodes.sql"])
def test_norma_data_formati(name):
    expr = _expr(SQL_DIR / name)
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"select norma_data, {expr} as d from (values "
            "('18/06/1931'), ('1931-06-18'), (''), (NULL)) t(norma_data)"
        ).fetchall()
    finally:
        con.close()
    assert rows[0][1].isoformat() == "1931-06-18"
    assert rows[1][1].isoformat() == "1931-06-18"
    assert rows[2][1] is None
    assert rows[3][1] is None
