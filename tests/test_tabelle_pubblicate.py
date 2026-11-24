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


def test_gate_per_dataset_non_unisce_i_due_publish(tmp_path, monkeypatch):
    con = duckdb.connect()
    graph = _metadata(tmp_path, "graph.json", ["mart_legal_nodes"])
    index = _metadata(tmp_path, "index.json", ["mart_legal_search_terms"])
    monkeypatch.setattr(db, "resolve_metadata_file", lambda: graph)
    monkeypatch.setattr(db, "resolve_index_metadata_file", lambda: index)

    pub = db._pubblicate_per_dataset(con)
    assert pub["legal_graph"] == {"mart_legal_nodes"}
    assert pub["legal_index"] == {"mart_legal_search_terms"}


def test_metadata_illeggibile_lascia_il_dataset_senza_gate(tmp_path, monkeypatch):
    """Metadata rotto = `None`: si prova la sorgente, non si salta la tabella."""
    con = duckdb.connect()
    rotto = tmp_path / "rotto.json"
    rotto.write_text("{non json")
    monkeypatch.setattr(db, "resolve_metadata_file", lambda: rotto)
    monkeypatch.setattr(db, "resolve_index_metadata_file", lambda: rotto)

    pub = db._pubblicate_per_dataset(con)
    assert pub["legal_graph"] is None
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
