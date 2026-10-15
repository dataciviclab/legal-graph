"""Fonti dati per la dashboard Legal Graph.

Wrappa lab_connectors (path contract mart) con @st.cache_data.
Layout GCS: gs://dataciviclab-mart/legal-graph/legal_graph/2026/mart_legal_*.parquet
Locale (fallback): out/data/mart/legal_graph/2026/ (gitignored, make run).
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

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

# Relazioni della catena legislativa (allineate a legal_graph.mcp_server._CHAIN_RELS)
CHAIN_RELS = (
    "diventa_legge",
    "attua_delega",
    "recepisce_direttiva",
    "attua_regolamento",
    "collega_ue",
    "cita_costituzione",
    "abroga",
    "sostituisce",
    "split",
    "join",
    "renumbering",
)

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


def _fold(text: str) -> str:
    """Minuscole + rimozione accenti (responsabilità → responsabilita)."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


@st.cache_data(ttl=3600, show_spinner=False)
def load_mart(table: str, year: int = YEAR) -> pd.DataFrame:
    """Carica un singolo mart table (cached 1h)."""
    return _load_mart_table(SLUG, table, year, prefix=PREFIX, local_root=LOCAL_ROOT)


@st.cache_data(ttl=3600, show_spinner=False)
def mart_sql(sql: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
    """SQL sui mart con alias comodi: nodes, edges, metrics, search_keys,
    node_rel, emend_leg. Cached 1h. Parametri DuckDB con placeholder `?`."""
    with safe_connect() as con:
        for table in MART_TABLES:
            alias = _VIEW_ALIASES.get(table, table.removeprefix("mart_legal_"))
            con.execute(
                f"CREATE OR REPLACE VIEW {alias} "
                f"AS SELECT * FROM read_parquet('{_mart_url(table)}')"
            )
        return con.execute(sql, list(params) if params else None).df()


@st.cache_data(ttl=3600, show_spinner=False)
def decreti_sql(sql: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
    """SQL sul clean compose decreti_legge (OP). Cached 1h."""
    with safe_connect() as con:
        con.execute(
            f"CREATE OR REPLACE VIEW dl AS SELECT * FROM read_parquet('{_dl_url()}')"
        )
        return con.execute(sql, list(params) if params else None).df()


@st.cache_data(ttl=3600, show_spinner=False)
def search_acts(query: str, limit: int = 40) -> pd.DataFrame:
    """Ricerca atti su search_keys (stessa logica ranking del MCP legal_search).

    Intent semplificato:
    - id esatto → primo
    - NNN/YYYY o NNN/YY → id_num + id_year, oppure id/URN, oppure titolo
    - frase → title_folded / search_text (folded, LIKE)
    """
    q = (query or "").strip()
    if not q:
        return pd.DataFrame()
    folded = _fold(q)
    like = f"%{folded}%"

    # NNN/YYYY | NNN/YY | n. NNN/YYYY
    num_m = re.search(r"\b(\d{1,4})\s*/\s*(\d{2,4})\b", q)
    # URN / id grezzo (contiene ':' o inizia con tipo noto)
    id_like = f"%{q}%"

    select = """
        SELECT k.id, k.tipo, k.title, k.source, k.anno,
               n.stato, n.materia, n.qualita_score,
               COALESCE(m.referenced_by, 0) AS referenced_by,
               COALESCE(m.impact_score, 0) AS impact_score
        FROM search_keys k
        LEFT JOIN nodes n ON n.id = k.id
        LEFT JOIN metrics m ON m.id = k.id
    """
    # Ranking allineato a mcp_server._base_order: id esatto → atti normativi
    # → Cost. → resto → referenced_by → anno
    order = """
        ORDER BY
            CASE WHEN k.id = ? THEN 0 ELSE 1 END,
            CASE
                WHEN k.tipo IN ('DECRETO LEGISLATIVO', 'LEGGE', 'DECRETO-LEGGE', 'DECRETO')
                    THEN 0
                WHEN k.source = 'costituzione' THEN 1
                ELSE 2
            END,
            COALESCE(m.referenced_by, 0) DESC,
            k.anno DESC NULLS LAST,
            k.title
    """

    if num_m:
        num, year = num_m.group(1), num_m.group(2)
        if len(year) == 2:
            year = f"20{year}" if int(year) <= 99 else year
        sql = (
            select
            + """
            WHERE k.id = ?
               OR (k.id_num = ? AND k.id_year = ?)
               OR k.id LIKE ?
               OR LOWER(k.title_folded) LIKE ?
            """
            + order
            + " LIMIT ?"
        )
        # placeholder: WHERE id, id_num, id_year, id LIKE, title_folded,
        #              ORDER CASE id, LIMIT
        params: tuple[Any, ...] = (q, num, year, id_like, like, q, limit)
    else:
        # Frase multi-parola → AND (come MCP _phrase_where mode=and)
        terms = [t for t in re.split(r"\s+", folded) if t]
        if len(terms) <= 1:
            sql = (
                select
                + """
                WHERE LOWER(k.title_folded) LIKE ?
                   OR LOWER(k.search_text) LIKE ?
                   OR k.id ILIKE ?
                """
                + order
                + " LIMIT ?"
            )
            params = (like, like, id_like, q, limit)
        else:
            and_clauses = " AND ".join(
                "(LOWER(k.title_folded) LIKE ? OR LOWER(k.search_text) LIKE ?)"
                for _ in terms
            )
            sql = (
                select
                + f"WHERE ({and_clauses}) OR k.id ILIKE ?" + order + " LIMIT ?"
            )
            term_params: list[Any] = []
            for t in terms:
                term_params.extend([f"%{t}%", f"%{t}%"])
            params = (*term_params, id_like, q, limit)

    try:
        return mart_sql(sql, params)
    except Exception:  # noqa: BLE001 — search_keys assente → fallback nodi
        return mart_sql(
            """
            SELECT id, tipo, title, source, anno, stato, materia, qualita_score,
                   NULL AS referenced_by, NULL AS impact_score
            FROM nodes
            WHERE id ILIKE ? OR LOWER(COALESCE(title, '')) LIKE ?
            ORDER BY COALESCE(qualita_score, 0) DESC, anno DESC NULLS LAST
            LIMIT ?
            """,
            (id_like, like, limit),
        )


def chain_edges(node_id: str, direction: str, depth: int = 2) -> pd.DataFrame:
    """Catena legislativa BFS (allineata a mcp_server._view_chain), 1 query per hop.

    Restituisce always other_id = nodo oltre il centro, from_id/to_id = estremi arco.
    direction: 'forward' = archi uscenti dal centro; 'backward' = archi in entrata.
    """
    rels = ",".join("?" for _ in CHAIN_RELS)
    visited = {node_id}
    frontier = [node_id]
    rows: list[dict[str, Any]] = []
    for _ in range(max(1, min(depth, 4))):
        if not frontier:
            break
        next_frontier: list[str] = []
        for current in frontier:
            if direction == "forward":
                # source_id = centro → target_id = other
                sql = f"""
                    SELECT e.source_id AS from_id, e.relation, e.target_id AS other_id,
                           e.weight, n.tipo, LEFT(n.title, 100) AS title, n.anno
                    FROM edges e
                    LEFT JOIN nodes n ON n.id = e.target_id
                    WHERE e.source_id = ? AND e.relation IN ({rels})
                    ORDER BY e.weight DESC LIMIT 8
                """
            else:
                # target_id = centro → source_id = other; from/to invertiti
                sql = f"""
                    SELECT e.source_id AS other_id, e.relation, e.target_id AS from_id,
                           e.weight, n.tipo, LEFT(n.title, 100) AS title, n.anno
                    FROM edges e
                    LEFT JOIN nodes n ON n.id = e.source_id
                    WHERE e.target_id = ? AND e.relation IN ({rels})
                    ORDER BY e.weight DESC LIMIT 8
                """
            df = mart_sql(sql, (current, *CHAIN_RELS))
            for r in df.itertuples(index=False):
                other = r.other_id
                if not other or other in visited:
                    continue
                visited.add(other)
                next_frontier.append(other)
                rows.append(
                    {
                        # arco reale: forward = centro→other; backward = other→centro
                        "from_id": r.from_id if direction == "forward" else other,
                        "relation": r.relation,
                        "to_id": other if direction == "forward" else r.from_id,
                        "other_id": other,
                        "tipo": r.tipo,
                        "title": r.title,
                        "anno": r.anno,
                        "weight": r.weight,
                    }
                )
        frontier = next_frontier
    out = pd.DataFrame(rows)
    if not out.empty:
        out.insert(0, "hop", range(1, len(out) + 1))
    return out


def node_metrics(node_id: str) -> pd.DataFrame:
    """Metrics del nodo (referenced_by, impact). `references` = reserved keyword."""
    return mart_sql(
        'SELECT referenced_by, impact_score, impact_level, "references" '
        "FROM metrics WHERE id = ?",
        (node_id,),
    )


def relation_counts(node_id: str) -> pd.DataFrame:
    """Conteggi relazioni out/in per il nodo (usa node_rel se presente)."""
    edges_sql = """
        SELECT relation,
               SUM(CASE WHEN source_id = ? THEN 1 ELSE 0 END) AS n_out,
               SUM(CASE WHEN target_id = ? THEN 1 ELSE 0 END) AS n_in
        FROM edges
        WHERE source_id = ? OR target_id = ?
        GROUP BY 1 ORDER BY (n_out + n_in) DESC LIMIT 15
    """
    try:
        df = mart_sql(
            """
            SELECT relation, n_out, n_in FROM node_rel WHERE id = ?
            ORDER BY (n_out + n_in) DESC LIMIT 15
            """,
            (node_id,),
        )
        if not df.empty:
            return df
    except Exception:  # noqa: BLE001 — node_rel assente
        return mart_sql(edges_sql, (node_id, node_id, node_id, node_id))
    return mart_sql(edges_sql, (node_id, node_id, node_id, node_id))


def partners(node_id: str, direction: str, limit: int = 12) -> pd.DataFrame:
    """Top partner out (source_id=node) o in (target_id=node)."""
    if direction == "out":
        return mart_sql(
            """
            SELECT e.target_id AS other_id, n.tipo, LEFT(n.title, 90) AS title,
                   e.relation, e.weight, e.evidence
            FROM edges e LEFT JOIN nodes n ON n.id = e.target_id
            WHERE e.source_id = ?
            ORDER BY e.weight DESC LIMIT ?
            """,
            (node_id, limit),
        )
    return mart_sql(
        """
        SELECT e.source_id AS other_id, n.tipo, LEFT(n.title, 90) AS title,
               e.relation, e.weight, e.evidence
        FROM edges e LEFT JOIN nodes n ON n.id = e.source_id
        WHERE e.target_id = ?
        ORDER BY e.weight DESC LIMIT ?
        """,
        (node_id, limit),
    )


def corte_panel(node_id: str) -> dict[str, pd.DataFrame]:
    """Pannello giurisprudenza per un atto/sentenza (vista MCP jurisprudence)."""
    return {
        "impugna": mart_sql(
            """
            SELECT e.target_id AS other_id, n.tipo, LEFT(n.title, 100) AS title,
                   n.anno, e.weight
            FROM edges e LEFT JOIN nodes n ON n.id = e.target_id
            WHERE e.source_id = ? AND e.relation = 'impugna'
            ORDER BY e.weight DESC LIMIT 15
            """,
            (node_id,),
        ),
        "impugnata_da": mart_sql(
            """
            SELECT e.source_id AS other_id, n.tipo, LEFT(n.title, 100) AS title,
                   n.anno, e.weight
            FROM edges e LEFT JOIN nodes n ON n.id = e.source_id
            WHERE e.target_id = ? AND e.relation = 'impugna'
            ORDER BY e.weight DESC LIMIT 15
            """,
            (node_id,),
        ),
        "relatore": mart_sql(
            """
            SELECT e.target_id AS other_id, LEFT(n.title, 100) AS title, e.evidence
            FROM edges e LEFT JOIN nodes n ON n.id = e.target_id
            WHERE e.source_id = ? AND e.relation = 'relatore_sentenza'
            LIMIT 5
            """,
            (node_id,),
        ),
    }


def parliament_panel(node_id: str) -> dict[str, Any]:
    """Pannello parlamento: emendamenti, firmatari, relatore (vista MCP parliament)."""
    n_emend: int | None
    try:
        n_emend = int(
            mart_sql(
                "SELECT COALESCE(SUM(n_emend), 0) AS n FROM emend_leg WHERE target_id = ?",
                (node_id,),
            )["n"].iloc[0]
        )
    except Exception:  # noqa: BLE001 — emend_leg assente
        n_emend = None
    firmatari = mart_sql(
        """
        SELECT e.source_id AS other_id, LEFT(n.title, 100) AS title,
               e.weight, e.evidence
        FROM edges e LEFT JOIN nodes n ON n.id = e.source_id
        WHERE e.target_id = ? AND e.relation = 'firmatario'
        ORDER BY e.weight DESC, e.source_id LIMIT 15
        """,
        (node_id,),
    )
    relatore = mart_sql(
        """
        SELECT e.target_id AS other_id, LEFT(n.title, 100) AS title
        FROM edges e LEFT JOIN nodes n ON n.id = e.target_id
        WHERE e.source_id = ? AND e.relation = 'relatore'
        LIMIT 5
        """,
        (node_id,),
    )
    return {"n_emend": n_emend, "firmatari": firmatari, "relatore": relatore}


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
