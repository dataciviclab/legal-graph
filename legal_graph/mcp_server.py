"""Server MCP legal-graph — Legal Knowledge Graph per agenti AI.

Grafo unificato di 430.000+ nodi normativi e 630.000+ archi (riferimenti,
citazioni costituzionali, emendamenti, interventi, bridge parlamento).

Tools:
  - legal_search: cerca atti per testo/tipo/anno
  - legal_node_details: dettagli di un nodo con tutti i suoi archi
  - legal_graph_query: query SQL arbitraria sul grafo
  - legal_stats: statistiche generali del grafo
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
from lab_connectors.mcp import create_mcp_server, guard_timed

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
NODES_FILE = DATA_DIR / "legal_nodes.parquet"
EDGES_FILE = DATA_DIR / "legal_edges.parquet"
TEMPORAL_FILE = DATA_DIR / "legal_edges_temporal.parquet"

_MAX_ROWS = 100

_cached_con: duckdb.DuckDBPyConnection | None = None


def _get_con() -> duckdb.DuckDBPyConnection:
    """Crea o ritorna la connessione DuckDB cachata con view sul grafo parquet.

    Usa VIEW (non TABLE) per evitare la materializzazione in RAM.
    DuckDB legge i parquet on-the-fly con predicate pushdown.
    """
    global _cached_con
    if _cached_con is not None:
        return _cached_con
    con = duckdb.connect(":memory:")
    con.execute("SET memory_limit='256MB'")
    con.execute("SET threads=1")
    con.execute(f"CREATE OR REPLACE VIEW nodes AS SELECT * FROM read_parquet('{NODES_FILE}')")
    con.execute(f"CREATE OR REPLACE VIEW edges AS SELECT * FROM read_parquet('{EDGES_FILE}')")
    if TEMPORAL_FILE.exists():
        con.execute(f"CREATE OR REPLACE VIEW temporal AS SELECT * FROM read_parquet('{TEMPORAL_FILE}')")
    _cached_con = con
    return con


# ─── Tool: legal_search ──────────────────────────────────────────


mcp = create_mcp_server(
    name="legal-graph",
    instructions=(
        "Legal Knowledge Graph — 430.000+ nodi normativi italiani, 630.000+ relazioni. "
        "Cerca atti, esplora il grafo delle relazioni, consulta la Costituzione e le "
        "sentenze della Corte Costituzionale. Usa legal_graph_query per SQL arbitrario."
    ),
)


@mcp.tool(
    name="legal_search",
    description=(
        "Cerca atti normativi nel Legal Knowledge Graph. "
        "Filtri opzionali: tipo (LEGGE, DECRETO-LEGGE, etc.), "
        "anno_min/anno_max, source (normativa/senato/gu/costituzione). "
        "Restituisce lista di nodi con id, tipo, titolo, data."
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
    return guard_timed(_impl_search, "legal_search",
                       query, tipo=tipo, anno_min=anno_min,
                       anno_max=anno_max, source=source, limit=limit)


def _impl_search(
    query: str,
    tipo: str = "",
    anno_min: int = 0,
    anno_max: int = 0,
    source: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    if not NODES_FILE.exists():
        raise FileNotFoundError(f"Legal graph non trovato: {NODES_FILE}")

    con = _get_con()
    limit = min(limit, _MAX_ROWS)

    conditions = []
    params: list[Any] = []
    if query:
        conditions.append("(LOWER(title) LIKE ? OR LOWER(id) LIKE ?)")
        params.extend([f"%{query.lower()}%", f"%{query.lower()}%"])
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

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    result = con.execute(f"""
        SELECT id, tipo, title, CAST(data AS VARCHAR) as data, anno, source
        FROM nodes
        {where}
        ORDER BY anno DESC NULLS LAST, title
        LIMIT {limit}
    """, params).fetchall()

    return [
        {
            "id": r[0],
            "tipo": r[1],
            "title": (r[2] or "")[:100],
            "data": r[3],
            "anno": r[4],
            "source": r[5],
        }
        for r in result
    ]


# ─── Tool: legal_node_details ────────────────────────────────────


@mcp.tool(
    name="legal_node_details",
    description=(
        "Dettagli di un nodo del grafo: metadati + tutti gli archi (in uscita e in entrata). "
        "Usa l'URN (es. 'urn:nir:stato:legge:2023-12-30;223') o un ID parziale."
    ),
    structured_output=True,
)
def legal_node_details(node_id: str) -> dict[str, Any]:
    return guard_timed(_impl_node_details, "legal_node_details", node_id)


def _impl_node_details(node_id: str) -> dict[str, Any]:
    con = _get_con()

    # Find node — parameterized to prevent SQL injection
    result = con.execute("""
        SELECT id, tipo, title, CAST(data AS VARCHAR) as data, anno, source,
               length_chars, length_words, celex
        FROM nodes
        WHERE id = ? OR id LIKE ?
        LIMIT 1
    """, [node_id, f"%{node_id}%"]).fetchone()

    if not result:
        return {"error": f"Nodo non trovato: {node_id}"}

    actual_id = result[0]

    # Outgoing edges
    out_edges = con.execute("""
        SELECT relation, target_id, weight, source_year, target_year, evidence
        FROM edges
        WHERE source_id = ?
        ORDER BY weight DESC
        LIMIT 20
    """, [actual_id]).fetchall()

    # Incoming edges
    in_edges = con.execute("""
        SELECT relation, source_id, weight, source_year, target_year, evidence
        FROM edges
        WHERE target_id = ?
        ORDER BY weight DESC
        LIMIT 20
    """, [actual_id]).fetchall()

    # Temporal edges
    temporal = []
    if "temporal" in [t[0] for t in con.execute("SHOW TABLES").fetchall()]:
        temporal = con.execute("""
            SELECT relation, target_id, weight, evidence
            FROM temporal
            WHERE source_id = ?
            LIMIT 20
        """, [actual_id]).fetchall()

    return {
        "node": {
            "id": actual_id,
            "tipo": result[1],
            "title": (result[2] or "")[:200],
            "data": result[3],
            "anno": result[4],
            "source": result[5],
            "length_chars": result[6],
            "length_words": result[7],
            "celex": result[8],
        },
        "outgoing_edges": [
            {"relation": e[0], "target": e[1][:80], "weight": e[2],
             "source_year": e[3], "target_year": e[4]}
            for e in out_edges
        ],
        "incoming_edges": [
            {"relation": e[0], "source": e[1][:80], "weight": e[2],
             "source_year": e[3], "target_year": e[4]}
            for e in in_edges
        ],
        "temporal_edges": [
            {"relation": t[0], "target": t[1][:80], "weight": t[2],
             "date": t[3]}
            for t in temporal
        ],
    }


# ─── Tool: legal_graph_query ─────────────────────────────────────


@mcp.tool(
    name="legal_graph_query",
    description=(
        "Esegui una query SQL sul Legal Knowledge Graph. "
        "Tabelle disponibili: nodes (id, tipo, title, data, anno, source), "
        "edges (source_id, relation, target_id, weight, source_year, target_year), "
        "temporal (source_id, relation, target_id, weight, source_year, target_year, evidence). "
        "Usa per aggregazioni, join, e analisi complesse."
    ),
    structured_output=True,
)
def legal_graph_query(sql: str, limit: int = 50) -> list[dict[str, Any]]:
    return guard_timed(_impl_query, "legal_graph_query", sql, limit=limit)


def _impl_query(sql: str, limit: int = 50) -> list[dict[str, Any]]:
    con = _get_con()
    limit = min(limit, _MAX_ROWS)

    # Safety: only allow SELECT
    sql_upper = sql.strip().upper()
    if not sql_upper.startswith("SELECT"):
            return [{"error": "Solo SELECT consentito"}]

    # Add LIMIT if not present
    if "LIMIT" not in sql_upper:
        sql = f"{sql.rstrip()} LIMIT {limit}"

    try:
        result = con.execute(sql)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()
    except duckdb.Error as e:
            return [{"error": str(e)[:200]}]


    return [dict(zip(columns, row)) for row in rows[:limit]]


# ─── Tool: legal_stats ──────────────────────────────────────────


@mcp.tool(
    name="legal_stats",
    description="Statistiche generali del Legal Knowledge Graph: nodi, archi, distribuzioni.",
    structured_output=True,
)
def legal_stats() -> dict[str, Any]:
    return guard_timed(_impl_stats, "legal_stats")


def _impl_stats() -> dict[str, Any]:
    con = _get_con()

    n_nodes = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    n_edges = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    n_temporal = con.execute("SELECT COUNT(*) FROM temporal").fetchone()[0] if TEMPORAL_FILE.exists() else 0

    by_source = dict(con.execute("""
        SELECT source, COUNT(*) FROM nodes GROUP BY source ORDER BY COUNT(*) DESC
    """).fetchall())

    by_relation = dict(con.execute("""
        SELECT relation, COUNT(*) FROM edges GROUP BY relation ORDER BY COUNT(*) DESC
    """).fetchall())

    by_tipo = dict(con.execute("""
        SELECT tipo, COUNT(*) FROM nodes GROUP BY tipo ORDER BY COUNT(*) DESC LIMIT 10
    """).fetchall())

    return {
        "nodi_totali": n_nodes,
        "archi_statici": n_edges,
        "archi_temporali": n_temporal,
        "archi_totali": n_edges + n_temporal,
        "per_sorgente": by_source,
        "per_relazione": by_relation,
        "top_tipi": by_tipo,
    }


# ─── Tool: legal_intelligence ──────────────────────────────────


METRICS_FILE = DATA_DIR / "graph_metrics.parquet"


@mcp.tool(
    name="legal_intelligence",
    description=(
        "Analisi di intelligenza sul grafo legale. "
        "Per un nodo: metriche di impatto, complessità, età, rischio. "
        "Per l'intero sistema: nodi critici, obsoleti, complessi, dormienti."
    ),
    structured_output=True,
)
def legal_intelligence(node_id: str = "", report: str = "") -> dict[str, Any]:
    return guard_timed(_impl_intelligence, "legal_intelligence", node_id, report)


def _impl_intelligence(node_id: str, report: str) -> dict[str, Any]:
    if not METRICS_FILE.exists():
        return {"error": "Graph metrics not computed. Run: python -m legal_graph.graph_intelligence"}

    con = _get_con()
    con.execute(f"CREATE OR REPLACE VIEW metrics AS SELECT * FROM read_parquet('{METRICS_FILE}')")

    # Node-specific intelligence
    if node_id:
        result = con.execute("""
            SELECT * FROM metrics WHERE id = ? LIMIT 1
        """, [node_id]).fetchone()

        if not result:
                    return {"error": f"Nodo non trovato: {node_id}"}

        columns = [desc[0] for desc in con.execute("SELECT * FROM metrics WHERE id = ? LIMIT 1", [node_id]).description]
        node_data = dict(zip(columns, result))

        # Get connected nodes
        out_edges = con.execute("""
            SELECT relation, target_id, weight FROM edges WHERE source_id = ?
            ORDER BY weight DESC LIMIT 10
        """, [node_id]).fetchall()

        in_edges = con.execute("""
            SELECT relation, source_id, weight FROM edges WHERE target_id = ?
            ORDER BY weight DESC LIMIT 10
        """, [node_id]).fetchall()

        return {
            "node": node_data,
            "outgoing_sample": [{"rel": e[0], "target": e[1][:60], "weight": e[2]} for e in out_edges],
            "incoming_sample": [{"rel": e[0], "source": e[1][:60], "weight": e[2]} for e in in_edges],
        }

    # System report
    report_type = report or "summary"

    if report_type == "critical":
        rows = con.execute("""
            SELECT id, title, referenced_by, impact_score, age_years
            FROM metrics WHERE impact_level = 'critical'
            ORDER BY referenced_by DESC LIMIT 20
        """).fetchall()
        return {"critical_nodes": [{"id": r[0], "title": (r[1] or r[0])[:60], "refs": r[2], "score": r[3], "age": r[4]} for r in rows]}

    elif report_type == "obsolete":
        rows = con.execute("""
            SELECT id, title, age_years, referenced_by
            FROM metrics WHERE age_risk = 'obsolete_candidate'
            ORDER BY age_years DESC LIMIT 20
        """).fetchall()
        return {"obsolete_candidates": [{"id": r[0], "title": (r[1] or r[0])[:60], "age": r[2], "refs": r[3]} for r in rows]}

    elif report_type == "complex":
        rows = con.execute("""
            SELECT id, title, "references", referenced_by, age_years
            FROM metrics WHERE complexity_level IN ('very_complex', 'complex')
            ORDER BY "references" DESC LIMIT 20
        """).fetchall()
        return {"complex_laws": [{"id": r[0], "title": (r[1] or r[0])[:60], "deps": r[2], "refs": r[3], "age": r[4]} for r in rows]}

    elif report_type == "dormant":
        rows = con.execute("""
            SELECT id, title, last_referenced_year, referenced_by
            FROM metrics WHERE activity_level = 'dormant' AND referenced_by > 5
            ORDER BY referenced_by DESC LIMIT 20
        """).fetchall()
        return {"dormant_references": [{"id": r[0], "title": (r[1] or r[0])[:60], "last_year": r[2], "refs": r[3]} for r in rows]}

    else:  # summary
        stats = con.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN impact_level = 'critical' THEN 1 ELSE 0 END) as critical,
                SUM(CASE WHEN impact_level = 'important' THEN 1 ELSE 0 END) as important,
                SUM(CASE WHEN complexity_level = 'very_complex' THEN 1 ELSE 0 END) as very_complex,
                SUM(CASE WHEN age_risk = 'obsolete_candidate' THEN 1 ELSE 0 END) as obsolete,
                SUM(CASE WHEN activity_level = 'dormant' THEN 1 ELSE 0 END) as dormant,
                ROUND(AVG(age_years), 1) as avg_age
            FROM metrics
        """).fetchone()

        # Top 5 critical
        critical = con.execute("""
            SELECT id, title, referenced_by FROM metrics
            WHERE impact_level = 'critical' ORDER BY referenced_by DESC LIMIT 5
        """).fetchall()

        # Top 5 obsolete
        obsolete = con.execute("""
            SELECT id, title, age_years, referenced_by FROM metrics
            WHERE age_risk = 'obsolete_candidate' ORDER BY age_years DESC LIMIT 5
        """).fetchall()

    
        return {
            "summary": {
                "total_nodes": stats[0],
                "critical": stats[1],
                "important": stats[2],
                "very_complex": stats[3],
                "obsolete_candidates": stats[4],
                "dormant": stats[5],
                "avg_age_years": stats[6],
            },
            "top_critical": [{"id": r[0], "title": (r[1] or r[0])[:60], "refs": r[2]} for r in critical],
            "top_obsolete": [{"id": r[0], "title": (r[1] or r[0])[:60], "age": r[2], "refs": r[3]} for r in obsolete],
        }


