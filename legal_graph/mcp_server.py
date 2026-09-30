"""Server MCP legal-graph — Legal Knowledge Graph per agenti AI.

Flusso consigliato (5 tool):
  1. legal_search     → trova l'atto
  2. legal_node       → contesto + vista specialistica (view=)
  3. legal_text       → testo integrale (normativa)
  4. legal_query      → SQL arbitrario (escape hatch)
  5. legal_insights   → report di sistema (critici/obsoleti)

View di legal_node:
  overview      metadati + conteggi relazioni + top edge + intelligence
  chain         catena legislativa (DDL→legge→D.Lgs→UE)
  jurisprudence Corte Costituzionale (impugna, parametri, citazioni)
  parliament    emendamenti, interventi, diventa_legge
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import duckdb
from lab_connectors.mcp import create_mcp_server, guard_timed

from legal_graph.legal_text import fetch_normativa_text
from legal_graph.legal_text import resolve_node as resolve_text_node
from legal_graph.paths import (
    resolve_edges_file,
    resolve_metrics_file,
    resolve_nodes_file,
    resolve_temporal_file,
)

NODES_FILE = resolve_nodes_file()
EDGES_FILE = resolve_edges_file()
TEMPORAL_FILE = resolve_temporal_file()
METRICS_FILE = resolve_metrics_file()

_MAX_ROWS = 100
_NODE_VIEWS = ("overview", "chain", "jurisprudence", "parliament")
_CHAIN_RELS = (
    "diventa_legge",
    "attua_delega",
    "recepisce_direttiva",
    "attua_regolamento",
    "collega_ue",
    "cita_costituzione",
)

_cached_con: duckdb.DuckDBPyConnection | None = None


def _get_con() -> duckdb.DuckDBPyConnection:
    """Connessione DuckDB cachata con VIEW sui mart compose (fallback legacy)."""
    global _cached_con
    if _cached_con is not None:
        return _cached_con
    nodes_file = resolve_nodes_file()
    edges_file = resolve_edges_file()
    temporal_file = resolve_temporal_file()
    con = duckdb.connect(":memory:")
    con.execute("SET memory_limit='256MB'")
    con.execute("SET threads=1")
    con.execute(f"CREATE OR REPLACE VIEW nodes AS SELECT * FROM read_parquet('{nodes_file}')")
    con.execute(f"CREATE OR REPLACE VIEW edges AS SELECT * FROM read_parquet('{edges_file}')")
    if temporal_file is not None:
        con.execute(
            f"CREATE OR REPLACE VIEW temporal AS SELECT * FROM read_parquet('{temporal_file}')"
        )
    _cached_con = con
    return con


mcp = create_mcp_server(
    name="legal-graph",
    instructions=(
        "Legal Knowledge Graph — grafo del diritto italiano (compose toolkit + MCP).\n"
        "Flusso:\n"
        "  1) legal_search per trovare un atto (URN o titolo)\n"
        "  2) legal_node(node_id, view) per contesto/viste:\n"
        "     overview | chain | jurisprudence | parliament\n"
        "  3) legal_text(node_id) per il testo integrale (solo source=normativa)\n"
        "  4) legal_query per SQL (SELECT only) su nodes/edges\n"
        "  5) legal_insights per report di sistema (critici/obsoleti)\n"
        "Preferisci legal_node + legal_text; usa query solo se serve analisi ad hoc."
    ),
)


# ─── helpers ─────────────────────────────────────────────────────


def _find_node(con: duckdb.DuckDBPyConnection, node_id: str) -> tuple | None:
    return con.execute(
        """
        SELECT id, tipo, title, CAST(data AS VARCHAR) AS data, anno, source,
               length_chars, length_words, celex, collezione, source_filename
        FROM nodes
        WHERE id = ? OR id LIKE ?
        ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END, length(id)
        LIMIT 1
        """,
        [node_id, f"%{node_id}%", node_id],
    ).fetchone()


def _node_payload(row: tuple) -> dict[str, Any]:
    return {
        "id": row[0],
        "tipo": row[1],
        "title": (row[2] or "")[:200],
        "data": row[3],
        "anno": row[4],
        "source": row[5],
        "length_chars": row[6],
        "length_words": row[7],
        "celex": row[8],
        "collezione": row[9],
        "source_filename": row[10],
    }


def _relation_counts(con: duckdb.DuckDBPyConnection, actual_id: str) -> dict[str, Any]:
    out = dict(
        con.execute(
            """
            SELECT relation, COUNT(*) FROM edges WHERE source_id = ?
            GROUP BY 1 ORDER BY 2 DESC LIMIT 12
            """,
            [actual_id],
        ).fetchall()
    )
    inn = dict(
        con.execute(
            """
            SELECT relation, COUNT(*) FROM edges WHERE target_id = ?
            GROUP BY 1 ORDER BY 2 DESC LIMIT 12
            """,
            [actual_id],
        ).fetchall()
    )
    return {"out": out, "in": inn}


def _top_edges(
    con: duckdb.DuckDBPyConnection,
    *,
    column: str,
    actual_id: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Top edge in uscita (source_id) o in entrata (target_id)."""
    join_col = "target_id" if column == "source_id" else "source_id"
    rows = con.execute(
        f"""
        SELECT e.relation, e.{join_col}, e.weight, e.source_year, e.target_year,
               n.tipo, LEFT(n.title, 90)
        FROM edges e
        LEFT JOIN nodes n ON n.id = e.{join_col}
        WHERE e.{column} = ?
        ORDER BY e.weight DESC
        LIMIT ?
        """,
        [actual_id, limit],
    ).fetchall()
    key = "target" if column == "source_id" else "source"
    return [
        {
            "relation": r[0],
            key: r[1],
            "weight": r[2],
            "source_year": r[3],
            "target_year": r[4],
            "tipo": r[5],
            "title": r[6],
        }
        for r in rows
    ]


