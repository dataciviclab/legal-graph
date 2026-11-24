"""Esito formale della conversione dalla Gazzetta (legal-graph#91).

`esito` è lo stato parlamentare; `esito_gu` è il fatto pubblicato dalla GU.
L'issue aveva isolato tre DL del 2026 `in_esame` con un atto di mancata
conversione già in Gazzetta; il join ne ha trovati altri tre nel verso
opposto (la GU dice convertito, l'iter dice `in_esame`).
"""

from __future__ import annotations

import pytest

from legal_graph.mcp import db

MANCATA = [
    ("urn:nir:stato:decreto.legge:2026-04-03;42", "in_esame"),
    ("urn:nir:stato:decreto.legge:2026-05-22;89", "in_esame"),
    ("urn:nir:stato:decreto.legge:2026-08-05;139", "in_esame"),
    ("urn:nir:stato:decreto.legge:2026-08-26;153", "decaduto"),
]

CONVERTITA_MA_IN_ESAME = [
    "urn:nir:stato:decreto.legge:2026-09-10;157",
    "urn:nir:stato:decreto.legge:2026-09-17;162",
    "urn:nir:stato:decreto.legge:2026-09-28;167",
]


@pytest.fixture(scope="module")
def edges_gu(con):
    """Skip su un mart costruito prima di `esito_gu` (colonna assente)."""
    if not db._has_col(con, "edges", "esito_gu"):
        pytest.skip("mart senza esito_gu: build precedente a legal-graph#91")
    return con


@pytest.mark.parametrize("urn,esito", MANCATA)
def test_mancata_conversione(edges_gu, urn, esito):
    rows = edges_gu.execute(
        "SELECT esito, esito_gu FROM edges "
        "WHERE relation='converte_decreto_legge' AND source_id = ?",
        [urn],
    ).fetchall()
    assert rows, urn
    for iter_esito, gu in rows:
        assert iter_esito == esito, (urn, iter_esito)
        assert gu == "mancata_conversione", (urn, gu)


@pytest.mark.parametrize("urn", CONVERTITA_MA_IN_ESAME)
def test_gu_corregge_iter_fermo(edges_gu, urn):
    """La GU dice convertito mentre l'iter è ancora `in_esame`."""
    rows = edges_gu.execute(
        "SELECT esito, esito_gu FROM edges "
        "WHERE relation='converte_decreto_legge' AND source_id = ?",
        [urn],
    ).fetchall()
    assert rows, urn
    for iter_esito, gu in rows:
        assert iter_esito == "in_esame", (urn, iter_esito)
        assert gu == "convertito", (urn, gu)


def test_copertura_conversioni_dal_2024(edges_gu):
    """Dal 2024 la GU copre; un `convertito` senza esito_gu è un buco di parse."""
    n = edges_gu.execute(
        """
        SELECT count(*) FROM edges
        WHERE relation='converte_decreto_legge' AND esito='convertito'
          AND TRY_CAST(regexp_extract(source_id, ':(\\d{4})-', 1) AS INT) >= 2024
          AND esito_gu IS NULL
        """
    ).fetchone()[0]
    assert n == 0, f"{n} conversioni dal 2024 senza atto in Gazzetta"


def test_prima_del_2023_resta_null(edges_gu):
    """La GU pubblica dal 2024: gli atti precedenti non hanno esito formale."""
    n = edges_gu.execute(
        """
        SELECT count(*) FROM edges
        WHERE relation='converte_decreto_legge'
          AND TRY_CAST(regexp_extract(source_id, ':(\\d{4})-', 1) AS INT) <= 2022
          AND esito_gu IS NOT NULL
        """
    ).fetchone()[0]
    assert n == 0


def test_nessun_fan_out(edges_gu):
    tot, distinte = edges_gu.execute(
        "SELECT count(*), count(DISTINCT (source_id, target_id)) FROM edges "
        "WHERE relation='converte_decreto_legge'"
    ).fetchone()
    assert tot == distinte


def test_esito_gu_solo_sulle_conversioni(edges_gu):
    n = edges_gu.execute(
        "SELECT count(*) FROM edges "
        "WHERE relation <> 'converte_decreto_legge' AND esito_gu IS NOT NULL"
    ).fetchone()[0]
    assert n == 0