# ─── Tool: legal_chain ────────────────────────────────────────


@mcp.tool(
    name="legal_chain",
    description=(
        "Catena del diritto per un atto: DDL → Legge → D.Lgs → EU. "
        "Mostra il percorso legislativo completo: deleghe, recepimenti, "
        "collegamenti Costituzionali. Input: URN o ID di un atto."
    ),
    structured_output=True,
)
def legal_chain(node_id: str, depth: int = 3) -> dict[str, Any]:
    return guard_timed(_impl_chain, "legal_chain", node_id, depth=depth)


def _impl_chain(node_id: str, depth: int = 3) -> dict[str, Any]:
    con = _get_con()
    depth = min(depth, 5)

    # Find the node
    node = con.execute("""
        SELECT id, tipo, title, CAST(data AS VARCHAR) as data, anno, source
        FROM nodes WHERE id = ? OR id LIKE ?
        LIMIT 1
    """, [node_id, f"%{node_id}%"]).fetchone()

    if not node:
        return {"error": f"Nodo non trovato: {node_id}"}

    actual_id = node[0]

    # Legislation chain relations
    chain_rels = [
        'diventa_legge', 'attua_delega', 'recepisce_direttiva',
        'attua_regolamento', 'collega_ue', 'cita_costituzione',
    ]

    # Walk the chain forward (what does this act lead to?)
    forward = []
    visited = {actual_id}
    queue = [(actual_id, 0)]
    while queue:
        current, current_depth = queue.pop(0)
        if current_depth >= depth:
            continue
        edges = con.execute("""
            SELECT relation, target_id, weight, evidence
            FROM edges WHERE source_id = ?
            AND relation IN (?, ?, ?, ?, ?, ?)
            ORDER BY weight DESC LIMIT 10
        """, [current] + chain_rels).fetchall()
        for rel, target, weight, evidence in edges:
            if target not in visited:
                visited.add(target)
                target_node = con.execute(
                    "SELECT tipo, title, anno FROM nodes WHERE id = ? LIMIT 1",
                    [target]
                ).fetchone()
                forward.append({
                    "relation": rel,
                    "target_id": target,
                    "target_tipo": target_node[0] if target_node else "?",
                    "target_title": (target_node[1] or "")[:100] if target_node else "",
                    "target_anno": target_node[2] if target_node else None,
                    "weight": weight,
                })
                queue.append((target, current_depth + 1))

    # Walk the chain backward (what produced this act?)
    backward = []
    visited_back = {actual_id}
    queue_back = [(actual_id, 0)]
    while queue_back:
        current, current_depth = queue_back.pop(0)
        if current_depth >= depth:
            continue
        edges = con.execute("""
            SELECT relation, source_id, weight, evidence
            FROM edges WHERE target_id = ?
            AND relation IN (?, ?, ?, ?, ?, ?)
            ORDER BY weight DESC LIMIT 10
        """, [current] + chain_rels).fetchall()
        for rel, source, weight, evidence in edges:
            if source not in visited_back:
                visited_back.add(source)
                source_node = con.execute(
                    "SELECT tipo, title, anno FROM nodes WHERE id = ? LIMIT 12",
                    [source]
                ).fetchone()
                backward.append({
                    "relation": rel,
                    "source_id": source,
                    "source_tipo": source_node[0] if source_node else "?",
                    "source_title": (source_node[1] or "")[:100] if source_node else "",
                    "source_anno": source_node[2] if source_node else None,
                    "weight": weight,
                })
                queue_back.append((source, current_depth + 1))

    # Temporal info
    temporal = con.execute("""
        SELECT relation, evidence FROM temporal
        WHERE source_id = ? LIMIT 5
    """, [actual_id]).fetchall()

    return {
        "node": {
            "id": actual_id,
            "tipo": node[1],
            "title": (node[2] or "")[:150],
            "data": node[3],
            "anno": node[4],
            "source": node[5],
        },
        "chain_forward": forward,
        "chain_backward": backward,
        "temporal": [{"relation": t[0], "evidence": t[1]} for t in temporal],
    }


