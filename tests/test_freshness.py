"""Freschezza degli input: lettura dei metadata e tabella pubblicata.

Il contratto è doppio: `_as_of` deve leggere il `metadata.json` accanto al
parquet (locale o remoto) senza mai sollevare, e la tabella scritta deve essere
rileggibile dal consumer (`legal_catena`, dashboard, guard).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import duckdb
import pytest

from legal_graph.freshness import SOGLIA_GIORNI, _as_of, write
from legal_graph.mcp.chain import _impl_catena
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


def test_as_of_naive_viene_normalizzato_a_utc(tmp_path):
    """Un timestamp senza fuso non deve far esplodere `now - as_of`."""
    parquet = tmp_path / "x.parquet"
    parquet.write_bytes(b"")
    (tmp_path / "metadata.json").write_text(
        json.dumps({"timestamp_utc": "2026-11-04T10:00:00"})
    )
    got = _as_of(str(parquet))
    assert got is not None and got.tzinfo is not None


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


def test_mappa_support_segue_l_sql():
    """La mappa deve seguire l'SQL: `converte` nasce da atti+dl, non da normativa."""
    from legal_graph.mcp.chain import _REL_SUPPORT

    # `gu_acts` sostiene `esito_gu` sullo stesso arco (legal-graph#91): senza,
    # una catena di conversione potrebbe poggiare su una Gazzetta vecchia.
    assert _REL_SUPPORT["converte_decreto_legge"] == ("atti", "dl", "gu_acts")
    assert "atti_api" in _REL_SUPPORT["attua_delega"]


def test_eta_ricalcolata_da_as_of(tmp_path, monkeypatch):
    """`age_days` nella tabella è del build: l'età si ricalcola alla lettura."""
    from datetime import timedelta

    from legal_graph.mcp import chain

    vecchio = datetime.now(UTC) - timedelta(days=30)
    out = tmp_path / "f.parquet"
    # age_days=0 volutamente sbagliato: se il codice lo riusasse, direbbe 0.
    write([("atti", "", "", "clean", "2026", vecchio, 0, "metadata_json", "")], out)
    monkeypatch.setattr(chain, "resolve_freshness_file", lambda: out)

    got = chain._dati_freschezza(duckdb.connect(), ["converte_decreto_legge"])
    assert got["dati_age_days"] >= 29


def test_catena_dichiara_letà_dei_dati(con):
    """Con la tabella presente, `legal_catena` espone età e data dei support."""
    if resolve_freshness_file() is None:
        pytest.skip("mart_legal_freshness assente — esegui `make freshness`")
    res = _impl_catena("urn:nir:stato:legge:1997-03-15;59", direzione="monte")
    if "dati_age_days" not in res:
        pytest.skip("nessun support mappato per le relazioni della catena")
    assert isinstance(res["dati_age_days"], int)
    assert res["dati_as_of"].count("-") == 2
    if res["dati_age_days"] > SOGLIA_GIORNI:
        assert any("poggia su dati di" in n for n in res["note"])
