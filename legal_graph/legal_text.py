"""Recupero testo integrale per un nodo legal-graph.

Oggi copre source='normativa' (italia-corpus markdown su GitHub raw,
con fallback locale se il repo e' clonato). Altre source restituiscono
un errore strutturato con il ponte note (senato-akn ecc.).
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import duckdb
from lab_connectors.http import HttpClient

from legal_graph.paths import resolve_nodes_file, resolve_texts_file, source_exists
from legal_graph.resolve import resolve_row, sql_quote

IC_RAW_BASE = "https://raw.githubusercontent.com/dataciviclab/italia-corpus/main"
# Allegati AKN (#94): indice dei <doc> (uno per articolo/allegato) e file
# markdown per atto. Pubblicati da italia-corpus sul raw layer / prefisso
# `allegati/`. `doc_id` è l'identità stabile; `doc_name` porta "…-art. N".
ALLEGATI_INDEX_URL = (
    "https://storage.googleapis.com/dataciviclab-clean/"
    "italia-corpus/_raw/2026/allegati.parquet"
)
ALLEGATI_MD_BASE = "https://storage.googleapis.com/dataciviclab-clean/italia-corpus"
_ALLEGATI_TTL_SECONDS = 24 * 3600
# Il testo di un atto cambia quando IC rigenera il corpus (settimanale):
# 7 giorni è bounded freshness coerente con la cadenza, senza riscaricare
# lo stesso file ogni giorno.
_ALLEGATI_MD_TTL_SECONDS = 7 * 24 * 3600
# URN → testo vigente (link pubblico, usato per gli atti fuori collezione)
NORMATTIVA_URI = "https://www.normattiva.it/uri-res/N2Ls?"
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


def _allegati_index_path() -> Path:
    return TEXT_CACHE.parent / "allegati_index.parquet"


def _ensure_allegati_index_file() -> Path:
    """Scarica l'indice allegati (TTL 24h) e restituisce il path locale."""
    import urllib.request

    path = _allegati_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or time.time() - path.stat().st_mtime > _ALLEGATI_TTL_SECONDS:
        with urllib.request.urlopen(ALLEGATI_INDEX_URL, timeout=60) as resp:
            path.write_bytes(resp.read())
    return path


def attachment_docs(urn: str) -> list[dict]:
    """Documenti allegati di un atto (vuoto se non ne ha).

    Legge l'indice con duckdb filtrando per URN: niente 177 MB di dict in
    memoria, e duckdb è già dipendenza del repo (pyarrow non lo è).
    """
    try:
        path = _ensure_allegati_index_file()
        con = duckdb.connect()
        try:
            rows = con.execute(
                "SELECT doc_id, doc_name, chars, rel_path, hash, versione "
                f"FROM read_parquet('{path.as_posix()}') WHERE urn = ?",
                [urn],
            ).fetchall()
        finally:
            con.close()
    except (OSError, ValueError, duckdb.Error) as exc:
        print(f"::warning::indice allegati non disponibile ({exc})")
        return []
    cols = ("doc_id", "doc_name", "chars", "rel_path", "hash", "versione")
    return [dict(zip(cols, row)) for row in rows]


def attachments_meta(docs: list[dict], limit: int = 40) -> dict[str, Any]:
    """Riassunto degli allegati per la risposta di legal_text."""
    return {
        "count": len(docs),
        "docs": [
            {"doc_id": d.get("doc_id"), "doc_name": d.get("doc_name"), "chars": d.get("chars")}
            for d in docs[:limit]
        ],
        "truncated": len(docs) > limit,
    }


def _cache_fresh(url: str, ttl: int | None) -> bool:
    """True se la cache esiste ed è più recente di `ttl` (None = sempre fresca)."""
    path = _cache_path(url)
    if not path.exists():
        return False
    return ttl is None or (time.time() - path.stat().st_mtime) <= ttl