# ─── Tool: legal_jurisprudence ──────────────────────────────────


@mcp.tool(
    name="legal_jurisprudence",
    description=(
        "Giurisprudenza della Corte Costituzionale: sentenze, impugnazioni, "
        "parametri invocati. Input: id di un atto per vedere le sentenze che lo "
        "impugnano, o id di un articolo Costituzionale per vedere chi lo invoca."
    ),
    structured_output=True,
)
def legal_jurisprudence(node_id: str) -> dict[str, Any]:
    return guard_timed(_impl_jurisprudence, "legal_jurisprudence", node_id)


def _impl_jurisprudence(node_id: str) -> dict[str, Any]:
    con = _get_con()

    # Find node
    node = con.execute("""
        SELECT id, tipo, title, CAST(data AS VARCHAR) as data, anno, source
        FROM nodes WHERE id = ? OR id LIKE ?
        LIMIT 1
    """, [node_id, f"%{node_id}%"]).fetchone()

    if not node:
        return {"error": f"Nodo non trovato: {node_id}"}

    actual_id = node[0]

    # What does this node impugn? (if it's a sentenza)
    impugna = con.execute("""
        SELECT e.target_id, n.tipo, LEFT(n.title, 100) as title, n.anno, e.weight
        FROM edges e JOIN nodes n ON e.target_id = n.id
        WHERE e.source_id = ? AND e.relation = 'impugna'
        ORDER BY e.weight DESC LIMIT 20
    """, [actual_id]).fetchall()

    # What impugns this node? (if it's a norma)
    impugnata_da = con.execute("""
        SELECT e.source_id, n.tipo, LEFT(n.title, 100) as title, n.anno, e.weight
        FROM edges e JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'impugna'
        ORDER BY e.weight DESC LIMIT 20
    """, [actual_id]).fetchall()

    # What parameters does this node invoke? (if it's a sentenza)
    parametri = con.execute("""
        SELECT e.target_id, LEFT(n.title, 80) as title, e.weight
        FROM edges e JOIN nodes n ON e.target_id = n.id
        WHERE e.source_id = ? AND e.relation = 'invoca_parametro'
        ORDER BY e.weight DESC LIMIT 20
    """, [actual_id]).fetchall()

    # What cites this article? (if it's a costituzione article)
    citato_da = con.execute("""
        SELECT e.source_id, n.tipo, LEFT(n.title, 100) as title, n.anno, e.weight
        FROM edges e JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'cita_costituzione'
        ORDER BY e.weight DESC LIMIT 20
    """, [actual_id]).fetchall()

    return {
        "node": {
            "id": actual_id,
            "tipo": node[1],
            "title": (node[2] or "")[:150],
            "data": node[3],
            "anno": node[4],
            "source": node[5],
        },
        "impugna": [
            {"id": r[0], "tipo": r[1], "title": r[2], "anno": r[3], "weight": r[4]}
            for r in impugna
        ],
        "impugnata_da": [
            {"id": r[0], "tipo": r[1], "title": r[2], "anno": r[3], "weight": r[4]}
            for r in impugnata_da
        ],
        "parametri_invocati": [
            {"articolo": r[0], "title": r[1], "weight": r[2]}
            for r in parametri
        ],
        "citata_da": [
            {"id": r[0], "tipo": r[1], "title": r[2], "anno": r[3], "weight": r[4]}
            for r in citato_da
        ],
    }


