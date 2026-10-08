"""Fonti dati per la dashboard Legal Graph.

Wrappa lab_connectors (path contract mart) con @st.cache_data.
Layout GCS: gs://dataciviclab-mart/legal-graph/legal_graph/2026/mart_legal_*.parquet
Locale (fallback): out/data/mart/legal_graph/2026/ (gitignored, make run).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
from lab_connectors.duckdb.core import safe_connect
from lab_connectors.duckdb.queries import (
    detect_local_root,
)
from lab_connectors.duckdb.queries import (
    load_mart_table as _load_mart_table,
)
from lab_connectors.formatters import fmt_eur, fmt_num, fmt_pct  # noqa: F401 — re-export pagine
from lab_connectors.gcs.paths import https_url, resolve

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "legal-graph/"
SLUG = "legal_graph"
YEAR = 2026
YEARS = [YEAR]
LOCAL_ROOT = detect_local_root(repo_root=ROOT)

# Mart tables (contratto: {prefix}{slug}/{year}/{table}.parquet)
MART_TABLES = (
    "mart_legal_nodes",
    "mart_legal_edges",
    "mart_legal_node_metrics",
    "mart_legal_search_keys",
    "mart_legal_node_rel",
    "mart_legal_emend_leg",
)

# Alias delle view: mart_legal_node_metrics → metrics (comodo per le pagine)
_VIEW_ALIASES = {"mart_legal_node_metrics": "metrics"}

# Compose OP — decreti_legge (clean già pubblicato su GCS)
DL_PREFIX = "open-politica/"
DL_SLUG = "decreti_legge"


def _mart_url(table: str, year: int = YEAR) -> str:
    """URL parquet mart: locale (out/data/mart/...) se presente, altrimenti GCS."""
    if LOCAL_ROOT:
        rel = resolve("mart_parquet", slug=SLUG, year=str(year), table=table)
        local = Path(LOCAL_ROOT) / "mart" / rel
        if local.is_file():
            return str(local)
    return https_url(
        "mart", "mart_parquet", prefix=PREFIX, slug=SLUG, year=str(year), table=table
    )


def _dl_url(year: int = YEAR) -> str:
    """URL clean compose decreti_legge (solo GCS)."""
    return https_url(
        "clean",
        "clean_parquet",
        prefix=DL_PREFIX,
        slug=DL_SLUG,
        year=str(year),
    )


@st.cache_data(ttl=3600, show_spinner=False)
def load_mart(table: str, year: int = YEAR) -> pd.DataFrame:
    """Carica un singolo mart table (cached 1h)."""
    return _load_mart_table(SLUG, table, year, prefix=PREFIX, local_root=LOCAL_ROOT)


@st.cache_data(ttl=3600, show_spinner=False)
def mart_sql(sql: str) -> pd.DataFrame:
    """SQL sui mart con alias comodi: nodes, edges, metrics, search_keys,
    node_rel, emend_leg. Cached 1h."""
    with safe_connect() as con:
        for table in MART_TABLES:
            alias = _VIEW_ALIASES.get(table, table.removeprefix("mart_legal_"))
            con.execute(
                f"CREATE OR REPLACE VIEW {alias} "
                f"AS SELECT * FROM read_parquet('{_mart_url(table)}')"
            )
        return con.execute(sql).df()


@st.cache_data(ttl=3600, show_spinner=False)
def decreti_sql(sql: str) -> pd.DataFrame:
    """SQL sul clean compose decreti_legge (OP). Cached 1h."""
    with safe_connect() as con:
        con.execute(
            f"CREATE OR REPLACE VIEW dl AS SELECT * FROM read_parquet('{_dl_url()}')"
        )
        return con.execute(sql).df()


# ── Costanti SQL riusate dalle pagine ──────────────────────────────

LEG_CASE = """
    CASE
        WHEN dl_anno BETWEEN 1996 AND 2000 THEN 'L13'
        WHEN dl_anno BETWEEN 2001 AND 2005 THEN 'L14'
        WHEN dl_anno BETWEEN 2006 AND 2007 THEN 'L15'
        WHEN dl_anno BETWEEN 2008 AND 2012 THEN 'L16'
        WHEN dl_anno BETWEEN 2013 AND 2017 THEN 'L17'
        WHEN dl_anno BETWEEN 2018 AND 2021 THEN 'L18'
        WHEN dl_anno BETWEEN 2022 AND 2026 THEN 'L19'
    END
"""

DETTAGLI_ATTO_SQL = """
    SELECT e.relation,
           CASE WHEN e.source_id = :id THEN 'out' ELSE 'in' END AS dir,
           COUNT(*) AS n
    FROM edges e
    WHERE e.source_id = :id OR e.target_id = :id
    GROUP BY 1, 2
"""
