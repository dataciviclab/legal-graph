"""Path resolution per legal-graph.

Priorita':
1. compose toolkit: out/data/mart/legal_graph/<year>/
2. legacy build: data/
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
COMPOSE_MART_DIR = REPO_ROOT / "out" / "data" / "mart" / "legal_graph" / "2026"

COMPOSE_NODES = COMPOSE_MART_DIR / "mart_legal_nodes.parquet"
COMPOSE_EDGES = COMPOSE_MART_DIR / "mart_legal_edges.parquet"

LEGACY_NODES = DATA_DIR / "legal_nodes.parquet"
LEGACY_EDGES = DATA_DIR / "legal_edges.parquet"
LEGACY_TEMPORAL = DATA_DIR / "legal_edges_temporal.parquet"
LEGACY_METRICS = DATA_DIR / "graph_metrics.parquet"
LEGACY_NODES_EU = DATA_DIR / "legal_nodes_eu.parquet"
LEGACY_EDGES_EU = DATA_DIR / "legal_edges_eu.parquet"


def resolve_nodes_file() -> Path:
    """Ritorna il parquet nodi: compose se presente, altrimenti legacy."""
    if COMPOSE_NODES.exists():
        return COMPOSE_NODES
    return LEGACY_NODES


def resolve_edges_file() -> Path:
    """Ritorna il parquet archi: compose se presente, altrimenti legacy."""
    if COMPOSE_EDGES.exists():
        return COMPOSE_EDGES
    return LEGACY_EDGES


def resolve_temporal_file() -> Path | None:
    """Archi temporali (solo legacy / step Python separato)."""
    return LEGACY_TEMPORAL if LEGACY_TEMPORAL.exists() else None


def resolve_metrics_file() -> Path | None:
    """Metriche intelligence (step Python separato dal compose)."""
    return LEGACY_METRICS if LEGACY_METRICS.exists() else None
