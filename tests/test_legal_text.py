"""Test legal_text — recupero testo da nodo normativa."""
from __future__ import annotations

from legal_graph.legal_text import (
    articles_overview,
    extract_article,
    fetch_normativa_text,
    resolve_node,
    strip_front_matter,
)
from legal_graph.mcp_server import _impl_legal_text


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
    def test_fetch_known_act(self):
        result = fetch_normativa_text(
            collezione="DL decaduti",
            filename="1996-01-25_096G0034_VIGENZA_2025-07-09_V0.md",
            max_chars=2000,
        )
        assert "error" not in result
        assert result["via"] in {"github_raw", "locale"}
        assert result["text"]
        assert result["truncated"] is True  # max_chars=2000
        assert len(result["text"]) <= 2000 + 50

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