def _node_intelligence(actual_id: str) -> dict[str, Any] | None:
    if METRICS_FILE is None or not METRICS_FILE.exists():
        return None
    con = duckdb.connect(":memory:")
    try:
        row = con.execute(
            f"""
            SELECT referenced_by, impact_score, impact_level, "references",
                   complexity_level, age_years, age_risk, activity_level
            FROM read_parquet('{METRICS_FILE}')
            WHERE id = ?
            """,
            [actual_id],
        ).fetchone()
    finally:
        con.close()
    if not row:
        return None
    return {
        "referenced_by": row[0],
        "impact_score": row[1],
        "impact_level": row[2],
        "references": row[3],
        "complexity_level": row[4],
        "age_years": row[5],
        "age_risk": row[6],
        "activity_level": row[7],
    }


def _view_overview(con: duckdb.DuckDBPyConnection, node_row: tuple) -> dict[str, Any]:
    actual_id = node_row[0]
    return {
        "view": "overview",
        "node": _node_payload(node_row),
        "relation_counts": _relation_counts(con, actual_id),
        "top_outgoing": _top_edges(con, column="source_id", actual_id=actual_id),
        "top_incoming": _top_edges(con, column="target_id", actual_id=actual_id),
        "intelligence": _node_intelligence(actual_id),
        "next": [
            "legal_text(node_id) per il testo (se source=normativa)",
            "legal_node(node_id, view='chain'|'jurisprudence'|'parliament')",
        ],
    }


