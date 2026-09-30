"""Recupero testo integrale per un nodo legal-graph.

Oggi copre source='normativa' (italia-corpus markdown su GitHub raw,
con fallback locale se il repo e' clonato). Altre source restituiscono
un errore strutturato con il ponte note (senato-akn ecc.).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import duckdb

from legal_graph.paths import resolve_nodes_file, resolve_texts_file

IC_RAW_BASE = "https://raw.githubusercontent.com/dataciviclab/italia-corpus/main"
IC_LOCAL_ROOT = Path("/home/gabry/dev/dataciviclab-workspace/diritto-legge/italia-corpus")
TEXT_CACHE = Path(__file__).resolve().parent.parent / "data" / "text_cache"
USER_AGENT = "DataCivicLab-legal-graph/0.2"
_DEFAULT_MAX_CHARS = 8000
_HARD_MAX_CHARS = 50000


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


def resolve_node(node_id: str) -> dict | None:
    """Lookup nodo nei mart compose (o legacy)."""
    nodes_file = resolve_nodes_file()
    if not nodes_file.exists():
        return None
    con = duckdb.connect(":memory:")
    try:
        row = con.execute(
            """
            SELECT id, tipo, title, CAST(data AS VARCHAR) AS data, anno,
                   source, collezione, source_filename, length_chars
            FROM read_parquet(?)
            WHERE id = ? OR id LIKE ?
            ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END, length(id)
            LIMIT 1
            """,
            [str(nodes_file), node_id, f"%{node_id}%", node_id],
        ).fetchone()
    finally:
        con.close()
    if not row:
        return None
    keys = (
        "id", "tipo", "title", "data", "anno",
        "source", "collezione", "source_filename", "length_chars",
    )
    return dict(zip(keys, row))


def _candidate_paths(collezione: str, filename: str) -> list[str]:
    """Path relativi al repo italia-corpus (una o più collezioni)."""
    colls = [c.strip() for c in (collezione or "").split(";") if c.strip()]
    if not colls and filename:
        return [filename]
    return [f"{c}/{filename}" for c in colls]


def _fetch_remote(rel_path: str) -> tuple[str, str]:
    url = f"{IC_RAW_BASE}/{quote(rel_path)}"
    cached = _read_cache(url)
    if cached is not None:
        return cached, url
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="replace")
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
) -> dict:
    """Scarica il markdown di un atto normativa (remote → cache → locale)."""
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
        except (HTTPError, URLError, OSError) as exc:
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

    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars] + "\n\n… [troncato]"

    return {
        "via": via,
        "url": url,
        "candidates": candidates,
        "chars": len(text),
        "truncated": truncated,
        "max_chars": max_chars,
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
    # normalizza sentenza senza zero-pad
    nid = node_id.strip()
    m = __import__("re").fullmatch(r"sentenza:(\d{4})-(\d{1,4})", nid, __import__("re").IGNORECASE)
    if m:
        nid = f"sentenza:{m.group(1)}-{int(m.group(2)):04d}"
    con = duckdb.connect(":memory:")
    try:
        row = con.execute(
            """
            SELECT id, kind, title, testo, dispositivo, ecli
            FROM read_parquet(?)
            WHERE id = ? OR id LIKE ?
            ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END, length(id)
            LIMIT 1
            """,
            [str(texts_file), nid, f"%{nid}%", nid],
        ).fetchone()
    finally:
        con.close()
    if not row:
        return {
            "error": (
                f"Testo non in mart_legal_texts per {node_id!r}. "
                "Coperto: articoli Cost. e pronunce Corte Cost."
            )
        }
    parts: list[str] = []
    if row[3]:
        parts.append(str(row[3]))
    if row[4]:
        parts.append("\n\n--- DISPOSITIVO ---\n" + str(row[4]))
    text = "\n".join(parts) or ""
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars] + "\n\n… [troncato]"
    return {
        "via": "mart_legal_texts",
        "url": str(texts_file),
        "kind": row[1],
        "chars": len(text),
        "truncated": truncated,
        "max_chars": max_chars,
        "ecli": row[5],
        "text": text,
    }
