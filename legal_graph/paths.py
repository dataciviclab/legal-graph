"""Path resolution per legal-graph.

Priorità sorgente mart (tabelle leggere del compose):
1. Variabile d'ambiente LEGAL_GRAPH_MART_DIR (override locale/CI)
2. compose toolkit locale: out/data/mart/legal_graph/<year>/
3. GCS pubblico: gs://dataciviclab-mart/legal-graph/… (HTTPS)

texts / massime / temporal: **solo locale** (non nel primo publish GCS).
Legacy data/*.parquet: fallback temporaneo per nodes/edges se assente tutto.
"""
from __future__ import annotations

import os
from pathlib import Path

PathLike = Path | str

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
YEAR = "2026"

DEFAULT_MART_DIR = REPO_ROOT / "out" / "data" / "mart" / "legal_graph" / YEAR
GCS_MART_BASE = (
    f"https://storage.googleapis.com/dataciviclab-mart/legal-graph/"
    f"legal_graph/{YEAR}"
)

# Tabelle pubblicate sul GCS (rilascio 1: grafo, no testi lunghi)
GCS_MART_TABLES = frozenset(
    {
        "mart_legal_nodes",
        "mart_legal_edges",
        "mart_legal_node_metrics",
        "mart_legal_search_keys",
        "mart_legal_node_rel",
        "mart_legal_emend_leg",
    }
)

LEGACY_NODES = DATA_DIR / "legal_nodes.parquet"
LEGACY_EDGES = DATA_DIR / "legal_edges.parquet"
LEGACY_TEMPORAL = DATA_DIR / "legal_edges_temporal.parquet"
LEGACY_METRICS = DATA_DIR / "graph_metrics.parquet"


def mart_dir() -> Path:
    """Directory locale dei mart compose (env override inclusa)."""
    env = os.environ.get("LEGAL_GRAPH_MART_DIR")
    if env:
        return Path(env)
    return DEFAULT_MART_DIR


def gcs_url(table: str) -> str:
    if table not in GCS_MART_TABLES:
        raise ValueError(f"tabella non pubblicata su GCS: {table}")
    return f"{GCS_MART_BASE}/{table}.parquet"


def resolve(table: str, *, allow_gcs: bool = True) -> PathLike | None:
    """Risolvi una tabella mart: locale → GCS (se ammesso)."""
    local = mart_dir() / f"{table}.parquet"
    if local.exists():
        return local
    if allow_gcs and table in GCS_MART_TABLES:
        return gcs_url(table)
    return None


def is_remote(source: PathLike | None) -> bool:
    return isinstance(source, str) and source.startswith(("http://", "https://"))


def source_exists(source: PathLike | None) -> bool:
    """True se la sorgente è leggibile (file locale o URL remote)."""
    if source is None:
        return False
    if is_remote(source):
        return True
    return Path(source).exists()


# ── API legacy (compat test / MCP) ─────────────────────────────────


def resolve_nodes_file() -> PathLike | None:
    src = resolve("mart_legal_nodes")
    if src is not None:
        return src
    return LEGACY_NODES if LEGACY_NODES.exists() else None


def resolve_edges_file() -> PathLike | None:
    src = resolve("mart_legal_edges")
    if src is not None:
        return src
    return LEGACY_EDGES if LEGACY_EDGES.exists() else None


def resolve_metrics_file() -> PathLike | None:
    src = resolve("mart_legal_node_metrics")
    if src is not None:
        return src
    return LEGACY_METRICS if LEGACY_METRICS.exists() else None


def resolve_search_keys_file() -> PathLike | None:
    return resolve("mart_legal_search_keys")


def resolve_node_rel_file() -> PathLike | None:
    return resolve("mart_legal_node_rel")


def resolve_emend_leg_file() -> PathLike | None:
    return resolve("mart_legal_emend_leg")


def resolve_texts_file() -> PathLike | None:
    # non su GCS nel primo rilascio
    return resolve("mart_legal_texts", allow_gcs=False)


def resolve_massime_file() -> PathLike | None:
    return resolve("mart_legal_massime", allow_gcs=False)


def resolve_temporal_file() -> PathLike | None:
    return LEGACY_TEMPORAL if LEGACY_TEMPORAL.exists() else None
