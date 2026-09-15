#!/usr/bin/env python3
"""Legal Graph — Build unified legal edges from multiple sources.

Consumes:
  - italia-corpus: data/derived/riferimenti.parquet (with tipo_riferimento)
  - costituzione-italiana: out/data/clean/ (massime, atti-promovimento, pronunce)
  - gu-monitor: data/gu_acts.parquet
  - open-politica: out/data/clean/senato_ddl
  - senato-akn: out/data/clean/ (senato_corpus, senato_emendamenti, senato_dibattito)
  - EUR-Lex: data/legal_edges_eu.parquet

Produces:
  - data/legal_edges.parquet (deduplicated by source_id, target_id, relation)
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent
OUTDIR = Path(__file__).resolve().parent.parent / "data"

# Source paths
RIFERIMENTI = WORKSPACE / "italia-corpus" / "data" / "derived" / "riferimenti.parquet"
MASSIME = WORKSPACE / "costituzione-italiana" / "out" / "data" / "clean" / "massime_corte_costituzionale" / "2026" / "massime_corte_costituzionale_2026_clean.parquet"
ATTI_PROMOVIMENTO = WORKSPACE / "costituzione-italiana" / "out" / "data" / "clean" / "atti_promovimento_corte_costituzionale" / "2026" / "atti_promovimento_corte_costituzionale_2026_clean.parquet"
PRONUNCE = WORKSPACE / "costituzione-italiana" / "out" / "data" / "clean" / "pronunce_corte_costituzionale" / "2026" / "pronunce_corte_costituzionale_2026_clean.parquet"
GU_ACTS = WORKSPACE / "gu-monitor" / "data" / "gu_acts.parquet"
NORMATIVA = WORKSPACE / "italia-corpus" / "data" / "derived" / "normativa.parquet"
SENATO_CORPUS = WORKSPACE / "senato-akn" / "out" / "data" / "clean" / "senato_corpus" / "2026" / "senato_corpus_2026_clean.parquet"
SENATO_EMEND = WORKSPACE / "senato-akn" / "out" / "data" / "clean" / "senato_emendamenti" / "2026" / "senato_emendamenti_2026_clean.parquet"
SENATO_DIB = WORKSPACE / "senato-akn" / "out" / "data" / "clean" / "senato_dibattito" / "2026" / "senato_dibattito_2026_clean.parquet"
CAMERA_DDL_FILES = sorted(WORKSPACE.glob("open-politica/out/data/clean/camera_ddl/*/camera_ddl_*_clean.parquet"))
CAMERA_LEGGI_FILES = sorted(WORKSPACE.glob("open-politica/out/data/clean/camera_leggi/*/camera_leggi_*_clean.parquet"))


def build_edges(con: duckdb.DuckDBPyConnection) -> None:
    """Build legal_edges from all sources, deduplicated."""

    # Discover DDL files across all legislature years
    senato_ddl_files = sorted(WORKSPACE.glob("open-politica/out/data/clean/senato_ddl/*/senato_ddl_*_clean.parquet"))

    # 0. Build a URN lookup from normativa (filename → urn)
    #    Deduplicate by filename (one URN per file)
    if NORMATIVA.exists():
        con.execute(f"""
            CREATE TABLE urn_lookup AS
            SELECT * FROM (
                SELECT
                    filename, urn,
                    ROW_NUMBER() OVER (PARTITION BY filename ORDER BY urn) AS _rn
                FROM read_parquet('{NORMATIVA}')
                WHERE NULLIF(urn, '') IS NOT NULL
            ) WHERE _rn = 1
        """)
        n = con.execute("SELECT COUNT(*) FROM urn_lookup").fetchone()[0]
        print(f"  URN lookup:     {n:>6} atti (deduplicated)")
    else:
        con.execute("CREATE TABLE urn_lookup (filename VARCHAR, urn VARCHAR)")

    # 0b. Build a DDL ID → resolved ID lookup for emendamento/corpus target resolution
    #     Always use senato:id_ddl — DDL nodes are always 'senato:{id_ddl}'
    if senato_ddl_files:
        globs_ddl = ", ".join(f"'{f}'" for f in senato_ddl_files)
        con.execute(f"""
            CREATE TABLE ddl_urn_lookup AS
            SELECT * FROM (
                SELECT
                    id_ddl,
                    'senato:' || CAST(id_ddl AS VARCHAR) AS resolved_id,
                    ROW_NUMBER() OVER (PARTITION BY id_ddl ORDER BY legislatura DESC) AS _rn
                FROM read_parquet([{globs_ddl}])
                WHERE id_ddl IS NOT NULL
            ) WHERE _rn = 1
        """)
        n = con.execute("SELECT COUNT(*) FROM ddl_urn_lookup").fetchone()[0]
        print(f"  DDL URN lookup: {n:>6} DDL")
    else:
        con.execute("CREATE TABLE ddl_urn_lookup (id_ddl BIGINT, resolved_id VARCHAR)")

    # 1. Riferimenti (atti → atti cross-references) + Citazioni costituzionali
    #    riferimenti.parquet now has tipo_riferimento (atto/costituzione) and articolo_costituzione
    if RIFERIMENTI.exists():
        # 1a. Riferimenti atto → atto
        con.execute(f"""
            CREATE TABLE edges_riferimenti AS
            SELECT
                COALESCE(lu.urn, 'file:' || r.fonte_filename) AS source_id,
                'riferimento' AS relation,
                COALESCE(lu2.urn, 'file:' || r.bersaglio_filename) AS target_id,
                r.peso AS weight,
                r.fonte_anno AS source_year,
                r.bersaglio_anno AS target_year,
                NULL AS evidence
            FROM read_parquet('{RIFERIMENTI}') r
            LEFT JOIN urn_lookup lu
              ON regexp_extract(r.fonte_filename, '/([^/]+)$', 1) = lu.filename
            LEFT JOIN urn_lookup lu2
              ON r.bersaglio_filename = lu2.filename
            WHERE r.risolto = true AND r.tipo_riferimento = 'atto'
        """)
        n = con.execute("SELECT COUNT(*) FROM edges_riferimenti").fetchone()[0]
        print(f"  riferimenti:    {n:>6} archi (atto→atto)")

        # 1b. Citazioni costituzionali (atto → articolo Costituzione)
        con.execute(f"""
            CREATE TABLE edges_citazioni AS
            SELECT
                COALESCE(lu.urn, 'file:' || r.fonte_filename) AS source_id,
                'cita_costituzione' AS relation,
                'costituzione:art:' || CAST(CAST(r.articolo_costituzione AS INTEGER) AS VARCHAR) AS target_id,
                1 AS weight,
                r.fonte_anno AS source_year,
                NULL AS target_year,
                LEFT(r.contesto, 200) AS evidence
            FROM read_parquet('{RIFERIMENTI}') r
            LEFT JOIN urn_lookup lu ON regexp_extract(r.fonte_filename, '/([^/]+)$', 1) = lu.filename
            WHERE r.tipo_riferimento = 'costituzione'
              AND r.articolo_costituzione IS NOT NULL
        """)
        n = con.execute("SELECT COUNT(*) FROM edges_citazioni").fetchone()[0]
        print(f"  citazioni:      {n:>6} archi (atto→costituzione)")
    else:
        con.execute("CREATE TABLE edges_riferimenti (source_id VARCHAR, relation VARCHAR, target_id VARCHAR, weight DOUBLE, source_year INTEGER, target_year INTEGER, evidence VARCHAR)")
        con.execute("CREATE TABLE edges_citazioni (source_id VARCHAR, relation VARCHAR, target_id VARCHAR, weight INTEGER, source_year INTEGER, target_year INTEGER, evidence VARCHAR)")
        print("  riferimenti:    NON TROVATO")

    # 3. Massime → norme impugnate (sentenze che dichiarano incostituzionale)
    if MASSIME.exists():
        con.execute(f"""
            CREATE TABLE edges_massime AS
            SELECT
                'sentenza:' || CAST(anno_pronuncia AS VARCHAR) || '-' || LPAD(CAST(numero_pronuncia AS VARCHAR), 4, '0') AS source_id,
                'impugna' AS relation,
                'norma:' || LOWER(COALESCE(norma_descrizione, 'legge'))
                       || ':' || CAST(norma_numero AS VARCHAR)
                       || ':' || CAST(YEAR(TRY_CAST(norma_data AS DATE)) AS VARCHAR) AS target_id,
                1 AS weight,
                anno_pronuncia AS source_year,
                NULL AS target_year,
                norma_descrizione AS evidence
            FROM read_parquet('{MASSIME}')
            WHERE esito IN ('illegittimo', 'misto')
              AND NULLIF(norma_numero, '') IS NOT NULL
              AND TRY_CAST(norma_data AS DATE) IS NOT NULL
        """)
        n = con.execute("SELECT COUNT(*) FROM edges_massime").fetchone()[0]
        print(f"  impugna:        {n:>6} archi")

        # 3b. Massime → parametro costituzionale
        con.execute(f"""
            CREATE TABLE edges_parametri AS
            SELECT
                'sentenza:' || CAST(anno_pronuncia AS VARCHAR) || '-' || LPAD(CAST(numero_pronuncia AS VARCHAR), 4, '0') AS source_id,
                'invoca_parametro' AS relation,
                'costituzione:art:' || CAST(CAST(parametro_articolo AS INTEGER) AS VARCHAR) AS target_id,
                1 AS weight,
                anno_pronuncia AS source_year,
                NULL AS target_year,
                NULL AS evidence
            FROM read_parquet('{MASSIME}')
            WHERE NULLIF(CAST(parametro_articolo AS VARCHAR), '') IS NOT NULL
              AND CAST(parametro_articolo AS VARCHAR) != '0'
        """)
        n = con.execute("SELECT COUNT(*) FROM edges_parametri").fetchone()[0]
        print(f"  invoca_param:   {n:>6} archi")
    else:
        con.execute("CREATE TABLE edges_massime (source_id VARCHAR, relation VARCHAR, target_id VARCHAR, weight INTEGER, source_year INTEGER, target_year INTEGER, evidence VARCHAR)")
        con.execute("CREATE TABLE edges_parametri (source_id VARCHAR, relation VARCHAR, target_id VARCHAR, weight INTEGER, source_year INTEGER, target_year INTEGER, evidence VARCHAR)")
        print("  massime:        NON TROVATO")

    # 4. Atti di promovimento (parametro evocato)
    if ATTI_PROMOVIMENTO.exists():
        con.execute(f"""
            CREATE TABLE edges_promovimento AS
            SELECT
                'promovimento:' || CAST(anno AS VARCHAR) || '-' || LPAD(CAST(numero_atto AS VARCHAR), 4, '0') AS source_id,
                'evoca_parametro' AS relation,
                'costituzione:art:' || CAST(parametro_articolo AS VARCHAR) AS target_id,
                1 AS weight,
                anno AS source_year,
                NULL AS target_year,
                NULL AS evidence
            FROM read_parquet('{ATTI_PROMOVIMENTO}')
        """)
        n = con.execute("SELECT COUNT(*) FROM edges_promovimento").fetchone()[0]
        print(f"  evoca_param:    {n:>6} archi")
    else:
        con.execute("CREATE TABLE edges_promovimento (source_id VARCHAR, relation VARCHAR, target_id VARCHAR, weight INTEGER, source_year INTEGER, target_year INTEGER, evidence VARCHAR)")
        print("  promovimento:   NON TROVATO")

    # 5. Senato DDL → normativa (proposed → enacted law)
    #    Source: senato:id_ddl (DDL node), Target: urn_normattiva (law node)
    if senato_ddl_files:
        globs = ", ".join(f"'{f}'" for f in senato_ddl_files)
        con.execute(f"""
            CREATE TABLE edges_senato AS
            SELECT
                'senato:' || CAST(s.id_ddl AS VARCHAR) AS source_id,
                'diventa_legge' AS relation,
                s.urn_normattiva AS target_id,
                1 AS weight,
                YEAR(s.data_presentazione) AS source_year,
                YEAR(s.data_legge) AS target_year,
                s.titolo_breve AS evidence
            FROM read_parquet([{globs}]) s
            WHERE NULLIF(s.urn_normattiva, '') IS NOT NULL
        """)
        n = con.execute("SELECT COUNT(*) FROM edges_senato").fetchone()[0]
        print(f"  senato->legge:  {n:>6} archi")
    else:
        con.execute("CREATE TABLE edges_senato (source_id VARCHAR, relation VARCHAR, target_id VARCHAR, weight INTEGER, source_year INTEGER, target_year INTEGER, evidence VARCHAR)")
        print("  senato_ddl:     NON TROVATO")

    # 5b. Camera DDL → normativa (proposed → enacted law)
    #     Source: camera:id_ddl (DDL node), Target: urn_normattiva (law node)
    if CAMERA_LEGGI_FILES:
        globs_cam = ", ".join(f"'{f}'" for f in CAMERA_LEGGI_FILES)
        con.execute(f"""
            CREATE TABLE edges_camera_ddl AS
            SELECT
                'camera:' || CAST(l.ddl_numero AS VARCHAR) AS source_id,
                'diventa_legge' AS relation,
                l.urn_normattiva AS target_id,
                1 AS weight,
                l.anno AS source_year,
                l.anno AS target_year,
                l.titolo AS evidence
            FROM read_parquet([{globs_cam}]) l
            WHERE NULLIF(l.urn_normattiva, '') IS NOT NULL
              AND l.ddl_numero IS NOT NULL
        """)
        n = con.execute("SELECT COUNT(*) FROM edges_camera_ddl").fetchone()[0]
        print(f"  camera->legge: {n:>6} archi")
    else:
        con.execute("CREATE TABLE edges_camera_ddl (source_id VARCHAR, relation VARCHAR, target_id VARCHAR, weight INTEGER, source_year INTEGER, target_year INTEGER, evidence VARCHAR)")
        print("  camera_leggi:  NON TROVATO")

    # 6. Senato Corpus → senato_ddl (atto testuale → iter legislativo)
    if SENATO_CORPUS.exists():
        con.execute(f"""
            CREATE TABLE edges_corpus AS
            SELECT
                'senato:atto:' || CAST(c.atto_num AS VARCHAR) AS source_id,
                'testo_atto' AS relation,
                COALESCE(d.resolved_id, 'senato:' || CAST(c.atto_num AS VARCHAR)) AS target_id,
                1 AS weight,
                YEAR(c.work_date) AS source_year,
                NULL AS target_year,
                c.famiglia AS evidence
            FROM read_parquet('{SENATO_CORPUS}') c
            LEFT JOIN ddl_urn_lookup d ON c.atto_num = d.id_ddl
            WHERE c.atto_num IS NOT NULL
        """)
        n = con.execute("SELECT COUNT(*) FROM edges_corpus").fetchone()[0]
        print(f"  senato_corpus: {n:>6} archi")
    else:
        con.execute("CREATE TABLE edges_corpus (source_id VARCHAR, relation VARCHAR, target_id VARCHAR, weight INTEGER, source_year INTEGER, target_year INTEGER, evidence VARCHAR)")
        print("  senato_corpus: NON TROVATO")

    # 7. Senato Emendamenti → DDL (emendamento → atto)
    #    Source ID includes legislature (matching node ID format)
    #    Target ID resolved to URN via ddl_urn_lookup
    if SENATO_EMEND.exists() and senato_ddl_files:
        globs_ddl = ", ".join(f"'{f}'" for f in senato_ddl_files)
        con.execute(f"""
            CREATE TABLE edges_emend AS
            SELECT
                'senato:emend:' || CAST(regexp_extract(e.legislatura, '(\\d+)', 1) AS BIGINT) || ':' || e.emend_id AS source_id,
                'emendamento' AS relation,
                COALESCE(ddl.resolved_id, 'senato:' || CAST(d.id_ddl AS VARCHAR)) AS target_id,
                1 AS weight,
                YEAR(e.work_date) AS source_year,
                NULL AS target_year,
                LEFT(e.text_integrale, 100) AS evidence
            FROM read_parquet('{SENATO_EMEND}') e
            JOIN read_parquet([{globs_ddl}]) d
              ON e.fase = d.fase
             AND CAST(regexp_extract(e.legislatura, '(\\d+)', 1) AS BIGINT) = d.legislatura
            LEFT JOIN ddl_urn_lookup ddl ON d.id_ddl = ddl.id_ddl
            WHERE e.emend_id IS NOT NULL AND e.fase IS NOT NULL
              AND e.legislatura IS NOT NULL
        """)
        n = con.execute("SELECT COUNT(*) FROM edges_emend").fetchone()[0]
        print(f"  senato_emend:  {n:>6} archi")
    else:
        con.execute("CREATE TABLE edges_emend (source_id VARCHAR, relation VARCHAR, target_id VARCHAR, weight INTEGER, source_year INTEGER, target_year INTEGER, evidence VARCHAR)")
        print("  senato_emend:  NON TROVATO")

    # 8. Senato Dibattito → Senatore (intervento → senatore)
    #    Source ID matches deduplicated node ID format
    if SENATO_DIB.exists():
        con.execute(f"""
            CREATE TABLE edges_dib AS
            SELECT
                'senato:dib:' || CAST(senatore_id AS VARCHAR) || ':' || CAST(data_seduta AS VARCHAR) || ':' || CAST(ordine_intervento AS VARCHAR) AS source_id,
                'intervento' AS relation,
                'senatore:' || CAST(senatore_id AS VARCHAR) AS target_id,
                1 AS weight,
                YEAR(data_seduta) AS source_year,
                NULL AS target_year,
                nome_oratore AS evidence
            FROM read_parquet('{SENATO_DIB}')
            WHERE senatore_id IS NOT NULL
        """)
        n = con.execute("SELECT COUNT(*) FROM edges_dib").fetchone()[0]
        print(f"  senato_dib:    {n:>6} archi")
    else:
        con.execute("CREATE TABLE edges_dib (source_id VARCHAR, relation VARCHAR, target_id VARCHAR, weight INTEGER, source_year INTEGER, target_year INTEGER, evidence VARCHAR)")
        print("  senato_dib:    NON TROVATO")

    # 9. Attua delega: D.Lgs → Legge di delega
    #    Heuristica: D.Lgs che referenziano leggi con "Delega al Governo" nel titolo
    #    con peso ≥ 5 sono quasi certamente i loro attuatori.
    #    Serve la tabella nodes per verificare i tipi e i titoli.
    nodes_file = OUTDIR / "legal_nodes.parquet"
    if nodes_file.exists():
        con.execute(f"CREATE TABLE _nodes_ref AS SELECT id, tipo, title FROM read_parquet('{nodes_file}')")
        con.execute("""
            CREATE TABLE edges_delega AS
            SELECT
                e.source_id AS source_id,
                'attua_delega' AS relation,
                e.target_id AS target_id,
                e.weight AS weight,
                e.source_year AS source_year,
                e.target_year AS target_year,
                n2.title AS evidence
            FROM edges_riferimenti e
            JOIN _nodes_ref n1 ON e.source_id = n1.id
            JOIN _nodes_ref n2 ON e.target_id = n2.id
            WHERE n1.tipo = 'DECRETO LEGISLATIVO'
              AND n2.tipo = 'LEGGE'
              AND LOWER(n2.title) LIKE '%delega al governo%'
              AND e.weight >= 5
        """)
        con.execute("DROP TABLE _nodes_ref")
        n = con.execute("SELECT COUNT(*) FROM edges_delega").fetchone()[0]
        print(f"  attua_delega:  {n:>6} archi")
    else:
        con.execute("CREATE TABLE edges_delega (source_id VARCHAR, relation VARCHAR, target_id VARCHAR, weight INTEGER, source_year INTEGER, target_year INTEGER, evidence VARCHAR)")
        print("  attua_delega:  NON TROVATO (nodes mancante)")

    # 10. EU edges (recepisce/attua)
    eu_edges_file = OUTDIR / "legal_edges_eu.parquet"
    if eu_edges_file.exists():
        con.execute(f"""
            CREATE TABLE edges_eu AS
            SELECT * FROM read_parquet('{eu_edges_file}')
        """)
        n = con.execute("SELECT COUNT(*) FROM edges_eu").fetchone()[0]
        print(f"  eu (EUR-Lex):  {n:>6} archi")
    else:
        con.execute("CREATE TABLE edges_eu (source_id VARCHAR, relation VARCHAR, target_id VARCHAR, weight INTEGER, source_year INTEGER, target_year INTEGER, evidence VARCHAR)")
        print("  eu (EUR-Lex):  NON TROVATO")

    # Union all edges, then DEDUPLICATE
    # Same (source_id, target_id, relation) can appear from multiple sources
    # → aggregate: sum weight, take first evidence, take min/max years
    con.execute("""
        CREATE TABLE legal_edges AS
        SELECT
            source_id,
            relation,
            target_id,
            SUM(weight) AS weight,
            MIN(source_year) AS source_year,
            MAX(target_year) AS target_year,
            FIRST(evidence) AS evidence
        FROM (
            SELECT * FROM edges_riferimenti
            UNION ALL
            SELECT * FROM edges_citazioni
            UNION ALL
            SELECT * FROM edges_massime
            UNION ALL
            SELECT * FROM edges_parametri
            UNION ALL
            SELECT * FROM edges_promovimento
            UNION ALL
            SELECT * FROM edges_senato
            UNION ALL
            SELECT * FROM edges_camera_ddl
            UNION ALL
            SELECT * FROM edges_delega
            UNION ALL
            SELECT * FROM edges_corpus
            UNION ALL
            SELECT * FROM edges_emend
            UNION ALL
            SELECT * FROM edges_dib
            UNION ALL
            SELECT * FROM edges_eu
        )
        GROUP BY source_id, relation, target_id
    """)

    n = con.execute("SELECT COUNT(*) FROM legal_edges").fetchone()[0]
    print(f"\n  TOTALE:         {n:>6} archi (deduplicated)")


def main() -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTDIR / "legal_edges.parquet"

    con = duckdb.connect(":memory:")

    print("Building legal edges...")
    build_edges(con)

    con.execute(f"COPY legal_edges TO '{out_file}' (FORMAT PARQUET, COMPRESSION 'zstd')")
    print(f"\nSalvato: {out_file}")

    # Summary
    by_relation = con.execute("""
        SELECT relation, COUNT(*) as n
        FROM legal_edges
        GROUP BY relation
        ORDER BY n DESC
    """).fetchall()
    print("\nPer relazione:")
    for rel, count in by_relation:
        print(f"  {rel:25s}: {count:>6}")

    # Graph connectivity stats
    n_src = con.execute("SELECT COUNT(DISTINCT source_id) FROM legal_edges").fetchone()[0]
    n_tgt = con.execute("SELECT COUNT(DISTINCT target_id) FROM legal_edges").fetchone()[0]
    print(f"\nNodi sorgente unici:  {n_src}")
    print(f"Nodi destinazione:    {n_tgt}")

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
