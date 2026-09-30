#!/usr/bin/env python3
"""Legal Graph — EUR-Lex enrichment (CLI sperimentale, non nel compose).

Fetch ELI metadata da EUR-Lex per atti italiani con CELEX.
Output opzionale in `data/legal_nodes_eu.parquet` + `legal_edges_eu.parquet`.

Usage:
  python scripts/eu_enrichment.py [--limit N] [--cache-only]
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
from pathlib import Path
from urllib.request import Request, urlopen

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

CACHE_FILE = DATA_DIR / "eu_cache.json"
EU_NODES_FILE = DATA_DIR / "legal_nodes_eu.parquet"
EU_EDGES_FILE = DATA_DIR / "legal_edges_eu.parquet"
# CELEX extraction: prefer compose mart, fallback legacy data/
NORMATIVA_CANDIDATES = [
    REPO_ROOT / "out/data/mart/legal_graph/2026/mart_legal_nodes.parquet",
    DATA_DIR / "legal_nodes.parquet",
]

# CELEX type codes → human-readable
CELEX_TYPES = {
    "DIR": "DIRETTIVA",
    "REG": "REGOLAMENTO",
    "DEC": "DECISIONE",
    "REC": "RACCOMANDAZIONE",
    "DIR_DEC": "DIR-DEC",
    "DIR_CORR": "DIR-CORR",
}

# EuroVoc top concepts (mapped from URIs found in EUR-Lex)
EUROVOC_LABELS: dict[str, str] = {}


def _load_cache() -> dict:
    """Load cached EUR-Lex responses."""
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def _save_cache(cache: dict) -> None:
    """Save EUR-Lex cache."""
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


def _extract_celex_from_normativa() -> list[dict]:
    """Extract acts with CELEX from compose mart (fallback legacy)."""
    src = next((p for p in NORMATIVA_CANDIDATES if p.exists()), None)
    if src is None:
        raise FileNotFoundError("Nessuna fonte normativa per estrazione CELEX")
    con = duckdb.connect(":memory:")
    con.execute(f"CREATE TABLE n AS SELECT * FROM read_parquet('{src}')")
    # Compose mart usa `id` come URN; upstream normativa usa `urn`
    cols = [r[0] for r in con.execute("DESCRIBE n").fetchall()]
    urn_col = "id" if "id" in cols else "urn"
    year_col = "anno" if "anno" in cols else "anno_atto"
    title_col = "title" if "title" in cols else "oggetto"
    file_col = "source_filename" if "source_filename" in cols else "filename"
    result = con.execute(f"""
        SELECT {urn_col} AS urn, celex, tipo, {year_col} AS anno_atto, {title_col} AS oggetto,
               {file_col} AS filename
        FROM n
        WHERE NULLIF(celex, '') IS NOT NULL
    """).fetchall()
    con.close()
    return [
        {"urn": r[0], "celex": r[1], "tipo": r[2], "anno": r[3], "oggetto": r[4], "filename": r[5]}
        for r in result
    ]


def _fetch_eurlex_metadata(celex: str) -> dict | None:
    """Fetch ELI metadata from EUR-Lex HTML page."""
    url = f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex}"
    req = Request(url, headers={"User-Agent": "LegalGraph/0.1"})

    try:
        with urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError):
        return None

    # Parse ELI metadata
    meta = {}

    # Document type
    type_match = re.search(r'property="eli:type_document"\s+resource="[^"]*/([^"]+)"', html)
    if type_match:
        meta["eu_type"] = type_match.group(1)

    # Dates
    date_doc = re.search(r'property="eli:date_document"\s+content="([^"]+)"', html)
    if date_doc:
        meta["date_document"] = date_doc.group(1)

    date_pub = re.search(r'property="eli:date_publication"\s+content="([^"]+)"', html)
    if date_pub:
        meta["date_publication"] = date_pub.group(1)

    # EuroVoc concepts
    eurovoc = re.findall(r'property="eli:is_about"\s+resource="(http://eurovoc\.europa\.eu/(\d+))"', html)
    if eurovoc:
        meta["eurovoc_ids"] = [e[1] for e in eurovoc]

    # Title (from DC.title or page title)
    title_match = re.search(r'<meta\s+name="DC\.title"\s+content="([^"]+)"', html)
    if title_match:
        meta["title"] = title_match.group(1)[:200]
    else:
        # Fallback: extract from page title
        title_match = re.search(r'<title>([^<]+)</title>', html)
        if title_match:
            meta["title"] = title_match.group(1)[:200]

    # Celex ID (confirm)
    meta["celex"] = celex

    return meta if meta.get("eu_type") else None


def _build_eu_nodes(con: duckdb.DuckDBPyConnection, cache: dict) -> int:
    """Build EU legislation nodes from cache."""
    # Get unique target CELEX (deduplicate)
    seen_celex: dict[str, dict] = {}
    for celex, meta in cache.items():
        if celex not in seen_celex and meta:
            seen_celex[celex] = meta

    if not seen_celex:
        return 0

    # Build table in DuckDB
    rows = []
    for celex, meta in seen_celex.items():
        eu_type_raw = meta.get("eu_type", "")
        eu_type = CELEX_TYPES.get(eu_type_raw, eu_type_raw)
        rows.append((
            f"celex:{celex}",
            eu_type,
            meta.get("date_document", ""),
            meta.get("date_publication", ""),
            meta.get("title", "")[:200],
            celex,
            json.dumps(meta.get("eurovoc_ids", [])),
        ))

    con.execute("""
        CREATE TABLE eu_nodes (
            id VARCHAR, tipo VARCHAR, date_document VARCHAR,
            date_publication VARCHAR, title VARCHAR, celex VARCHAR,
            eurovoc_ids VARCHAR
        )
    """)
    for row in rows:
        con.execute("INSERT INTO eu_nodes VALUES (?, ?, ?, ?, ?, ?, ?)", row)

    return len(rows)


def _build_eu_edges(con: duckdb.DuckDBPyConnection) -> int:
    """Build edges: atto italiano → atto UE (recepisce/attua)."""
    con.execute("""
        CREATE TABLE eu_edges AS
        SELECT
            n.urn AS source_id,
            CASE
                WHEN en.tipo = 'DIRETTIVA' THEN 'recepisce_direttiva'
                WHEN en.tipo = 'REGOLAMENTO' THEN 'attua_regolamento'
                ELSE 'collega_ue'
            END AS relation,
            en.id AS target_id,
            1 AS weight,
            n.anno AS source_year,
            NULL AS target_year,
            en.title AS evidence
        FROM nodes_italia n
        JOIN eu_nodes en ON n.celex = en.celex
    """)
    return con.execute("SELECT COUNT(*) FROM eu_edges").fetchone()[0]


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="EUR-Lex enrichment")
    parser.add_argument("--limit", type=int, default=0, help="Limit CELEX to fetch (0=all)")
    parser.add_argument("--cache-only", action="store_true", help="Only use cached data")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between requests (seconds)")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Extract CELEX from italia-corpus
    print("Estrazione CELEX da italia-corpus...")
    acts = _extract_celex_from_normativa()
    unique_celex = {a["celex"] for a in acts}
    print(f"  {len(acts)} atti con CELEX, {len(unique_celex)} CELEX unici")

    # 2. Load cache
    cache = _load_cache()
    print(f"  Cache: {len(cache)} CELEX già fetchati")

    # 3. Fetch missing CELEX
    to_fetch = [c for c in unique_celex if c not in cache]
    if args.limit > 0:
        to_fetch = to_fetch[:args.limit]

    if to_fetch and not args.cache_only:
        print(f"\nFetch di {len(to_fetch)} CELEX da EUR-Lex...")
        fetched = 0
        errors = 0
        for i, celex in enumerate(to_fetch):
            meta = _fetch_eurlex_metadata(celex)
            if meta:
                cache[celex] = meta
                fetched += 1
            else:
                cache[celex] = None
                errors += 1

            if (i + 1) % 20 == 0:
                print(f"  [{i+1}/{len(to_fetch)}] fetched={fetched} errors={errors}")
                _save_cache(cache)

            time.sleep(args.delay)

        _save_cache(cache)
        print(f"  Completato: {fetched} fetched, {errors} errori")
    else:
        print("  Nessun CELEX da fetchare (o --cache-only)")

    # 4. Build nodes and edges
    print("\nCostruzione nodi e archi EU...")
    con = duckdb.connect(":memory:")

    # Load Italia nodes (compose mart → upstream → legacy)
    italia_nodes = next((p for p in NORMATIVA_CANDIDATES if p.exists()), None)
    if italia_nodes is None:
        raise FileNotFoundError("Nessuna fonte nodi Italia trovata per eu_enrichment")
    print(f"  Fonte nodi Italia: {italia_nodes}")
    con.execute(
        f"CREATE TABLE nodes_italia AS SELECT id AS urn, celex, anno "
        f"FROM read_parquet('{italia_nodes}') WHERE NULLIF(celex, '') IS NOT NULL"
    )
    n_italia = con.execute("SELECT COUNT(*) FROM nodes_italia").fetchone()[0]
    print(f"  Att italiani con CELEX: {n_italia}")

    # Build EU nodes
    n_eu = _build_eu_nodes(con, cache)
    print(f"  Nodi UE creati: {n_eu}")

    # Build EU edges
    n_edges = _build_eu_edges(con)
    print(f"  Archi EU creati: {n_edges}")

    # 5. Save
    if n_eu > 0:
        con.execute(f"COPY eu_nodes TO '{EU_NODES_FILE}' (FORMAT PARQUET, COMPRESSION 'zstd')")
        print(f"\nSalvato: {EU_NODES_FILE}")
    if n_edges > 0:
        con.execute(f"COPY eu_edges TO '{EU_EDGES_FILE}' (FORMAT PARQUET, COMPRESSION 'zstd')")
        print(f"Salvato: {EU_EDGES_FILE}")

    # Summary
    if n_eu > 0:
        print("\nPer tipo EU:")
        result = con.execute("SELECT tipo, COUNT(*) FROM eu_nodes GROUP BY tipo ORDER BY COUNT(*) DESC").fetchall()
        for tipo, cnt in result:
            print(f"  {tipo:20s}: {cnt:>4}")

    if n_edges > 0:
        print("\nPer relazione:")
        result = con.execute("SELECT relation, COUNT(*) FROM eu_edges GROUP BY relation ORDER BY COUNT(*) DESC").fetchall()
        for rel, cnt in result:
            print(f"  {rel:25s}: {cnt:>4}")

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
