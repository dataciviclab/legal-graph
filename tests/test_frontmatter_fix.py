"""Test frontmatter parser handles encoding artifacts."""

from __future__ import annotations

import sys
from pathlib import Path

# Add italia-corpus to path so we can import lab_tools
_corpus_root = Path(__file__).resolve().parent.parent.parent.parent / "italia-corpus"
sys.path.insert(0, str(_corpus_root))

from lab_tools._frontmatter import parse_frontmatter


def test_clean_frontmatter():
    text = "---\ntipo: LEGGE\nnumero: 1\n---\nBody"
    result = parse_frontmatter(text)
    assert result is not None
    assert result["tipo"] == "LEGGE"


def test_encoding_artifact_0x95():
    """Windows-1252 \\x95 becomes U+FFFD after errors='replace', should not break parsing."""
    text = "---\ntipo: LEGGE\nnumero: 1\ntitolo: Test \ufffd title\n---\nBody"
    result = parse_frontmatter(text)
    assert result is not None
    assert result["tipo"] == "LEGGE"


def test_encoding_artifact_0x9b():
    """Windows-1252 \\x9b becomes U+FFFD after errors='replace', should not break parsing."""
    text = "---\ntipo: LEGGE\nnumero: 1\ntitolo: Test \ufffd title\n---\nBody"
    result = parse_frontmatter(text)
    assert result is not None
    assert result["tipo"] == "LEGGE"


def test_replacement_char():
    """Unicode replacement character (U+FFFD) should be stripped."""
    text = "---\ntipo: LEGGE\nnumero: 1\ntitolo: Test \ufffd title\n---\nBody"
    result = parse_frontmatter(text)
    assert result is not None


def test_no_frontmatter():
    result = parse_frontmatter("Just body text")
    assert result is None