def _fetch_url(url: str, ttl: int | None = None) -> tuple[str, str]:
    """(testo, via) da un URL pubblico, cache su disco per URL.

    La chiave è l'URL: la cache segue la granularità della risorsa (un file
    per atto, non un doc). `ttl` bounded-freschezza: scaduto, si riscarica.
    """
    if _cache_fresh(url, ttl):
        cached = _read_cache(url)
        if cached is not None:
            return cached, "cache"
    result = _get_http().get(url)
    if not result.is_ok or result.response is None or not result.response.ok:
        raise OSError(f"HTTP fallito per {url}")
    text = result.response.text
    _write_cache(url, text)
    return text, "gcs"


def _sezione_doc(md: str, doc_name: str) -> str | None:
    """Testo della sezione `## <doc_name>` (fino al successivo `## `)."""
    marker = f"## {doc_name}"
    start = md.find(marker)
    if start < 0:
        return None
    body_start = md.find("\n", start)
    if body_start < 0:
        return None
    nxt = md.find("\n## ", body_start + 1)
    return (md[body_start:nxt] if nxt != -1 else md[body_start:]).strip()


def _norm_articolo(articolo: int | str) -> str:
    """`"Art. 2043"`, `"art.2043."`, `2043` → `"2043"`."""
    val = str(articolo).strip().lower()
    if val.startswith("art."):
        val = val[4:].strip()
    return val.rstrip(".").strip()


def match_articolo(docs: list[dict], articolo: int | str) -> list[dict]:
    """Allegati il cui `doc_name` finisce per `art. N` (N esatto, non prefisso)."""
    want = f"art. {_norm_articolo(articolo)}"
    return [
        d for d in docs if str(d.get("doc_name") or "").strip().lower().endswith(want)
    ]


def is_shell(docs_total: int, body_total: int) -> bool:
    """True se il body è il decreto di approvazione e il testo sta negli allegati."""
    return docs_total > 10 * max(body_total, 1)


def _serve_doc(urn: str, target: dict, max_chars: int) -> dict:
    rel_path = target.get("rel_path") or ""
    if not rel_path:
        return {"error": "allegato senza rel_path nell'indice"}
    url = f"{ALLEGATI_MD_BASE}/{quote(rel_path)}"
    try:
        md, via = _fetch_url(url, ttl=_ALLEGATI_MD_TTL_SECONDS)
    except OSError as exc:
        return {"error": f"allegato non recuperato: {exc}", "url": url}
    sezione = _sezione_doc(md, target.get("doc_name") or "")
    if sezione is None:
        return {"error": "sezione non trovata nel markdown allegati", "url": url}
    total = len(sezione)
    truncated = total > max_chars
    if truncated:
        sezione = sezione[:max_chars] + "\n\n… [troncato]"
    return {
        "via": via,
        "url": url,
        "doc_id": target.get("doc_id"),
        "doc_name": target.get("doc_name"),
        "chars": len(sezione),
        "total_chars": total,
        "truncated": truncated,
        "max_chars": max_chars,
        "text": sezione,
    }


