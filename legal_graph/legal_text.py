"""Recupero testo integrale per un nodo legal-graph.

Oggi copre source='normativa' (italia-corpus markdown su GitHub raw,
con fallback locale se il repo e' clonato). Altre source restituiscono
un errore strutturato con il ponte note (senato-akn ecc.).
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import duckdb
from lab_connectors.http import HttpClient

from legal_graph.paths import resolve_nodes_file, resolve_texts_file, source_exists
from legal_graph.resolve import resolve_row, sql_quote

IC_RAW_BASE = "https://raw.githubusercontent.com/dataciviclab/italia-corpus/main"
# Override opzionale: repo IC clonato in locale (sviluppo/offline)
IC_LOCAL_ROOT = Path(
    os.environ.get(
        "ITALIA_CORPUS_ROOT",
        str(
            Path(__file__).resolve().parent.parent.parent / "italia-corpus"
        ),
    )
)
# Parent delle collezioni legislative in italia-corpus (contratto IC)
IC_COLLEZIONI_DIRNAME = "collezioni"
TEXT_CACHE = Path(__file__).resolve().parent.parent / "data" / "text_cache"
USER_AGENT = "DataCivicLab-legal-graph/0.2"
_DEFAULT_MAX_CHARS = 8000
_HARD_MAX_CHARS = 50000

_http: HttpClient | None = None


def _get_http() -> HttpClient:
    """HttpClient condiviso: retry, SSL fallback, 429 handling (lab-connectors)."""
    global _http
    if _http is None:
        _http = HttpClient(timeout=30, user_agent=USER_AGENT)
    return _http


def _cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode()).hexdigest()[:20]
    return TEXT_CACHE / f"{digest}.md"


def _read_cache(url: str) -> str | None:
    path = _cache_path(url)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def _write_cache(url: str, text: str) -> None:
    TEXT_CACHE.mkdir(parents=True, exist_ok=True)
    _cache_path(url).write_text(text, encoding="utf-8")


@dataclass
class NodeResolution:
    """Esito di un lookup per id: nodo risolto oppure candidati se ambiguo."""

    node: dict[str, Any] | None = None
    candidates: list[str] = field(default_factory=list)
    match: str = "none"


_NODE_COLS = (
    "id, tipo, title, CAST(data AS VARCHAR) AS data, anno, source, "
    "collezione, source_filename, length_chars"
)
_NODE_KEYS = (
    "id", "tipo", "title", "data", "anno", "source",
    "collezione", "source_filename", "length_chars",
)

# Front-matter YAML in testa ai markdown di italia-corpus: è metadata, non testo
# della norma. Prima finiva nella risposta di legal_text.
_FRONT_MATTER = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)
# Heading di articolo prodotto dall'AKN: "### Articolo 45. — Titolo".
# `45-bis` non deve matchare 45.
_ARTICLE_HEADING = re.compile(
    r"^(#{2,6})[ \t]*(?:Articolo|Art\.)[ \t]+(\d+(?:-[a-z]+)?)\b[^\n]*$",
    re.MULTILINE | re.IGNORECASE,
)


def resolve_node(node_id: str) -> NodeResolution:
    """Lookup nodo nei mart compose. Parziale solo se unico; alias sentenza/ECLI."""
    nodes_file = resolve_nodes_file()
    if not source_exists(nodes_file):
        return NodeResolution()
    con = duckdb.connect(":memory:")
    try:
        res = resolve_row(
            con,
            source=f"read_parquet('{sql_quote(str(nodes_file))}')",
            cols=_NODE_COLS,
            node_id=node_id,
            ecli_alias=True,
        )
    finally:
        con.close()
    node = dict(zip(_NODE_KEYS, res.row)) if res.row is not None else None
    return NodeResolution(node=node, candidates=res.candidates, match=res.match)


def strip_front_matter(text: str) -> str:
    """Toglie il blocco YAML in testa (metadata IC, non testo della norma)."""
    return _FRONT_MATTER.sub("", text, count=1)


def articles_overview(text: str) -> dict[str, Any]:
    """Numeri di articolo disponibili: serve a dire *cosa* esiste, non solo 404."""
    nums = [m.group(2) for m in _ARTICLE_HEADING.finditer(text)]
    return {
        "articles": len(nums),
        "first_article": nums[0] if nums else None,
        "last_article": nums[-1] if nums else None,
    }


def extract_article(text: str, articolo: int | str) -> dict[str, Any] | None:
    """Estrae un articolo dal markdown AKN (heading → heading di pari livello)."""
    want = str(articolo).strip().lower()
    matches = list(_ARTICLE_HEADING.finditer(text))
    for i, m in enumerate(matches):
        if m.group(2).lower() != want:
            continue
        level = len(m.group(1))
        end = len(text)
        for nxt in matches[i + 1:]:
            if len(nxt.group(1)) <= level:
                end = nxt.start()
                break
        return {"heading": m.group(0).strip(), "text": text[m.start():end].strip()}
    return None


def _candidate_paths(collezione: str, filename: str) -> list[str]:
    """Path relativi al repo italia-corpus (sotto collezioni/)."""
    colls = [c.strip() for c in (collezione or "").split(";") if c.strip()]
    prefix = IC_COLLEZIONI_DIRNAME
    if not colls and filename:
        return [f"{prefix}/{filename}"]
    return [f"{prefix}/{c}/{filename}" for c in colls]


def _fetch_remote(rel_path: str) -> tuple[str, str]:
    url = f"{IC_RAW_BASE}/{quote(rel_path)}"
    cached = _read_cache(url)
    if cached is not None:
        return cached, url
    result = _get_http().get(url)
    if not result.is_ok:
        raise OSError(str(result.err) if result.err else f"HTTP fallito per {url}")
    resp = result.response
    if resp is None or not resp.ok:
        status = resp.status_code if resp is not None else "?"
        raise OSError(f"HTTP {status} per {url}")
    text = resp.text
    _write_cache(url, text)
    return text, url


def _fetch_local(rel_path: str) -> str | None:
    local = IC_LOCAL_ROOT / rel_path
    if local.exists():
        return local.read_text(encoding="utf-8", errors="replace")
    return None


def fetch_normativa_text(
    *,
    collezione: str,
    filename: str,
    max_chars: int = _DEFAULT_MAX_CHARS,
    articolo: int | str | None = None,
) -> dict:
    """Scarica il markdown di un atto normativa (remote → cache → locale).

    Con `articolo`, estrae il singolo articolo dagli heading AKN
    (`### Articolo N.`): senza, `max_chars` taglia i codici prima della
    maggior parte degli articoli (l'art. 45 del D.Lgs 36/2023 parte a 160k
    caratteri, il cap è 50k).
    """
    if not filename:
        return {"error": "source_filename mancante sul nodo normativa"}

    max_chars = max(200, min(int(max_chars), _HARD_MAX_CHARS))
    candidates = _candidate_paths(collezione, filename)
    errors: list[str] = []
    text: str | None = None
    url: str | None = None
    via: str | None = None

    # 1) remoto (GitHub raw) — percorso primario del compose
    for rel in candidates:
        try:
            text, url = _fetch_remote(rel)
            via = "github_raw"
            break
        except OSError as exc:
            errors.append(f"{rel}: {exc}")

    # 2) locale opzionale (repo clonato)
    if text is None:
        for rel in candidates:
            local_text = _fetch_local(rel)
            if local_text is not None:
                text = local_text
                via = "locale"
                url = f"file://{IC_LOCAL_ROOT / rel}"
                break

    if text is None:
        return {
            "error": "Testo non recuperato per nessuna collezione",
            "candidates": candidates,
            "errors": errors[:5],
        }

    text = strip_front_matter(text)
    total_chars = len(text)
    article_meta: dict = {}
    if articolo:
        found = extract_article(text, articolo)
        if found is None:
            return {
                "error": f"Articolo {articolo} non trovato in {filename}",
                **articles_overview(text),
            }
        text = found["text"]
        article_meta = {"articolo": str(articolo), "heading": found["heading"]}

    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars] + "\n\n… [troncato]"

    return {
        "via": via,
        "url": url,
        "candidates": candidates,
        "chars": len(text),
        "total_chars": total_chars,
        "truncated": truncated,
        "max_chars": max_chars,
        **article_meta,
        "text": text,
    }


def fetch_mart_text(
    node_id: str,
    *,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> dict:
    """Testo da mart_legal_texts (articoli Cost. + pronunce Corte)."""
    texts_file = resolve_texts_file()
    if texts_file is None or not texts_file.exists():
        return {
            "error": (
                "mart_legal_texts assente — esegui `make run` per produrre "
                "testi articoli Cost. e pronunce."
            )
        }
    max_chars = max(200, min(int(max_chars), _HARD_MAX_CHARS))
    con = duckdb.connect(":memory:")
    try:
        res = resolve_row(
            con,
            source=f"read_parquet('{sql_quote(str(texts_file))}')",
            cols="id, kind, title, testo, dispositivo, ecli",
            node_id=node_id,
        )
    finally:
        con.close()
    if res.row is None:
        if res.candidates:
            return {
                "error": (
                    f"id ambiguo: {node_id!r} corrisponde a più testi. "
                    "Passa l'id completo."
                ),
                "ambiguous": True,
                "candidates": res.candidates,
            }
        return {
            "error": (
                f"Testo non in mart_legal_texts per {node_id!r}. "
                "Coperto: articoli Cost. e pronunce Corte Cost."
            )
        }
    row = res.row
    parts: list[str] = []
    if row[3]:
        parts.append(str(row[3]))
    if row[4]:
        parts.append("\n\n--- DISPOSITIVO ---\n" + str(row[4]))
    text = "\n".join(parts) or ""
    total_chars = len(text)
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars] + "\n\n… [troncato]"
    return {
        "via": "mart_legal_texts",
        "url": str(texts_file),
        "kind": row[1],
        "chars": len(text),
        "total_chars": total_chars,
        "truncated": truncated,
        "max_chars": max_chars,
        "ecli": row[5],
        "text": text,
    }
