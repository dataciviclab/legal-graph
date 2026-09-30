#!/usr/bin/env python3
"""Legal Graph — Intelligence layer (CLI).

Calcola metriche di impatto/complessità/età dal grafo compose e scrive
`data/graph_metrics.parquet` (letto da MCP legal_insights).

Usage:
  python scripts/graph_intelligence.py
  python scripts/graph_intelligence.py --dry-run
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from legal_graph.paths import (
    resolve_edges_file,
    resolve_nodes_file,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
OUTPUT_FILE = DATA_DIR / "graph_metrics.parquet"

# Relations that matter for intelligence
ANALYSIS_RELATIONS = (
    'riferimento', 'cita_costituzione', 'impugna', 'invoca_parametro',
    'diventa_legge', 'recepisce_direttiva', 'attua_regolamento', 'evoca_parametro',
    'abroga',
)


def compute_metrics(con: duckdb.DuckDBPyConnection) -> None:
    """Compute all graph metrics."""
    current_year = datetime.now(UTC).year

    # 1. Incoming edges (impact on this node)
    print("Computing incoming metrics...")
    con.execute("""
        CREATE TABLE incoming AS
        SELECT
            target_id,
            COUNT(*) AS referenced_by,
            SUM(weight) AS impact_score,
            MAX(source_year) AS last_referenced_year,
            MIN(source_year) AS first_referenced_year,
            COUNT(DISTINCT source_id) AS distinct_sources,
            COUNT(DISTINCT relation) AS relation_types
        FROM edges
        WHERE relation IN ({rels})
        GROUP BY target_id
    """.format(rels=", ".join(f"'{r}'" for r in ANALYSIS_RELATIONS)))

    # 2. Outgoing edges (complexity of this node)
    print("Computing outgoing metrics...")
    con.execute("""
        CREATE TABLE outgoing AS
        SELECT
            source_id,
            COUNT(*) AS references,
            SUM(weight) AS complexity_score,
            COUNT(DISTINCT target_id) AS distinct_targets,
            MAX(target_year) AS newest_reference,
            MIN(target_year) AS oldest_reference
        FROM edges
        WHERE relation IN ({rels})
        GROUP BY source_id
    """.format(rels=", ".join(f"'{r}'" for r in ANALYSIS_RELATIONS)))

    # 3. Combine with nodes
    print("Building node metrics...")
    con.execute(f"""
        CREATE TABLE node_metrics AS
        SELECT
            n.id,
            n.tipo,
            n.title,
            n.data,
            n.anno,
            n.source,
            -- Incoming
            COALESCE(i.referenced_by, 0) AS referenced_by,
            COALESCE(i.impact_score, 0) AS impact_score,
            i.last_referenced_year,
            i.first_referenced_year,
            COALESCE(i.distinct_sources, 0) AS distinct_sources,
            COALESCE(i.relation_types, 0) AS relation_types,
            -- Outgoing
            COALESCE(o.references, 0) AS references,
            COALESCE(o.complexity_score, 0) AS complexity_score,
            COALESCE(o.distinct_targets, 0) AS distinct_targets,
            o.newest_reference,
            o.oldest_reference,
            -- Derived
            CASE WHEN n.anno IS NOT NULL THEN {current_year} - n.anno ELSE NULL END AS age_years,
            -- Impact level
            CASE
                WHEN COALESCE(i.referenced_by, 0) >= 100 THEN 'critical'
                WHEN COALESCE(i.referenced_by, 0) >= 50 THEN 'important'
                WHEN COALESCE(i.referenced_by, 0) >= 10 THEN 'moderate'
                ELSE 'minor'
            END AS impact_level,
            -- Complexity level
            CASE
                WHEN COALESCE(o.references, 0) >= 200 THEN 'very_complex'
                WHEN COALESCE(o.references, 0) >= 100 THEN 'complex'
                WHEN COALESCE(o.references, 0) >= 50 THEN 'moderate_complexity'
                ELSE NULL
            END AS complexity_level,
            -- Age risk
            CASE
                WHEN ({current_year} - n.anno) >= 50 AND COALESCE(i.referenced_by, 0) >= 10 THEN 'obsolete_candidate'
                WHEN ({current_year} - n.anno) >= 30 AND COALESCE(i.referenced_by, 0) >= 50 THEN 'aging'
                ELSE NULL
            END AS age_risk,
            -- Activity level
            CASE
                WHEN i.last_referenced_year IS NOT NULL AND i.last_referenced_year < 2010 THEN 'dormant'
                WHEN i.last_referenced_year IS NOT NULL AND i.last_referenced_year < 2015 THEN 'declining'
                ELSE NULL
            END AS activity_level
        FROM nodes n
        LEFT JOIN incoming i ON n.id = i.target_id
        LEFT JOIN outgoing o ON n.id = o.source_id
    """)
    n = con.execute("SELECT COUNT(*) FROM node_metrics").fetchone()[0]
    print(f"  {n} nodi metricati")


def generate_report(con: duckdb.DuckDBPyConnection) -> str:
    """Generate text report."""
    lines = []
    lines.append("Legal Graph Intelligence Report")
    lines.append("=" * 50)

    # Critical nodes
    rows = con.execute("""
        SELECT id, title, referenced_by, impact_score, age_years
        FROM node_metrics
        WHERE impact_level = 'critical'
        ORDER BY referenced_by DESC
        LIMIT 15
    """).fetchall()
    lines.append(f"\n🔴 CRITICAL (>= 100 references): {len(rows)} nodes")
    for r in rows:
        title = (r[1] or r[0])[:55]
        lines.append(f"  {r[2]:>5} refs | score {r[3]:>6.0f} | {r[4] or '?':>3}y | {title}")

    # Obsolete candidates
    rows = con.execute("""
        SELECT id, title, age_years, referenced_by
        FROM node_metrics
        WHERE age_risk = 'obsolete_candidate'
        ORDER BY age_years DESC
        LIMIT 15
    """).fetchall()
    lines.append(f"\n🟡 OBSOLETE CANDIDATES (>= 50 years): {len(rows)} nodes")
    for r in rows:
        title = (r[1] or r[0])[:55]
        lines.append(f"  {r[2]:>3}y old | {r[3]:>5} refs | {title}")

    # Complex laws
    rows = con.execute("""
        SELECT id, title, "references", referenced_by, age_years
        FROM node_metrics
        WHERE complexity_level IN ('very_complex', 'complex')
        ORDER BY "references" DESC
        LIMIT 15
    """).fetchall()
    lines.append(f"\n🟠 COMPLEX (>= 100 dependencies): {len(rows)} nodes")
    for r in rows:
        title = (r[1] or r[0])[:55]
        lines.append(f"  {r[2]:>3} deps | {r[3]:>5} refs | {r[4] or '?':>3}y | {title}")

    # Dormant
    rows = con.execute("""
        SELECT id, title, last_referenced_year, referenced_by
        FROM node_metrics
        WHERE activity_level = 'dormant' AND referenced_by > 5
        ORDER BY referenced_by DESC
        LIMIT 15
    """).fetchall()
    lines.append(f"\n⚪ DORMANT (last cited < 2010): {len(rows)} nodes")
    for r in rows:
        title = (r[1] or r[0])[:55]
        lines.append(f"  last {r[2]} | {r[3]:>5} refs | {title}")

    # Stats
    stats = con.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN impact_level = 'critical' THEN 1 ELSE 0 END) as critical,
            SUM(CASE WHEN impact_level = 'important' THEN 1 ELSE 0 END) as important,
            SUM(CASE WHEN complexity_level = 'very_complex' THEN 1 ELSE 0 END) as very_complex,
            SUM(CASE WHEN age_risk = 'obsolete_candidate' THEN 1 ELSE 0 END) as obsolete,
            SUM(CASE WHEN activity_level = 'dormant' THEN 1 ELSE 0 END) as dormant,
            AVG(age_years) as avg_age
        FROM node_metrics
    """).fetchone()
    lines.append("\n📊 SUMMARY")
    lines.append(f"  Total nodes: {stats[0]:,}")
    lines.append(f"  Critical: {stats[1]}")
    lines.append(f"  Important: {stats[2]}")
    lines.append(f"  Very complex: {stats[3]}")
    lines.append(f"  Obsolete candidates: {stats[4]}")
    lines.append(f"  Dormant: {stats[5]}")
    lines.append(f"  Average age: {stats[6]:.1f} years")

    return "\n".join(lines)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Graph Intelligence")
    parser.add_argument("--dry-run", action="store_true", help="Print report without saving")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    nodes_file = resolve_nodes_file()
    edges_file = resolve_edges_file()
    con = duckdb.connect(":memory:")

    print("Loading graph data...")
    print(f"  nodes:  {nodes_file}")
    print(f"  edges:  {edges_file}")
    con.execute(f"CREATE TABLE nodes AS SELECT * FROM read_parquet('{nodes_file}')")
    con.execute(f"CREATE TABLE edges AS SELECT * FROM read_parquet('{edges_file}')")
    n_nodes = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    n_edges = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    print(f"  {n_nodes:,} nodi, {n_edges:,} archi")

    compute_metrics(con)

    report = generate_report(con)
    print("\n" + report)

    if not args.dry_run:
        con.execute(f"COPY node_metrics TO '{OUTPUT_FILE}' (FORMAT PARQUET, COMPRESSION 'zstd')")
        print(f"\nSalvato: {OUTPUT_FILE}")

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