def _view_chain(con: duckdb.DuckDBPyConnection, node_row: tuple, depth: int) -> dict[str, Any]:
    actual_id = node_row[0]
    depth = max(1, min(int(depth or 3), 5))
    forward: list[dict[str, Any]] = []
    visited = {actual_id}
    queue: list[tuple[str, int]] = [(actual_id, 0)]
    while queue:
        current, d = queue.pop(0)
        if d >= depth:
            continue
        edges = con.execute(
            """
            SELECT relation, target_id, weight FROM edges
            WHERE source_id = ? AND relation IN (?,?,?,?,?,?)
            ORDER BY weight DESC LIMIT 8
            """,
            [current, *_CHAIN_RELS],
        ).fetchall()
        for rel, target, weight in edges:
            if target in visited:
                continue
            visited.add(target)
            tnode = con.execute(
                "SELECT tipo, title, anno FROM nodes WHERE id = ? LIMIT 1", [target]
            ).fetchone()
            forward.append(
                {
                    "relation": rel,
                    "target_id": target,
                    "target_tipo": tnode[0] if tnode else "?",
                    "target_title": (tnode[1] or "")[:100] if tnode else "",
                    "target_anno": tnode[2] if tnode else None,
                    "weight": weight,
                }
            )
            queue.append((target, d + 1))

    backward: list[dict[str, Any]] = []
    visited_b = {actual_id}
    queue_b: list[tuple[str, int]] = [(actual_id, 0)]
    while queue_b:
        current, d = queue_b.pop(0)
        if d >= depth:
            continue
        edges = con.execute(
            """
            SELECT relation, source_id, weight FROM edges
            WHERE target_id = ? AND relation IN (?,?,?,?,?,?)
            ORDER BY weight DESC LIMIT 8
            """,
            [current, *_CHAIN_RELS],
        ).fetchall()
        for rel, source, weight in edges:
            if source in visited_b:
                continue
            visited_b.add(source)
            snode = con.execute(
                "SELECT tipo, title, anno FROM nodes WHERE id = ? LIMIT 1", [source]
            ).fetchone()
            backward.append(
                {
                    "relation": rel,
                    "source_id": source,
                    "source_tipo": snode[0] if snode else "?",
                    "source_title": (snode[1] or "")[:100] if snode else "",
                    "source_anno": snode[2] if snode else None,
                    "weight": weight,
                }
            )
            queue_b.append((source, d + 1))

    temporal = []
    if TEMPORAL_FILE is not None and TEMPORAL_FILE.exists():
        temporal = con.execute(
            "SELECT relation, evidence FROM temporal WHERE source_id = ? LIMIT 5",
            [actual_id],
        ).fetchall()

    return {
        "view": "chain",
        "depth": depth,
        "node": _node_payload(node_row),
        "chain_forward": forward,
        "chain_backward": backward,
        "temporal": [{"relation": t[0], "evidence": t[1]} for t in temporal],
    }


def _view_jurisprudence(con: duckdb.DuckDBPyConnection, node_row: tuple) -> dict[str, Any]:
    actual_id = node_row[0]
    impugna = con.execute(
        """
        SELECT e.target_id, n.tipo, LEFT(n.title, 100), n.anno, e.weight
        FROM edges e JOIN nodes n ON e.target_id = n.id
        WHERE e.source_id = ? AND e.relation = 'impugna'
        ORDER BY e.weight DESC LIMIT 15
        """,
        [actual_id],
    ).fetchall()
    impugnata_da = con.execute(
        """
        SELECT e.source_id, n.tipo, LEFT(n.title, 100), n.anno, e.weight
        FROM edges e JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'impugna'
        ORDER BY e.weight DESC LIMIT 15
        """,
        [actual_id],
    ).fetchall()
    parametri = con.execute(
        """
        SELECT e.target_id, LEFT(n.title, 80), e.weight
        FROM edges e JOIN nodes n ON e.target_id = n.id
        WHERE e.source_id = ? AND e.relation = 'invoca_parametro'
        ORDER BY e.weight DESC LIMIT 15
        """,
        [actual_id],
    ).fetchall()
    citata_da = con.execute(
        """
        SELECT e.source_id, n.tipo, LEFT(n.title, 100), n.anno, e.weight
        FROM edges e JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'cita_costituzione'
        ORDER BY e.weight DESC LIMIT 15
        """,
        [actual_id],
    ).fetchall()
    return {
        "view": "jurisprudence",
        "node": _node_payload(node_row),
        "impugna": [
            {"id": r[0], "tipo": r[1], "title": r[2], "anno": r[3], "weight": r[4]}
            for r in impugna
        ],
        "impugnata_da": [
            {"id": r[0], "tipo": r[1], "title": r[2], "anno": r[3], "weight": r[4]}
            for r in impugnata_da
        ],
        "parametri_invocati": [
            {"articolo": r[0], "title": r[1], "weight": r[2]} for r in parametri
        ],
        "citata_da": [
            {"id": r[0], "tipo": r[1], "title": r[2], "anno": r[3], "weight": r[4]}
            for r in citata_da
        ],
    }


