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

import re
import unicodedata
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
_TITLE_SEARCH = 280
_TITLE_NODE = 500
_TITLE_EDGE = 120

_cached_con: duckdb.DuckDBPyConnection | None = None


def _fold(text: str) -> str:
    """Minuscole + rimozione accenti (responsabilità → responsabilita)."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


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
        "  4) legal_query per SQL (SELECT/PRAGMA/DESCRIBE) su nodes/edges\n"
        "  5) legal_insights per report di sistema (critici/obsoleti)\n"
        "Preferisci legal_node + legal_text; usa query solo se serve analisi ad hoc."
    ),
)


# ─── helpers ─────────────────────────────────────────────────────


def _find_node(con: duckdb.DuckDBPyConnection, node_id: str) -> tuple | None:
    """Preferisci match esatto; LIKE solo se l'id esatto non esiste."""
    exact = con.execute(
        """
        SELECT id, tipo, title, CAST(data AS VARCHAR) AS data, anno, source,
               length_chars, length_words, celex, collezione, source_filename
        FROM nodes WHERE id = ? LIMIT 1
        """,
        [node_id],
    ).fetchone()
    if exact:
        return exact
    return con.execute(
        """
        SELECT id, tipo, title, CAST(data AS VARCHAR) AS data, anno, source,
               length_chars, length_words, celex, collezione, source_filename
        FROM nodes
        WHERE id LIKE ?
        ORDER BY length(id)
        LIMIT 1
        """,
        [f"%{node_id}%"],
    ).fetchone()


