#!/usr/bin/env python3
"""Legal Graph — Temporal enrichment from Normattiva API + existing data.

Strategy:
1. Query Normattiva API for validity dates (articoloDataInizioVigenza/FineVigenza)
2. Add temporal edges: atto modifica atto (based on riferimenti + dates)
3. Add validity windows to nodes
4. Produce data/legal_edges_temporal.parquet

Usage:
  python scripts/temporal_enrichment.py [--sample N]
"""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path
from urllib.request import Request, urlopen

import duckdb

WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent
OUTDIR = Path(__file__).resolve().parent.parent / "data"

# Source paths
NORMATIVA = WORKSPACE / "italia-corpus" / "data" / "derived" / "normativa.parquet"
RIFERIMENTI = WORKSPACE / "italia-corpus" / "data" / "derived" / "riferimenti.parquet"
GU_ACTS = WORKSPACE / "gu-monitor" / "data" / "gu_acts.parquet"
GU_LINKS = WORKSPACE / "gu-monitor" / "data" / "gu_links.json"
LEGAL_NODES = OUTDIR / "legal_nodes.parquet"
LEGAL_EDGES = OUTDIR / "legal_edges.parquet"

BASE_URL = "https://api.normattiva.it/t/normattiva.api/bff-opendata/v1/api/v1"


def query_normattiva(codice_redazionale: str, data_gu: str) -> dict | None:
    """Query Normattiva API for act validity dates."""
    payload = json.dumps({
        "dataGU": data_gu,
        "codiceRedazionale": codice_redazionale,
        "formatoRichiesta": "V"
    }).encode()

    req = Request(f"{BASE_URL}/atto/dettaglio-atto", data=payload, headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "LegalGraph/0.1"
    })

    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            atto = data.get("data", {}).get("atto")
            if atto:
                return {
                    "inizio_vigenza": atto.get("articoloDataInizioVigenza", ""),
                    "fine_vigenza": atto.get("articoloDataFineVigenza", ""),
                    "tipo": atto.get("tipoProvvedimentoDescrizione", ""),
                    "numero": atto.get("numeroProvvedimento"),
                    "anno": atto.get("annoProvvedimento"),
                }
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        pass
    return None


def build_temporal_edges_from_riferimenti(con: duckdb.DuckDBPyConnection) -> None:
    """Build temporal amendment edges from cross-references + dates.

    Logic: if act A (year X) references act B (year Y), and A is a
    modification-type act (D.Lgs, D.Lgs.luog., etc.), then A modifies B.
    """
    con.execute(f"""
        CREATE TABLE temporal_amendments AS
        SELECT
            COALESCE(lu.urn, 'file:' || r.fonte_filename) AS source_id,
            'modifica' AS relation,
            COALESCE(lu2.urn, 'file:' || r.bersaglio_filename) AS target_id,
            r.peso AS weight,
            r.fonte_anno AS source_year,
            r.bersaglio_anno AS target_year,
            'riferimento_modifica' AS evidence
        FROM read_parquet('{RIFERIMENTI}') r
        LEFT JOIN (
            SELECT filename, urn FROM read_parquet('{NORMATIVA}')
        ) lu ON regexp_extract(r.fonte_filename, '/([^/]+)$', 1) = lu.filename
        LEFT JOIN (
            SELECT filename, urn FROM read_parquet('{NORMATIVA}')
        ) lu2 ON r.bersaglio_filename = lu2.filename
        WHERE r.risolto = true
          AND r.fonte_tipo IN (
              'DECRETO LEGISLATIVO',
              'DECRETO LEGISLATIVO LUOGOTENENZIALE',
              'DECRETO-LEGGE',
              'LEGGE'
          )
          AND r.fonte_anno > r.bersaglio_anno
          AND r.fonte_anno > 0
          AND r.bersaglio_anno > 0
    """)
    n = con.execute("SELECT COUNT(*) FROM temporal_amendments").fetchone()[0]
    print(f"  modifica (da riferimenti): {n:>6} archi")