# ─── Tool: legal_parliament ─────────────────────────────────────


@mcp.tool(
    name="legal_parliament",
    description=(
        "Attività parlamentare su un DDL: emendamenti, interventi, "
        "stato dell'iter. Input: id di un DDL (senato:{id} o camera:{id})."
    ),
    structured_output=True,
)
def legal_parliament(node_id: str) -> dict[str, Any]:
    return guard_timed(_impl_parliament, "legal_parliament", node_id)


def _impl_parliament(node_id: str) -> dict[str, Any]:
    con = _get_con()

    # Find node
    node = con.execute("""
        SELECT id, tipo, title, CAST(data AS VARCHAR) as data, anno, source
        FROM nodes WHERE id = ? OR id LIKE ?
        LIMIT 1
    """, [node_id, f"%{node_id}%"]).fetchone()

    if not node:
        return {"error": f"Nodo non trovato: {node_id}"}

    actual_id = node[0]

    # Emendamenti
    emendamenti = con.execute("""
        SELECT e.source_id, LEFT(n.title, 100) as title, n.anno, e.weight
        FROM edges e JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'emendamento'
        ORDER BY e.weight DESC LIMIT 20
    """, [actual_id]).fetchall()

    # Top emendatori
    top_emendatori = con.execute("""
        SELECT n.title, COUNT(*) as cnt
        FROM edges e JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'emendamento'
        GROUP BY n.title ORDER BY cnt DESC LIMIT 10
    """, [actual_id]).fetchall()

    # Interventi
    interventi = con.execute("""
        SELECT e.source_id, LEFT(n.title, 80) as title, n.anno
        FROM edges e JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'intervento'
        ORDER BY n.anno DESC LIMIT 10
    """, [actual_id]).fetchall()

    # Did it become law?
    diventa = con.execute("""
        SELECT e.target_id, LEFT(n.title, 100) as title, n.anno
        FROM edges e JOIN nodes n ON e.target_id = n.id
        WHERE e.source_id = ? AND e.relation = 'diventa_legge'
        LIMIT 5
    """, [actual_id]).fetchall()

    # Testo
    testo = con.execute("""
        SELECT e.target_id, LEFT(n.title, 100) as title
        FROM edges e JOIN nodes n ON e.target_id = n.id
        WHERE e.source_id = ? AND e.relation = 'testo_atto'
        LIMIT 5
    """, [actual_id]).fetchall()

    return {
        "node": {
            "id": actual_id,
            "tipo": node[1],
            "title": (node[2] or "")[:150],
            "data": node[3],
            "anno": node[4],
            "source": node[5],
        },
        "n_emendamenti": len(emendamenti),
        "emendamenti": [
            {"id": r[0], "title": r[1], "anno": r[2], "weight": r[3]}
            for r in emendamenti
        ],
        "top_emendatori": [
            {"autore": r[0], "n_emendamenti": r[1]}
            for r in top_emendatori
        ],
        "n_interventi": con.execute("""
            SELECT COUNT(*) FROM edges
            WHERE target_id = ? AND relation = 'intervento'
        """, [actual_id]).fetchone()[0],
        "diventa_legge": [
            {"id": r[0], "title": r[1], "anno": r[2]}
            for r in diventa
        ],
        "testo": [
            {"id": r[0], "title": r[1]}
            for r in testo
        ],
    }


# ─── Main ────────────────────────────────────────────────────────


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
