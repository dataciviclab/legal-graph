"""Test legal_text — recupero testo da nodo normativa."""
from __future__ import annotations

from legal_graph.legal_text import fetch_normativa_text, resolve_node
from legal_graph.mcp_server import _impl_legal_text


class TestResolveNode:
    def test_resolve_urn_exact(self):
        node = resolve_node("urn:nir:stato:decreto.legge:1996-01-24;30")
        assert node is not None
        assert node["source"] == "normativa"
        assert node["source_filename"].endswith(".md")
        assert node["collezione"]

    def test_resolve_partial(self):
        node = resolve_node("decreto.legislativo:2017-07-03;117")
        assert node is not None
        assert "117" in node["id"]

    def test_resolve_missing(self):
        assert resolve_node("urn:nir:stato:non:esiste;0") is None


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
        assert result["candidates"][0].startswith("DL e leggi di conversione/")

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

    def test_tool_non_normativa(self):
        out = _impl_legal_text("senato:emend:", max_chars=500)
        # partial match may hit an emendamento node
        if "error" in out and "non disponibile" in out.get("error", ""):
            assert out["source"] != "normativa"
        else:
            # se per caso matcha altro, deve comunque non crashtare
            assert isinstance(out, dict)

    def test_tool_missing(self):
        out = _impl_legal_text("questo-nodo-non-esiste-xyz")
        assert "error" in out
