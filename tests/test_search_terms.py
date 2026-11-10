"""Contratto dell'indice lessicale (`mart_legal_search_terms`) e regressione temi.

L'indice esiste perché la ricerca per tema non poteva vedere gli atti il cui
titolo non contiene le parole della query ("appalti pubblici" → Codice dei
contratti pubblici). Questi test proteggono:

- forma e integrità del mart (id esistenti, pesi finiti, stem coerente);
- la regola di stem condivisa tra SQL e MCP;
- i temi che prima fallivano e ora devono restare in top-5.

I casi non coperti restano dichiarati in `docs/COVERAGE.md` (L. 241/1990 non
ha testo nel corpus, DPR 380/2001 usa "permesso di costruire", ecc.).
"""

from __future__ import annotations

import pytest

import legal_graph.mcp_server as m
from legal_graph.mcp import db
from legal_graph.mcp.textfold import _term_stem


@pytest.fixture(scope="module")
def terms_ready(con):
    if not db._view_exists(con, "search_terms"):
        if __import__("os").environ.get("LEGAL_GRAPH_REQUIRE_MART") == "1":
            pytest.fail("mart_legal_search_terms assente — esegui `make run`")
        pytest.skip("mart_legal_search_terms assente — esegui `make run`")
    return con


def test_terms_mart_contract(terms_ready):
    """Indice pubblicabile: dimensione, chiave, pesi, nessun id inventato."""
    n, termini = terms_ready.execute(
        "SELECT COUNT(*), COUNT(DISTINCT term) FROM search_terms"
    ).fetchone()
    assert n >= 1_000_000, f"indice troppo piccolo: {n} righe"
    assert termini >= 10_000, f"vocabolario troppo piccolo: {termini} termini"

    dup = terms_ready.execute(
        "SELECT COUNT(*) FROM (SELECT id, term FROM search_terms "
        "GROUP BY 1, 2 HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    assert dup == 0, f"{dup} coppie (id, term) duplicate"

    bad = terms_ready.execute(
        "SELECT COUNT(*) FROM search_terms WHERE weight IS NULL OR weight <= 0"
    ).fetchone()[0]
    assert bad == 0, f"{bad} pesi nulli o non positivi"

    dangling = terms_ready.execute(
        "SELECT COUNT(*) FROM search_terms t "
        "LEFT JOIN nodes n ON n.id = t.id WHERE n.id IS NULL"
    ).fetchone()[0]
    assert dangling == 0, f"{dangling} id dell'indice senza nodo"


def test_stem_rule_shared_sql_python(terms_ready):
    """La regola di stem del SQL deve essere quella di `_term_stem`.

    SQL: `CASE WHEN length(term) > 4 AND right(term,1) IN ('a','e','i','o')`.
    Se una delle due parti cambia, la query cerca una forma che il mart non
    contiene e il tema sparisce in silenzio: meglio rompere qui.
    """
    for superficie, atteso in [
        ("congedo", "conged"),
        ("congedi", "conged"),
        ("appalti", "appalt"),
        ("appalto", "appalt"),
        ("rifiuti", "rifiut"),
        ("sicurezza", "sicurezz"),
        ("casa", "casa"),  # 4 caratteri: non si tocca
    ]:
        assert _term_stem(superficie) == atteso
        assert terms_ready.execute(
            "SELECT 1 FROM search_terms WHERE term = ? LIMIT 1", [atteso]
        ).fetchone(), f"mart senza lo stem {atteso!r} ({superficie})"
        # la forma superficiale non deve comparire come termine: è stata stemmata
        if atteso != superficie:
            assert not terms_ready.execute(
                "SELECT 1 FROM search_terms WHERE term = ? LIMIT 1", [superficie]
            ).fetchone(), f"mart con la forma non stemmata {superficie!r}"


@pytest.mark.parametrize(
    "query,expected",
    [
        # temi che la ricerca sul solo titolo sbagliava
        ("diritto di recesso dal contratto", "decreto.legislativo:2005-09-06;206"),
        ("congedo parentale", "decreto.legislativo:2001-03-26;151"),
        ("protezione dati personali", "decreto.legislativo:2003-06-30;196"),
        ("codice del terzo settore", "decreto.legislativo:2017-07-03;117"),
        ("appalti pubblici", "decreto.legislativo:2023-03-31;36"),
        ("sicurezza sul lavoro", "decreto.legislativo:2008-04-09;81"),
        ("responsabilità società reati ambientali", "decreto.legislativo:2001-06-08;231"),
        ("rifiuti urbani", "decreto.legislativo:2006-04-03;152"),
        ("patente a punti", "decreto.legislativo:1992-04-30;285"),
        ("servizio civile universale", "decreto.legislativo:2017-03-06;40"),
    ],
)
def test_theme_top5(terms_ready, query, expected):
    """Un tema deve portare in top-5 l'atto canonico (regressione eval 2026-11)."""
    rows = m.legal_search(query, limit=5)
    ids = [r.get("id") or "" for r in rows]
    assert expected in " | ".join(ids), f"{query!r}: {expected} non in top-5 — {ids}"


def test_term_join_coverage_need(terms_ready):
    """La copertura minima si conta sulle parole dell'utente, non sul glossario.

    "whistleblowing" si espande in "segnalazione": se contassimo le espansioni
    servirebbero due match per un concetto solo, e l'atto non uscirebbe.
    """
    from legal_graph.mcp.search import _term_join

    join, params, need = _term_join(
        terms_ready, {"terms": ["appalti", "pubblici"], "primary": ["appalti", "pubblici"]}
    )
    assert join.startswith("LEFT JOIN")
    assert need == 2 and params[-1] == 2

    _join, params, need = _term_join(
        terms_ready,
        {"terms": ["whistleblowing", "segnalazione"], "primary": ["whistleblowing"]},
    )
    assert need == 1 and params[-1] == 1

    # fallback: la soglia si abbassa a 1 quando la query stretta è vuota
    _join, params, need = _term_join(
        terms_ready,
        {"terms": ["pizzo", "estorsione"], "primary": ["pizzo", "estorsione"]},
        min_need=1,
    )
    assert need == 1


def test_term_index_absent_falls_back(monkeypatch):
    """Senza indice il MCP non deve rompersi: ricade sul match sul titolo."""
    from legal_graph.mcp import search as s

    monkeypatch.setattr(s, "_view_exists", lambda con, name: name != "search_terms")
    # la funzione restituisce None quando il mart manca
    assert s._term_join(None, {"terms": ["appalti"], "primary": ["appalti"]}) is None