def build_validity_windows(con: duckdb.DuckDBPyConnection) -> None:
    """Build validity window edges from Normattiva publication dates.

    Each act has a publication date. Acts that reference earlier acts
    create temporal relationships: "this act was valid starting from date X."
    """
    # Get acts with dates
    con.execute(f"""
        CREATE TABLE acts_with_dates AS
        SELECT
            urn,
            tipo,
            data,
            anno_atto,
            CASE
                WHEN tipo IN ('DECRETO-LEGGE') THEN 'decreto_legge'
                WHEN tipo LIKE 'DECRETO LEGISLATIVO%' THEN 'decreto_legislativo'
                WHEN tipo = 'LEGGE' THEN 'legge'
                WHEN tipo = 'LEGGE COSTITUZIONALE' THEN 'legge_costituzionale'
                ELSE 'altro'
            END AS categoria
        FROM read_parquet('{NORMATIVA}')
        WHERE NULLIF(urn, '') IS NOT NULL
          AND NULLIF(data, '') IS NOT NULL
    """)
    n = con.execute("SELECT COUNT(*) FROM acts_with_dates").fetchone()[0]
    print(f"  atti con date:            {n:>6}")

    # Build "entra_in_vigore" edges (atto → se stesso, con data)
    con.execute("""
        CREATE TABLE temporal_validity AS
        SELECT
            urn AS source_id,
            'entra_in_vigore' AS relation,
            urn AS target_id,
            1 AS weight,
            anno_atto AS source_year,
            anno_atto AS target_year,
            data AS evidence
        FROM acts_with_dates
    """)
    n = con.execute("SELECT COUNT(*) FROM temporal_validity").fetchone()[0]
    print(f"  entrata in vigore:        {n:>6}")


def build_gu_temporal_edges(con: duckdb.DuckDBPyConnection) -> None:
    """Build temporal edges from GU publication dates."""
    if not GU_ACTS.exists():
        print("  gu_acts non trovato")
        return

    con.execute(f"""
        CREATE TABLE gu_temporal AS
        SELECT
            COALESCE(NULLIF(urn_normattiva, ''), 'gu:' || id) AS source_id,
            'pubblicato_in_gu' AS relation,
            COALESCE(NULLIF(urn_normattiva, ''), 'gu:' || id) AS target_id,
            1 AS weight,
            YEAR(CAST(data_pubblicazione AS DATE)) AS source_year,
            YEAR(CAST(data_pubblicazione AS DATE)) AS target_year,
            CAST(data_pubblicazione AS VARCHAR) AS evidence
        FROM read_parquet('{GU_ACTS}')
        WHERE NULLIF(urn_normattiva, '') IS NOT NULL
    """)
    n = con.execute("SELECT COUNT(*) FROM gu_temporal").fetchone()[0]
    print(f"  GU pubblicazioni:         {n:>6}")


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Temporal enrichment for legal graph")
    parser.add_argument("--sample", type=int, default=0, help="Sample N acts for API queries")
    parser.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(":memory:")

    print("Building temporal edges...")
    print()

    # 1. Temporal amendments from cross-references
    build_temporal_edges_from_riferimenti(con)

    # 2. Validity windows from publication dates
    build_validity_windows(con)

    # 3. GU temporal edges
    build_gu_temporal_edges(con)

    # Union all temporal edges
    con.execute("""
        CREATE TABLE temporal_edges AS
        SELECT * FROM temporal_amendments
        UNION ALL
        SELECT * FROM temporal_validity
        UNION ALL
        SELECT * FROM gu_temporal
    """)

    n = con.execute("SELECT COUNT(*) FROM temporal_edges").fetchone()[0]
    print(f"\n  TOTALE:                   {n:>6} archi temporali")

    # Save
    out_file = OUTDIR / "legal_edges_temporal.parquet"
    con.execute(f"COPY temporal_edges TO '{out_file}' (FORMAT PARQUET, COMPRESSION 'zstd')")
    print(f"\nSalvato: {out_file}")

    # Summary
    by_relation = con.execute("""
        SELECT relation, COUNT(*) as n
        FROM temporal_edges
        GROUP BY relation
        ORDER BY n DESC
    """).fetchall()
    print("\nPer relazione:")
    for rel, count in by_relation:
        print(f"  {rel:25s}: {count:>6}")

    # Query: acts active at a specific date
    print("\n── Esempio: atti vigenti al 2024-01-01 ──")
    result = con.execute("""
        SELECT COUNT(DISTINCT source_id)
        FROM temporal_edges
        WHERE relation = 'entra_in_vigore'
          AND evidence <= '2024-01-01'
    """).fetchone()
    print(f"  {result[0]:>6} atti con data di entrata in vigore <= 2024-01-01")

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
