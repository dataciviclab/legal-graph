"""Freschezza degli input: lettura dei metadata e tabella pubblicata.

Il contratto è doppio: `_as_of` deve leggere il `metadata.json` accanto al
parquet (locale o remoto) senza mai sollevare, e la tabella scritta deve essere
rileggibile dal consumer (`legal_catena`, dashboard, guard).
"""

from __future__ import annotations

import json
from datetime import datetime

import duckdb
import pytest

from legal_graph.freshness import _as_of, write
from legal_graph.mcp.chain import _SOGLIA_FRESCHEZZA, _impl_catena
from legal_graph.paths import resolve_freshness_file


def test_as_of_legge_il_metadata_locale(tmp_path):
    parquet = tmp_path / "x.parquet"
    parquet.write_bytes(b"")
    (tmp_path / "metadata.json").write_text(
        json.dumps({"timestamp_utc": "2026-11-04T10:00:00+00:00"})
    )
    got = _as_of(str(parquet))
    assert got == datetime.fromisoformat("2026-11-04T10:00:00+00:00")


def test_as_of_senza_metadata_ritorna_none(tmp_path):
    parquet = tmp_path / "x.parquet"
    parquet.write_bytes(b"")
    assert _as_of(str(parquet)) is None


def test_as_of_json_malformato_ritorna_none(tmp_path):
    parquet = tmp_path / "x.parquet"
    parquet.write_bytes(b"")
    (tmp_path / "metadata.json").write_text("{non json")
    assert _as_of(str(parquet)) is None


def test_tabella_scritta_e_rileggibile(tmp_path):
    out = tmp_path / "f.parquet"
    rows = [
        ("senato_ddl", "senato-akn", "senato_ddl", "clean", "2026",
         datetime.fromisoformat("2026-11-04T10:00:00+00:00"), 20, "metadata_json", ""),
        ("testi_articoli", "italia-corpus", "testi_articoli", "mart", "2026",
         None, None, "assente", "nessun metadata.json accanto al parquet"),
    ]
    write(rows, out)
    con = duckdb.connect()
    got = con.execute(
        "SELECT support, age_days, fonte FROM read_parquet(?) ORDER BY support",
        [str(out)],
    ).fetchall()
    assert got == [
        ("senato_ddl", 20, "metadata_json"),
        ("testi_articoli", None, "assente"),
    ]


def test_catena_dichiara_letà_dei_dati(con):
    """Con la tabella presente, `legal_catena` espone età e data dei support."""
    if resolve_freshness_file() is None:
        pytest.skip("mart_legal_freshness assente — esegui `make freshness`")
    res = _impl_catena("urn:nir:stato:legge:1997-03-15;59", direzione="monte")
    if "dati_age_days" not in res:
        pytest.skip("nessun support mappato per le relazioni della catena")
    assert isinstance(res["dati_age_days"], int)
    assert res["dati_as_of"].count("-") == 2
    if res["dati_age_days"] > _SOGLIA_FRESCHEZZA:
        assert any("poggia su dati di" in n for n in res["note"])
