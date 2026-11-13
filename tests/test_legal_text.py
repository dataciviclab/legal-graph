"""Test legal_text — recupero testo da nodo normativa.

I test marcati `network` scaricano il markdown da GitHub raw (cache in
`data/text_cache/`, gitignored): restano fuori dal loop locale, dove
`data/text_cache/` è fredda e la rete non è un presupposto.
"""
from __future__ import annotations

import pytest

from legal_graph.legal_text import (
    articles_overview,
    extract_article,
    fetch_mart_text,
    fetch_normativa_text,
    resolve_node,
    strip_front_matter,
)
from legal_graph.mcp_server import _impl_legal_text


def test_fetch_mart_text_url_non_raggiungibile(monkeypatch, tmp_path):
    """Sorgente remota assente: errore leggibile, non AttributeError/eccezione.

    Regressione 2026-11: `texts_file.exists()` assumeva un Path locale; con
    `LEGAL_GRAPH_SOURCE=gcs` il resolver restituisce una URL (str).
    """
    mancante = str(tmp_path / "mart_legal_texts.parquet")
    monkeypatch.setattr(
        "legal_graph.legal_text.resolve_texts_file", lambda: mancante
    )
    out = fetch_mart_text("costituzione:art:3")
    assert "error" in out
    assert "assente" in out["error"]


def test_cache_hit_dichiarata(monkeypatch, tmp_path):
    """Un testo servito dalla cache non deve dichiararsi `github_raw`."""
    from legal_graph import legal_text as lt

    monkeypatch.setattr(lt, "TEXT_CACHE", tmp_path)
    rel = "Codici/finto.md"
    lt._write_cache(f"{lt.IC_RAW_BASE}/{rel}", "---\ntitolo: x\n---\ntesto di prova")
    text, _url, via = lt._fetch_remote(rel)
    assert via == "cache"
    assert "testo di prova" in text


def test_atto_fuori_collezione_normattiva():
    """I 171 nodi `atti_api` hanno l'URN come filename: non è un 404.

    Il testo non è in italia-corpus (collezione Normattiva API): la risposta
    onesta è un errore strutturato con il link, non un path interno e un 404.
    """
    out = fetch_normativa_text(
        collezione="Normattiva API", filename="urn:nir:stato:legge:1978-05-22;194"
    )
    assert "error" in out and "fuori collezione" in out["error"]
    assert out["normattiva_url"] == (
        "https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:legge:1978-05-22;194"
    )
    assert "hint" in out


class TestResolveNode:
    def test_resolve_urn_exact(self):
        found = resolve_node("urn:nir:stato:decreto.legge:1996-01-24;30")
        assert found.node is not None
        assert found.match == "exact"
        assert found.node["source"] == "normativa"
        assert found.node["source_filename"].endswith(".md")
        assert found.node["collezione"]

    def test_resolve_partial(self):
        found = resolve_node("decreto.legislativo:2017-07-03;117")
        assert found.node is not None
        assert found.match == "partial"
        assert "117" in found.node["id"]

    def test_resolve_missing(self):
        found = resolve_node("urn:nir:stato:non:esiste;0")
        assert found.node is None and not found.candidates


class TestArticoli:
    """`articolo=N` deve estrarre il singolo articolo, non la testa del file."""

    MD = """---
tipo: LEGGE
numero: 1
---

IL PRESIDENTE DELLA REPUBBLICA

### Articolo 1. — Principio
Testo dell'articolo uno.

#### Comma 2
Dettaglio del comma.

### Articolo 2.
Testo dell'articolo due.

### Articolo 2-bis.
Testo dell'articolo due bis.
"""

    def test_strip_front_matter(self):
        body = strip_front_matter(self.MD)
        assert not body.startswith("---")
        assert "IL PRESIDENTE" in body

    def test_estrazione_fino_al_prossimo_articolo(self):
        art = extract_article(strip_front_matter(self.MD), 1)
        assert art is not None
        assert art["heading"].startswith("### Articolo 1")
        assert "Comma 2" in art["text"]  # le sottosezioni restano nell'articolo
        assert "articolo due" not in art["text"]

    def test_bis_non_confuso_col_numero_base(self):
        body = strip_front_matter(self.MD)
        assert "due bis" not in (extract_article(body, 2) or {}).get("text", "")
        assert extract_article(body, "2-bis") is not None

    def test_articolo_assente(self):
        assert extract_article(strip_front_matter(self.MD), 99) is None

    def test_overview_articoli(self):
        ov = articles_overview(strip_front_matter(self.MD))
        # 1, 2 e 2-bis: tre heading, due numeri base (i -bis non alzano il massimo)
        assert ov["headings"] == 3
        assert ov["articles"] == 2
        assert ov["first_article"] == "1"
        assert ov["last_article"] == "2"

    @pytest.mark.network
    def test_articolo_bis(self):
        found = resolve_node("urn:nir:stato:decreto.legislativo:2023-03-31;36")
        res = fetch_normativa_text(
            collezione=found.node["collezione"],
            filename=found.node["source_filename"],
            articolo="82-bis",
            max_chars=800,
        )
        assert "error" not in res, res.get("error")
        assert res["heading"].startswith("### Articolo 82-bis")
        assert res["articolo"] == "82-bis"

    @pytest.mark.network
    def test_articolo_su_codice_reale(self):
        found = resolve_node("urn:nir:stato:decreto.legislativo:2023-03-31;36")
        assert found.node is not None
        res = fetch_normativa_text(
            collezione=found.node["collezione"],
            filename=found.node["source_filename"],
            articolo=45,
            max_chars=3000,
        )
        assert "error" not in res, res.get("error")
        assert res["heading"].startswith("### Articolo 45")
        assert res["total_chars"] > 500_000  # il codice intero è ~800k
        assert res["chars"] < 4000
        assert not res["text"].startswith("---")