def _view_parliament(con: duckdb.DuckDBPyConnection, node_row: tuple) -> dict[str, Any]:
    actual_id = node_row[0]
    emendamenti = con.execute(
        """
        SELECT e.source_id, LEFT(n.title, 100), n.anno, e.weight
        FROM edges e JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'emendamento'
        ORDER BY e.weight DESC LIMIT 15
        """,
        [actual_id],
    ).fetchall()
    top_emendatori = con.execute(
        """
        SELECT n.title, COUNT(*) AS cnt
        FROM edges e JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'emendamento'
        GROUP BY 1 ORDER BY 2 DESC LIMIT 8
        """,
        [actual_id],
    ).fetchall()
    n_interventi = con.execute(
        "SELECT COUNT(*) FROM edges WHERE target_id = ? AND relation = 'intervento'",
        [actual_id],
    ).fetchone()[0]
    interventi = con.execute(
        """
        SELECT e.source_id, LEFT(n.title, 80), n.anno
        FROM edges e JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'intervento'
        ORDER BY n.anno DESC LIMIT 10
        """,
        [actual_id],
    ).fetchall()
    diventa = con.execute(
        """
        SELECT e.target_id, LEFT(n.title, 100), n.anno
        FROM edges e JOIN nodes n ON e.target_id = n.id
        WHERE e.source_id = ? AND e.relation = 'diventa_legge'
        LIMIT 5
        """,
        [actual_id],
    ).fetchall()
    testo = con.execute(
        """
        SELECT e.target_id, LEFT(n.title, 100)
        FROM edges e JOIN nodes n ON e.target_id = n.id
        WHERE e.source_id = ? AND e.relation = 'testo_atto'
        LIMIT 5
        """,
        [actual_id],
    ).fetchall()
    return {
        "view": "parliament",
        "node": _node_payload(node_row),
        "n_emendamenti": len(emendamenti),
        "emendamenti": [
            {"id": r[0], "title": r[1], "anno": r[2], "weight": r[3]} for r in emendamenti
        ],
        "top_emendatori": [
            {"autore": r[0], "n_emendamenti": r[1]} for r in top_emendatori
        ],
        "n_interventi": n_interventi,
        "interventi": [{"id": r[0], "title": r[1], "anno": r[2]} for r in interventi],
        "diventa_legge": [{"id": r[0], "title": r[1], "anno": r[2]} for r in diventa],
        "testo": [{"id": r[0], "title": r[1]} for r in testo],
    }


# ─── Tool 1: legal_search ────────────────────────────────────────


