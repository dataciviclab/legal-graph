"""Selezione sorgente mart — `LEGAL_GRAPH_SOURCE` (regressione 2026-11).

L'MCP risolveva sempre il build locale prima di GCS: con un `out/` vecchio
continuava a servire dati superati anche quando il publish GCS era più
fresco. `LEGAL_GRAPH_SOURCE=gcs` rende la scelta esplicita; `auto` mantiene
il comportamento storico (locale → GCS).
"""
from __future__ import annotations

import pytest

from legal_graph import paths


@pytest.fixture(autouse=True)
def _env_pulita(monkeypatch, tmp_path):
    """Isola ogni test da env e da `out/` reali."""
    monkeypatch.delenv(paths.SOURCE_ENV, raising=False)
    monkeypatch.setenv("LEGAL_GRAPH_MART_DIR", str(tmp_path))
    return tmp_path


def _touch(mart_dir, table: str):
    f = mart_dir / f"{table}.parquet"
    f.write_bytes(b"")
    return f


def test_auto_prefers_local_when_present(_env_pulita):
    local = _touch(_env_pulita, "mart_legal_nodes")
    assert paths.resolve("mart_legal_nodes") == local


def test_auto_falls_back_to_gcs_when_local_missing(_env_pulita):
    assert paths.resolve("mart_legal_nodes") == paths.gcs_url("mart_legal_nodes")


def test_gcs_mode_ignores_local_build(_env_pulita, monkeypatch):
    _touch(_env_pulita, "mart_legal_nodes")
    monkeypatch.setenv(paths.SOURCE_ENV, "gcs")
    assert paths.resolve("mart_legal_nodes") == paths.gcs_url("mart_legal_nodes")


def test_local_mode_has_no_remote_fallback(_env_pulita, monkeypatch):
    monkeypatch.setenv(paths.SOURCE_ENV, "local")
    assert paths.resolve("mart_legal_nodes") is None


def test_invalid_mode_raises(monkeypatch):
    monkeypatch.setenv(paths.SOURCE_ENV, "ftp")
    with pytest.raises(ValueError, match="LEGAL_GRAPH_SOURCE"):
        paths.resolve("mart_legal_nodes")


def test_metadata_locale_se_presente(_env_pulita):
    meta = _env_pulita / "metadata.json"
    meta.write_text("{}", encoding="utf-8")
    assert paths.resolve_metadata_file() == meta


def test_metadata_su_gcs_in_modalita_gcs(monkeypatch):
    monkeypatch.setenv(paths.SOURCE_ENV, "gcs")
    url = paths.resolve_metadata_file()
    assert str(url).startswith("https://storage.googleapis.com/")
    assert str(url).endswith("/metadata.json")


def test_metadata_none_in_locale_senza_file(monkeypatch):
    monkeypatch.setenv(paths.SOURCE_ENV, "local")
    assert paths.resolve_metadata_file() is None


@pytest.mark.parametrize(
    "table",
    [
        "mart_legal_nodes",
        "mart_legal_edges",
        "mart_legal_edges_in",
        "mart_legal_coverage",
        "mart_legal_node_metrics",
        "mart_legal_search_keys",
        "mart_legal_node_rel",
        "mart_legal_emend_leg",
        "mart_legal_texts",
        "mart_legal_massime",
    ],
)
def test_all_mart_tables_are_published(table):
    assert table in paths.GCS_MART_TABLES
    assert paths.gcs_url(table).startswith("https://storage.googleapis.com/")


def test_texts_and_massime_resolve_on_gcs(monkeypatch):
    monkeypatch.setenv(paths.SOURCE_ENV, "gcs")
    assert paths.resolve_texts_file() == paths.gcs_url("mart_legal_texts")
    assert paths.resolve_massime_file() == paths.gcs_url("mart_legal_massime")


def test_unpublished_table_raises():
    with pytest.raises(ValueError, match="non pubblicata"):
        paths.gcs_url("mart_legal_nope")
