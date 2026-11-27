"""Gate delle tabelle pubblicate, per dataset (regressione 2026-11-24).

Dopo lo split `mart_legal_search_terms` vive in `legal_index`. Il gate in
`_bootstrap_views` usava un elenco unico preso dal `metadata.json` del grafo:
la tabella non compariva, la view non veniva creata e la ricerca per tema
perdeva l'indice. L'ha preso la pipeline (`test_search_terms` con
`LEGAL_GRAPH_REQUIRE_MART=1`); qui il contratto è fissato in isolamento, senza
dipendere dal mart pubblicato.
"""

from __future__ import annotations

import json

import duckdb
import pytest

from legal_graph.mcp import db


def _metadata(tmp_path, name: str, tables: list[str]):
    path = tmp_path / name
    path.write_text(json.dumps({"tables": [{"name": t} for t in tables]}))
    return path


def test_gate_per_dataset_non_unisce_i_publish(tmp_path, monkeypatch):
    con = duckdb.connect()
    graph = _metadata(tmp_path, "graph.json", ["mart_legal_nodes"])
    texts = _metadata(tmp_path, "texts.json", ["mart_legal_texts"])
    index = _metadata(tmp_path, "index.json", ["mart_legal_search_terms"])
    monkeypatch.setattr(db, "resolve_metadata_file", lambda: graph)
    monkeypatch.setattr(db, "resolve_texts_metadata_file", lambda: texts)
    monkeypatch.setattr(db, "resolve_index_metadata_file", lambda: index)

    pub = db._pubblicate_per_dataset(con)
    assert pub["legal_graph"] == {"mart_legal_nodes"}
    assert pub["legal_texts"] == {"mart_legal_texts"}
    assert pub["legal_index"] == {"mart_legal_search_terms"}


def test_texts_non_sono_nel_publish_del_grafo(tmp_path, monkeypatch):
    """Dopo lo split `texts`/`massime` vivono in `legal_texts`.

    Se il gate usasse il metadata del grafo per deciderle, le view `texts` e
    `massime` verrebbero saltate e `legal_text` risponderebbe "assente" pur
    essendo pubblicate nel dataset fratello.
    """
    con = duckdb.connect()
    graph = _metadata(tmp_path, "graph.json", ["mart_legal_nodes"])
    texts = _metadata(tmp_path, "texts.json", ["mart_legal_texts", "mart_legal_massime"])
    monkeypatch.setattr(db, "resolve_metadata_file", lambda: graph)
    monkeypatch.setattr(db, "resolve_texts_metadata_file", lambda: texts)
    monkeypatch.setattr(db, "resolve_index_metadata_file", lambda: None)

    pub = db._pubblicate_per_dataset(con)
    assert "mart_legal_texts" not in (pub["legal_graph"] or set())
    assert "mart_legal_texts" in (pub["legal_texts"] or set())


def test_metadata_illeggibile_lascia_il_dataset_senza_gate(tmp_path, monkeypatch):
    """Metadata rotto = `None`: si prova la sorgente, non si salta la tabella."""
    con = duckdb.connect()
    rotto = tmp_path / "rotto.json"
    rotto.write_text("{non json")
    monkeypatch.setattr(db, "resolve_metadata_file", lambda: rotto)
    monkeypatch.setattr(db, "resolve_texts_metadata_file", lambda: rotto)
    monkeypatch.setattr(db, "resolve_index_metadata_file", lambda: rotto)

    pub = db._pubblicate_per_dataset(con)
    assert pub["legal_graph"] is None
    assert pub["legal_texts"] is None
    assert pub["legal_index"] is None


def test_search_terms_visibile_quando_l_indice_e_pubblicato(con):
    """La regressione vera: con l'indice pubblicato la view deve esserci."""
    pub = db._pubblicate_per_dataset(con)
    if pub.get("legal_index") is None:
        pytest.skip("metadata dell'indice non disponibile (indice non pubblicato)")
    assert db._view_exists(con, "search_terms"), (
        "view search_terms assente con il metadata dell'indice presente: "
        "il gate non sta usando il dataset che possiede la tabella"
    )