@mcp.tool(
    name="legal_search",
    description=(
        "Trova atti/nodi nel grafo. Multi-parola = AND; frase tra virgolette = LIKE intero. "
        "Filtri: tipo, anno_min/anno_max, source (normativa/senato/costituzione/...). "
        "Usa il risultato come input di legal_node / legal_text."
    ),
    structured_output=True,
)
def legal_search(
    query: str,
    tipo: str = "",
    anno_min: int = 0,
    anno_max: int = 0,
    source: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    return guard_timed(
        _impl_search,
        "legal_search",
        query,
        tipo=tipo,
        anno_min=anno_min,
        anno_max=anno_max,
        source=source,
        limit=limit,
    )


def _impl_search(
    query: str,
    tipo: str = "",
    anno_min: int = 0,
    anno_max: int = 0,
    source: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    con = _get_con()
    limit = min(max(limit, 1), _MAX_ROWS)

    conditions = ["(anno IS NULL OR anno <= ?)"]
    params: list[Any] = [datetime.now(UTC).year + 1]

    q = (query or "").strip()
    if q:
        if (q.startswith('"') and q.endswith('"')) or (q.startswith("'") and q.endswith("'")):
            terms = [q[1:-1]]
        else:
            terms = [w for w in q.split() if w][:8]
        for term in terms:
            conditions.append("(LOWER(title) LIKE ? OR LOWER(id) LIKE ?)")
            params.extend([f"%{term.lower()}%", f"%{term.lower()}%"])
    if tipo:
        conditions.append("UPPER(tipo) = ?")
        params.append(tipo.upper())
    if anno_min > 0:
        conditions.append("anno >= ?")
        params.append(anno_min)
    if anno_max > 0:
        conditions.append("anno <= ?")
        params.append(anno_max)
    if source:
        conditions.append("source = ?")
        params.append(source)

    rows = con.execute(
        f"""
        SELECT id, tipo, title, CAST(data AS VARCHAR), anno, source
        FROM nodes
        WHERE {' AND '.join(conditions)}
        ORDER BY anno DESC NULLS LAST, title
        LIMIT {limit}
        """,
        params,
    ).fetchall()
    return [
        {
            "id": r[0],
            "tipo": r[1],
            "title": (r[2] or "")[:100],
            "data": r[3],
            "anno": r[4],
            "source": r[5],
            "next": f"legal_node(node_id='{r[0]}')",
        }
        for r in rows
    ]


# ─── Tool 2: legal_node ──────────────────────────────────────────


@mcp.tool(
    name="legal_node",
    description=(
        "Contesto di un nodo del grafo. view:\n"
        "  overview (default) — metadati, conteggi relazioni, top edge, intelligence\n"
        "  chain — catena legislativa forward/backward\n"
        "  jurisprudence — impugnazioni, parametri Cost., citazioni\n"
        "  parliament — emendamenti, interventi, diventa_legge\n"
        "Input: URN completo o id parziale (es. 'decreto.legislativo:2017-07-03;117')."
    ),
    structured_output=True,
)
def legal_node(node_id: str, view: str = "overview", depth: int = 3) -> dict[str, Any]:
    return guard_timed(_impl_node, "legal_node", node_id, view=view, depth=depth)


def _impl_node(node_id: str, view: str = "overview", depth: int = 3) -> dict[str, Any]:
    view = (view or "overview").lower().strip()
    if view not in _NODE_VIEWS:
        return {"error": f"view non valida: {view!r}. Usa una di {_NODE_VIEWS}"}

    con = _get_con()
    node_row = _find_node(con, node_id)
    if not node_row:
        return {"error": f"Nodo non trovato: {node_id}"}

    if view == "overview":
        return _view_overview(con, node_row)
    if view == "chain":
        return _view_chain(con, node_row, depth)
    if view == "jurisprudence":
        return _view_jurisprudence(con, node_row)
    return _view_parliament(con, node_row)


# ─── Tool 3: legal_text ──────────────────────────────────────────


@mcp.tool(
    name="legal_text",
    description=(
        "Testo integrale di un atto normativa da nodo/URN (italia-corpus, GitHub raw). "
        "Copre solo source=normativa. Altre source: errore strutturato."
    ),
    structured_output=True,
)
def legal_text(node_id: str, max_chars: int = 8000) -> dict[str, Any]:
    return guard_timed(_impl_legal_text, "legal_text", node_id, max_chars=max_chars)


def _impl_legal_text(node_id: str, max_chars: int = 8000) -> dict[str, Any]:
    node = resolve_text_node(node_id)
    if not node:
        return {"error": f"Nodo non trovato: {node_id}"}

    base = {
        "id": node["id"],
        "tipo": node["tipo"],
        "title": (node.get("title") or "")[:200],
        "data": node.get("data"),
        "anno": node.get("anno"),
        "source": node.get("source"),
        "collezione": node.get("collezione"),
        "source_filename": node.get("source_filename"),
    }

    source = node.get("source") or ""
    if source != "normativa":
        return {
            **base,
            "error": (
                f"legal_text copre solo source=normativa (oggi {source!r}). "
                "Per senato-akn/costituzione usare toolkit o i parquet clean."
            ),
        }

    result = fetch_normativa_text(
        collezione=node.get("collezione") or "",
        filename=node.get("source_filename") or "",
        max_chars=max_chars,
    )
    return {**base, **result}


# ─── Tool 4: legal_query ─────────────────────────────────────────


@mcp.tool(
    name="legal_query",
    description=(
        "SQL sul grafo (solo SELECT). Tabelle: nodes, edges, [temporal]. "
        "Per stats/conteggi usa aggregazioni SQL. Per un nodo usa legal_node."
    ),
    structured_output=True,
)
def legal_query(sql: str, limit: int = 50) -> list[dict[str, Any]]:
    return guard_timed(_impl_query, "legal_query", sql, limit=limit)


def _impl_query(sql: str, limit: int = 50) -> list[dict[str, Any]]:
    con = _get_con()
    limit = min(max(limit, 1), _MAX_ROWS)
    sql_clean = (sql or "").strip()
    if not sql_clean.upper().startswith("SELECT"):
        return [{"error": "Solo SELECT consentito"}]
    if "LIMIT" not in sql_clean.upper():
        sql_clean = f"{sql_clean.rstrip()} LIMIT {limit}"
    try:
        result = con.execute(sql_clean)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()[:limit]
    except duckdb.Error as e:
        return [{"error": str(e)[:200]}]
    return [dict(zip(columns, row)) for row in rows]


# ─── Tool 5: legal_insights ──────────────────────────────────────


@mcp.tool(
    name="legal_insights",
    description=(
        "Report di sistema sul grafo. report:\n"
        "  summary (default) | critical | obsolete | complex | dormant\n"
        "Richiede graph_intelligence (python -m legal_graph.graph_intelligence)."
    ),
    structured_output=True,
)
def legal_insights(report: str = "summary") -> dict[str, Any]:
    return guard_timed(_impl_insights, "legal_insights", report)


def _impl_insights(report: str = "summary") -> dict[str, Any]:
    if METRICS_FILE is None or not METRICS_FILE.exists():
        return {
            "error": "Metriche non calcolate. Esegui: python scripts/graph_intelligence.py",
            "hint": "Senza metrics, usa legal_query su edges per impatto grezzo.",
        }

    con = _get_con()
    con.execute(
        f"CREATE OR REPLACE VIEW metrics AS SELECT * FROM read_parquet('{METRICS_FILE}')"
    )
    report_type = (report or "summary").lower().strip()

    if report_type == "critical":
        rows = con.execute(
            """
            SELECT id, title, referenced_by, impact_score, age_years
            FROM metrics WHERE impact_level = 'critical'
            ORDER BY referenced_by DESC LIMIT 20
            """
        ).fetchall()
        return {
            "report": "critical",
            "nodes": [
                {"id": r[0], "title": (r[1] or r[0])[:70], "refs": r[2], "score": r[3], "age": r[4]}
                for r in rows
            ],
        }

    if report_type == "obsolete":
        rows = con.execute(
            """
            SELECT id, title, age_years, referenced_by
            FROM metrics WHERE age_risk = 'obsolete_candidate'
            ORDER BY age_years DESC LIMIT 20
            """
        ).fetchall()
        return {
            "report": "obsolete",
            "nodes": [
                {"id": r[0], "title": (r[1] or r[0])[:70], "age": r[2], "refs": r[3]} for r in rows
            ],
        }

    if report_type == "complex":
        rows = con.execute(
            """
            SELECT id, title, "references", referenced_by, age_years
            FROM metrics WHERE complexity_level IN ('very_complex', 'complex')
            ORDER BY "references" DESC LIMIT 20
            """
        ).fetchall()
        return {
            "report": "complex",
            "nodes": [
                {"id": r[0], "title": (r[1] or r[0])[:70], "deps": r[2], "refs": r[3], "age": r[4]}
                for r in rows
            ],
        }

    if report_type == "dormant":
        rows = con.execute(
            """
            SELECT id, title, last_referenced_year, referenced_by
            FROM metrics WHERE activity_level = 'dormant' AND referenced_by > 5
            ORDER BY referenced_by DESC LIMIT 20
            """
        ).fetchall()
        return {
            "report": "dormant",
            "nodes": [
                {"id": r[0], "title": (r[1] or r[0])[:70], "last_year": r[2], "refs": r[3]}
                for r in rows
            ],
        }

    stats = con.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN impact_level = 'critical' THEN 1 ELSE 0 END) AS critical,
            SUM(CASE WHEN impact_level = 'important' THEN 1 ELSE 0 END) AS important,
            SUM(CASE WHEN complexity_level = 'very_complex' THEN 1 ELSE 0 END) AS very_complex,
            SUM(CASE WHEN age_risk = 'obsolete_candidate' THEN 1 ELSE 0 END) AS obsolete,
            SUM(CASE WHEN activity_level = 'dormant' THEN 1 ELSE 0 END) AS dormant,
            ROUND(AVG(age_years), 1) AS avg_age
        FROM metrics
        """
    ).fetchone()
    critical = con.execute(
        """
        SELECT id, title, referenced_by FROM metrics
        WHERE impact_level = 'critical' ORDER BY referenced_by DESC LIMIT 5
        """
    ).fetchall()
    obsolete = con.execute(
        """
        SELECT id, title, age_years, referenced_by FROM metrics
        WHERE age_risk = 'obsolete_candidate' ORDER BY age_years DESC LIMIT 5
        """
    ).fetchall()
    return {
        "report": "summary",
        "summary": {
            "total_nodes": stats[0],
            "critical": stats[1],
            "important": stats[2],
            "very_complex": stats[3],
            "obsolete_candidates": stats[4],
            "dormant": stats[5],
            "avg_age_years": stats[6],
        },
        "top_critical": [
            {"id": r[0], "title": (r[1] or r[0])[:70], "refs": r[2]} for r in critical
        ],
        "top_obsolete": [
            {"id": r[0], "title": (r[1] or r[0])[:70], "age": r[2], "refs": r[3]} for r in obsolete
        ],
    }


# ─── main ────────────────────────────────────────────────────────


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