def fetch_attachment_text(
    *,
    urn: str,
    doc: str | None = None,
    articolo: int | str | None = None,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> dict | None:
    """Testo di un `<doc>` allegato (#94): i codici hanno il testo negli attachments.

    `doc` = `doc_id` (identità stabile). Con `articolo` prova il match univoco
    su `doc_name` che finisce per `art. N`; se è ambiguo (preleggi + codice),
    restituisce i candidati. None se l'atto non ha allegati.
    """
    docs = attachment_docs(urn)
    if not docs:
        return None
    max_chars = max(200, min(int(max_chars), _HARD_MAX_CHARS))
    target: dict | None = None
    if doc:
        target = next((d for d in docs if str(d.get("doc_id")) == str(doc)), None)
        if target is None:
            # tolleranza: il chiamante può passare il doc_name letto dalla risposta
            target = next(
                (d for d in docs if str(d.get("doc_name") or "").strip() == str(doc).strip()),
                None,
            )
        if target is None:
            return {
                "error": f"doc {doc!r} non trovato tra gli allegati",
                "attachments": attachments_meta(docs),
            }
    elif articolo:
        matches = match_articolo(docs, articolo)
        if len(matches) == 1:
            target = matches[0]
        elif matches:
            return {
                "ambiguous": True,
                "note": "più allegati per questo articolo: passa doc=<doc_id>.",
                "attachments": attachments_meta(matches),
            }
        else:
            return {
                "error": f"articolo {articolo} non trovato negli allegati",
                "attachments": attachments_meta(docs),
            }
    if target is None:
        return {"attachments": attachments_meta(docs)}
    served = _serve_doc(urn, target, max_chars)
    served.setdefault("attachments", attachments_meta(docs))
    return served


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
    """Articoli disponibili: serve a dire *cosa* esiste, non solo 404.

    `articles` conta i numeri base (1..229), `headings` tutti gli heading: i
    `-bis`/`-ter` sono articoli in più ma non alzano il numero massimo, e
    dichiarare "233 articoli" con `last_article: 229` confondeva.
    """
    labels = [m.group(2) for m in _ARTICLE_HEADING.finditer(text)]
    base: list[str] = []
    for label in labels:
        stem = label.split("-")[0]
        if stem not in base:
            base.append(stem)
    return {
        "articles": len(base),
        "headings": len(labels),
        "first_article": base[0] if base else None,
        "last_article": base[-1] if base else None,
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


def _fetch_remote(rel_path: str) -> tuple[str, str, str]:
    """(testo, url, via) — `via="cache"` se il testo viene dalla cache locale.

    Serve a distinguere una risposta fresca da una vecchia: prima anche i
    cache hit si dichiaravano `github_raw`, quindi un agente non poteva sapere
    se il testo era aggiornato.
    """
    url = f"{IC_RAW_BASE}/{quote(rel_path)}"
    cached = _read_cache(url)
    if cached is not None:
        return cached, url, "cache"
    result = _get_http().get(url)
    if not result.is_ok:
        raise OSError(str(result.err) if result.err else f"HTTP fallito per {url}")
    resp = result.response
    if resp is None or not resp.ok:
        status = resp.status_code if resp is not None else "?"
        raise OSError(f"HTTP {status} per {url}")
    text = resp.text
    _write_cache(url, text)
    return text, url, "github_raw"


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

    # Atti fuori collezione (`support atti_api`) e nodi catalogo
    # (`source='catalogo'`): `source_filename` è l'URN, non un file markdown di
    # italia-corpus. Il testo non è nel corpus e non c'è niente da scaricare:
    # dirlo con il link Normattiva invece di lasciare un 404 su un path interno.
    if filename.startswith("urn:"):
        return {
            "error": (
                "Atto fuori collezione italia-corpus (Normattiva API): il testo "
                "non è nel corpus, non è un errore di fetch."
            ),
            "collezione": collezione,
            "normattiva_url": f"{NORMATTIVA_URI}{filename}",
            "hint": "Il grafo copre nodo e relazioni; per il testo apri normattiva_url.",
        }

    max_chars = max(200, min(int(max_chars), _HARD_MAX_CHARS))
    candidates = _candidate_paths(collezione, filename)
    errors: list[str] = []
    text: str | None = None
    url: str | None = None
    via: str | None = None

    # 1) remoto (GitHub raw) — percorso primario del compose
    for rel in candidates:
        try:
            text, url, via = _fetch_remote(rel)
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
    if not source_exists(texts_file):
        return {
            "error": (
                "mart_legal_texts assente — esegui `make run` o pubblica i mart "
                "su GCS per i testi di articoli Cost. e pronunce."
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
    except duckdb.Error as exc:
        # URL pubblico non raggiungibile (es. tabella non ancora pubblicata):
        # errore leggibile invece dell'eccezione DuckDB grezza.
        return {"error": f"mart_legal_texts non leggibile ({texts_file}): {exc}"}
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