class TestFetchNormativaText:
    @pytest.mark.network
    def test_fetch_known_act(self):
        result = fetch_normativa_text(
            collezione="DL decaduti",
            filename="1996-01-25_096G0034_VIGENZA_2025-07-09_V0.md",
            max_chars=2000,
        )
        assert "error" not in result
        assert result["via"] in {"github_raw", "cache", "locale"}
        assert result["text"]
        assert result["truncated"] is True  # max_chars=2000
        assert len(result["text"]) <= 2000 + 50

    @pytest.mark.network
    def test_fetch_multi_collection_first_wins(self):
        result = fetch_normativa_text(
            collezione="DL e leggi di conversione;Leggi di ratifica",
            filename="1948-07-27_048U0970_ORIGINALE_V0.md",
            max_chars=500,
        )
        assert "error" not in result
        assert result["candidates"][0].startswith("collezioni/DL e leggi di conversione/")

    def test_fetch_missing_filename(self):
        result = fetch_normativa_text(collezione="Codici", filename="")
        assert "error" in result


class TestMcpLegalText:
    @pytest.mark.network
    def test_tool_normativa(self):
        out = _impl_legal_text("urn:nir:stato:decreto.legge:1996-01-24;30", max_chars=1500)
        assert "error" not in out
        assert out["source"] == "normativa"
        assert out["text"]
        assert out["id"].startswith("urn:nir:")

    def test_tool_id_ambiguo_non_indovina(self):
        # "senato:emend:" esiste su 248k nodi: prima ne restituiva uno a caso
        out = _impl_legal_text("senato:emend:", max_chars=500)
        assert out.get("ambiguous") is True
        assert out["candidates"]
        assert "error" in out

    @pytest.mark.network
    def test_tool_articolo_bis(self):
        # il tool dichiara `int | str`: "2-bis" deve passare, non essere rifiutato
        out = _impl_legal_text(
            "urn:nir:stato:decreto.legislativo:2023-03-31;36",
            max_chars=800,
            articolo="82-bis",
        )
        assert "error" not in out, out.get("error")
        assert out["articolo"] == "82-bis"
        assert out["heading"].startswith("### Articolo 82-bis")

    @pytest.mark.network
    def test_tool_articolo(self):
        out = _impl_legal_text(
            "urn:nir:stato:decreto.legislativo:2023-03-31;36",
            max_chars=2000,
            articolo=1,
        )
        assert "error" not in out, out.get("error")
        assert out["heading"].startswith("### Articolo 1")
        assert out["articolo"] == "1"
        assert out["total_chars"] > 100_000

    def test_tool_missing(self):
        out = _impl_legal_text("questo-nodo-non-esiste-xyz")
        assert "error" in out


def test_fetch_mart_text_riusa_la_connessione_del_server(con, monkeypatch):
    """Nessuna connessione propria per chiamata.

    La cache esterna di DuckDB (i blocchi del parquet già letti) vive nella
    connessione: una connessione nuova per ogni `legal_text` la azzera, e da
    GCS erano 5-7 s *a ogni* chiamata (`mart_legal_texts` è un unico row
    group da 80 MB). Con la connessione del server la seconda chiamata
    scende a ~2,5 s. Il test vieta `duckdb.connect` dentro il fetch.
    """
    from legal_graph import legal_text as lt

    class _Vietato:
        Error = lt.duckdb.Error

        def __getattr__(self, name):
            return getattr(lt.duckdb, name)

        def connect(self, *args, **kwargs):
            raise AssertionError(
                "fetch_mart_text ha aperto una connessione propria: la cache "
                "esterna non viene riusata e il testo si riscarica ogni volta"
            )

    monkeypatch.setattr(lt, "duckdb", _Vietato())

    out = fetch_mart_text("costituzione:art:3")

    assert "error" not in out, out
    assert out["text"].strip(), "testo vuoto dall'articolo 3"