def _node_payload(row: tuple) -> dict[str, Any]:
    return {
        "id": row[0],
        "tipo": row[1],
        "title": (row[2] or "")[:_TITLE_NODE],
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

    top_riferimenti_in: list[dict[str, Any]] = []
    top_riferimenti_out: list[dict[str, Any]] = []
    if not backward:
        rows_in = con.execute(
            """
            SELECT e.source_id, e.weight, n.tipo, LEFT(n.title, 120), n.anno
            FROM edges e JOIN nodes n ON n.id = e.source_id
            WHERE e.target_id = ? AND e.relation = 'riferimento'
            ORDER BY e.weight DESC LIMIT 5
            """,
            [actual_id],
        ).fetchall()
        top_riferimenti_in = [
            {"source_id": r[0], "weight": r[1], "tipo": r[2], "title": r[3], "anno": r[4]}
            for r in rows_in
        ]
    if not forward:
        rows_out = con.execute(
            """
            SELECT e.target_id, e.weight, n.tipo, LEFT(n.title, 120), n.anno
            FROM edges e JOIN nodes n ON n.id = e.target_id
            WHERE e.source_id = ? AND e.relation = 'riferimento'
            ORDER BY e.weight DESC LIMIT 5
            """,
            [actual_id],
        ).fetchall()
        top_riferimenti_out = [
            {"target_id": r[0], "weight": r[1], "tipo": r[2], "title": r[3], "anno": r[4]}
            for r in rows_out
        ]

    return {
        "view": "chain",
        "depth": depth,
        "node": _node_payload(node_row),
        "chain_forward": forward,
        "chain_backward": backward,
        "top_riferimenti_out": top_riferimenti_out,
        "top_riferimenti_in": top_riferimenti_in,
        "temporal": [{"relation": t[0], "evidence": t[1]} for t in temporal],
        "note": (
            "chain_* = relazioni legislative tipizzate. "
            "top_riferimenti_* = campione riferimento se la chain è vuota."
        ),
    }


def _view_jurisprudence(con: duckdb.DuckDBPyConnection, node_row: tuple) -> dict[str, Any]:
    actual_id = node_row[0]
    impugna = con.execute(
        """
        SELECT e.target_id, n.tipo, LEFT(n.title, 120), n.anno, e.weight
        FROM edges e JOIN nodes n ON e.target_id = n.id
        WHERE e.source_id = ? AND e.relation = 'impugna'
        ORDER BY e.weight DESC LIMIT 15
        """,
        [actual_id],
    ).fetchall()
    impugnata_da = con.execute(
        """
        SELECT e.source_id, n.tipo, LEFT(n.title, 120), n.anno, e.weight
        FROM edges e JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'impugna'
        ORDER BY e.weight DESC LIMIT 15
        """,
        [actual_id],
    ).fetchall()
    parametri = con.execute(
        """
        SELECT e.target_id, LEFT(n.title, 120), e.weight
        FROM edges e JOIN nodes n ON e.target_id = n.id
        WHERE e.source_id = ? AND e.relation = 'invoca_parametro'
        ORDER BY e.weight DESC LIMIT 15
        """,
        [actual_id],
    ).fetchall()
    citata_da = con.execute(
        """
        SELECT e.source_id, n.tipo, LEFT(n.title, 120), n.anno, e.weight
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
    n_emendamenti = con.execute(
        "SELECT COUNT(*) FROM edges WHERE target_id = ? AND relation = 'emendamento'",
        [actual_id],
    ).fetchone()[0]
    emendamenti = con.execute(
        """
        SELECT e.source_id, LEFT(n.title, 120), n.anno, e.weight
        FROM edges e JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'emendamento'
        ORDER BY e.weight DESC LIMIT 15
        """,
        [actual_id],
    ).fetchall()
    # I nodi emendamento hanno titolo unico per emendamento: raggruppare per
    # title dà sempre n=1. Utile: distribuzione per legislatura (dal source_id).
    top_emendatori = con.execute(
        """
        SELECT
          CAST(NULLIF(regexp_extract(e.source_id, 'senato:emend:(\\d+):', 1), '') AS INTEGER)
            AS legislatura,
          COUNT(*) AS cnt
        FROM edges e
        WHERE e.target_id = ? AND e.relation = 'emendamento'
        GROUP BY 1
        ORDER BY 2 DESC
        LIMIT 8
        """,
        [actual_id],
    ).fetchall()
    top_emendatori = [
        {"legislatura": r[0], "n_emendamenti": r[1]} for r in top_emendatori if r[0] is not None
    ]
    n_interventi = con.execute(
        "SELECT COUNT(*) FROM edges WHERE target_id = ? AND relation = 'intervento'",
        [actual_id],
    ).fetchone()[0]
    interventi = con.execute(
        """
        SELECT e.source_id, LEFT(n.title, 120), n.anno
        FROM edges e JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'intervento'
        ORDER BY n.anno DESC LIMIT 10
        """,
        [actual_id],
    ).fetchall()
    diventa = con.execute(
        """
        SELECT e.target_id, LEFT(n.title, 120), n.anno
        FROM edges e JOIN nodes n ON e.target_id = n.id
        WHERE e.source_id = ? AND e.relation = 'diventa_legge'
        LIMIT 5
        """,
        [actual_id],
    ).fetchall()
    testo = con.execute(
        """
        SELECT e.target_id, LEFT(n.title, 120)
        FROM edges e JOIN nodes n ON e.target_id = n.id
        WHERE e.source_id = ? AND e.relation = 'testo_atto'
        LIMIT 5
        """,
        [actual_id],
    ).fetchall()
    return {
        "view": "parliament",
        "node": _node_payload(node_row),
        "n_emendamenti": n_emendamenti,
        "note": "n_emendamenti/n_interventi = COUNT(*); liste = campioni LIMIT.",
        "emendamenti": [
            {"id": r[0], "title": r[1], "anno": r[2], "weight": r[3]} for r in emendamenti
        ],
        "emendamenti_per_legislatura": top_emendatori,
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
        "Numeri (es. '231', '231/2001', 'n. 231') → priorità a normativa. "
        "Accenti flessibili (responsabilità ~ responsabilita). "
        "Filtri: tipo, anno_min/anno_max, source. Input per legal_node / legal_text."
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
    q = (query or "").strip()
    folded = _fold(q)

    base_cond = "(anno IS NULL OR anno <= ?)"
    base_params: list[Any] = [datetime.now(UTC).year + 1]
    filters = ""
    filter_params: list[Any] = []
    if tipo:
        filters += " AND UPPER(tipo) = ?"
        filter_params.append(tipo.upper())
    if anno_min > 0:
        filters += " AND anno >= ?"
        filter_params.append(anno_min)
    if anno_max > 0:
        filters += " AND anno <= ?"
        filter_params.append(anno_max)
    if source:
        filters += " AND source = ?"
        filter_params.append(source)

    def _select(where: str, params: list[Any], order: str) -> list[tuple]:
        return con.execute(
            f"""
            SELECT id, tipo, title, CAST(data AS VARCHAR), anno, source
            FROM nodes
            WHERE {where}
            ORDER BY {order}
            LIMIT {limit}
            """,
            params,
        ).fetchall()

    rows: list[tuple] = []
    seen: set[str] = set()

    def _absorb(batch: list[tuple]) -> None:
        for r in batch:
            if r[0] not in seen:
                rows.append(r)
                seen.add(r[0])

    # 1) numero atto (231, n. 231, 231/2001) → priorità normativa
    num_m = re.search(r"(?:n\.?\s*)?(\d{1,4})(?:\s*/\s*(\d{2,4}))?", folded)
    if num_m:
        num = num_m.group(1)
        year = num_m.group(2)
        if year and len(year) == 2:
            year = "20" + year
        id_pattern = f"%{year}%;{num}" if year else f"%;{num}"
        title_num = f"%{year}%n. {num}%" if year else f"%n. {num}%"
        metrics_path = str(METRICS_FILE) if METRICS_FILE and METRICS_FILE.exists() else None
        if metrics_path:
            order = """
              CASE WHEN id LIKE ? THEN 0 ELSE 1 END,
              CASE WHEN tipo IN ('DECRETO LEGISLATIVO','LEGGE','DECRETO-LEGGE','DECRETO') THEN 0 ELSE 1 END,
              COALESCE((SELECT m.referenced_by FROM read_parquet(?) m WHERE m.id = nodes.id), 0) DESC,
              anno DESC NULLS LAST, title
            """
            extra_params = [id_pattern, metrics_path]
        else:
            order = """
              CASE WHEN id LIKE ? THEN 0 ELSE 1 END,
              CASE WHEN tipo IN ('DECRETO LEGISLATIVO','LEGGE','DECRETO-LEGGE','DECRETO') THEN 0 ELSE 1 END,
              anno DESC NULLS LAST, title
            """
            extra_params = [id_pattern]
        batch = con.execute(
            f"""
            SELECT id, tipo, title, CAST(data AS VARCHAR), anno, source
            FROM nodes
            WHERE {base_cond}{filters} AND source = 'normativa'
              AND (id LIKE ? OR LOWER(title) LIKE ? OR LOWER(title) LIKE ?)
            ORDER BY {order}
            LIMIT {limit}
            """,
            [
                *base_params,
                *filter_params,
                id_pattern,
                title_num,
                f"%n.{num}%",
                *extra_params,
            ],
        ).fetchall()
        _absorb(batch)

    # 2) AND multi-parola su title/id (fold accenti sul termine)
    if len(rows) < limit and folded:
        if (q.startswith('"') and q.endswith('"')) or (q.startswith("'") and q.endswith("'")):
            terms = [_fold(q[1:-1])]
        else:
            terms = [t for t in re.split(r"\s+", folded) if t][:8]
        cond = base_cond + filters
        params2: list[Any] = [*base_params, *filter_params]
        for term in terms:
            cond += " AND (LOWER(title) LIKE ? OR LOWER(id) LIKE ?)"
            params2.extend([f"%{term}%", f"%{term}%"])
        # prima i nodi normativa (atti), poi il resto del grafo
        _absorb(
            _select(
                cond + " AND source = 'normativa'",
                params2,
                "anno DESC NULLS LAST, title",
            )
        )
        if len(rows) < limit:
            _absorb(_select(cond, params2, "CASE WHEN source = 'normativa' THEN 0 ELSE 1 END, anno DESC NULLS LAST, title"))

    # 3) fallback OR
    if len(rows) < min(3, limit) and folded:
        terms = [t for t in re.split(r"\s+", folded) if t][:6]
        if terms:
            parts = " OR ".join(["LOWER(title) LIKE ? OR LOWER(id) LIKE ?"] * len(terms))
            params3: list[Any] = []
            for t in terms:
                params3.extend([f"%{t}%", f"%{t}%"])
            _absorb(
                _select(
                    f"{base_cond}{filters} AND ({parts})",
                    [*base_params, *filter_params, *params3],
                    "anno DESC NULLS LAST, title",
                )
            )

    return [
        {
            "id": r[0],
            "tipo": r[1],
            "title": (r[2] or "")[:_TITLE_SEARCH],
            "data": r[3],
            "anno": r[4],
            "source": r[5],
            "next": f"legal_node(node_id='{r[0]}')",
        }
        for r in rows[:limit]
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

    try:
        if view == "overview":
            return _view_overview(con, node_row)
        if view == "chain":
            return _view_chain(con, node_row, depth)
        if view == "jurisprudence":
            return _view_jurisprudence(con, node_row)
        return _view_parliament(con, node_row)
    except Exception as e:  # noqa: BLE001 — non far crashare l'MCP su nodi pesanti
        return {
            "error": f"view={view} fallita per {node_row[0]!r}: {type(e).__name__}: {e}",
            "node_id": node_row[0] if node_row else node_id,
        }


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
        "title": (node.get("title") or "")[:300],
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
        "SQL sul grafo. Ammessi: SELECT, WITH, PRAGMA, DESCRIBE, SHOW, EXPLAIN. "
        "Tabelle: nodes, edges, [temporal]. Niente scritture."
    ),
    structured_output=True,
)
def legal_query(sql: str, limit: int = 50) -> list[dict[str, Any]]:
    return guard_timed(_impl_query, "legal_query", sql, limit=limit)


def _impl_query(sql: str, limit: int = 50) -> list[dict[str, Any]]:
    con = _get_con()
    limit = min(max(limit, 1), _MAX_ROWS)
    sql_clean = (sql or "").strip()
    first = sql_clean.upper().split(None, 1)[0] if sql_clean else ""
    allowed = {"SELECT", "WITH", "PRAGMA", "DESCRIBE", "SHOW", "EXPLAIN"}
    if first not in allowed:
        return [{"error": f"Comando non consentito: {first!r}. Usa SELECT/WITH/PRAGMA/DESCRIBE/SHOW/EXPLAIN."}]
    if first in {"SELECT", "WITH"} and "LIMIT" not in sql_clean.upper():
        sql_clean = f"{sql_clean.rstrip()} LIMIT {limit}"
    try:
        result = con.execute(sql_clean)
        if result.description is None:
            # PRAGMA/SHOW senza description: restituisci almeno ok + columns note
            try:
                raw = result.fetchall()
            except duckdb.Error:
                raw = []
            if raw and isinstance(raw[0], (tuple, list)):
                # heuristic: PRAGMA table_info -> campi noti
                cols = ["cid", "name", "type", "notnull", "dflt_value", "pk"]
                return [dict(zip(cols, list(r))) for r in raw[:limit]]
            return [{"ok": True, "note": "PRAGMA/SHOW eseguito senza tabella risultati", "rows": len(raw)}]
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()[:limit]
    except duckdb.Error as e:
        return [{"error": str(e)[:200]}]
    return [dict(zip(columns, row)) for row in rows]


# ─── Tool 5: legal_insights ──────────────────────────────────────


@mcp.tool(
    name="legal_insights",
    description=(
        "Report di sistema sul grafo. report: summary | critical | obsolete | complex | dormant. "
        "obsolete = candidati per età/anagrafica storica, NON lista abrogazioni formali. "
        "Richiede: python scripts/graph_intelligence.py"
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
            "note": "Segmento per age_years (candidati storici), non per abrogazione ufficiale.",
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
