#!/usr/bin/env python3
"""Legal Graph — Build unified legal nodes from multiple sources.

Consumes:
  - italia-corpus: data/derived/normativa.parquet
  - costituzione-italiana: out/data/clean/ (articoli, revisioni, pronunce, giudici)
  - gu-monitor: data/gu_acts.parquet
  - open-politica: out/data/clean/senato_ddl
  - senato-akn: out/data/clean/ (senato_corpus, senato_emendamenti, senato_dibattito)
  - EUR-Lex: data/legal_nodes_eu.parquet

Produces:
  - data/legal_nodes.parquet (deduplicated by ID)
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent
OUTDIR = Path(__file__).resolve().parent.parent / "data"

# Source paths
NORMATIVA = WORKSPACE / "italia-corpus" / "data" / "derived" / "normativa.parquet"
REVISIONI = WORKSPACE / "costituzione-italiana" / "out" / "data" / "clean" / "revisioni_costituzionali" / "2026" / "revisioni_costituzionali_2026_clean.parquet"
ARTICOLI = WORKSPACE / "costituzione-italiana" / "out" / "data" / "clean" / "articoli_costituzione" / "2026" / "articoli_costituzione_2026_clean.parquet"
PRONUNCE = WORKSPACE / "costituzione-italiana" / "out" / "data" / "clean" / "pronunce_corte_costituzionale" / "2026" / "pronunce_corte_costituzionale_2026_clean.parquet"
GIUDICI = WORKSPACE / "costituzione-italiana" / "out" / "data" / "clean" / "giudici_corte_costituzionale" / "2026" / "giudici_corte_costituzionale_2026_clean.parquet"
GU_ACTS = WORKSPACE / "gu-monitor" / "data" / "gu_acts.parquet"
SENATO_CORPUS = WORKSPACE / "senato-akn" / "out" / "data" / "clean" / "senato_corpus" / "2026" / "senato_corpus_2026_clean.parquet"
SENATO_EMEND = WORKSPACE / "senato-akn" / "out" / "data" / "clean" / "senato_emendamenti" / "2026" / "senato_emendamenti_2026_clean.parquet"
SENATO_DIB = WORKSPACE / "senato-akn" / "out" / "data" / "clean" / "senato_dibattito" / "2026" / "senato_dibattito_2026_clean.parquet"


def build_nodes(con: duckdb.DuckDBPyConnection) -> None:
    """Build legal_nodes from all sources, deduplicated by ID."""

    # 1. Normativa (italia-corpus) — the backbone
    #    Deduplicate by URN (same law can appear in multiple collezioni)
    if NORMATIVA.exists():
        con.execute(f"""
            CREATE TABLE nodes_normativa AS
            SELECT
                id, tipo, data, numero, title, collezione, source_filename,
                source, anno, length_chars, length_words, celex, vigente
            FROM (
                SELECT
                    urn AS id,
                    tipo,
                    CAST(data AS VARCHAR) AS data,
                    numero,
                    oggetto AS title,
                    collezione,
                    filename AS source_filename,
                    'normativa' AS source,
                    anno_atto AS anno,
                    lunghezza_caratteri AS length_chars,
                    lunghezza_parole AS length_words,
                    NULLIF(celex, '') AS celex,
                    vigente,
                    ROW_NUMBER() OVER (PARTITION BY urn ORDER BY filename) AS _rn
                FROM read_parquet('{NORMATIVA}')
                WHERE NULLIF(urn, '') IS NOT NULL
            ) WHERE _rn = 1
        """)
        n = con.execute("SELECT COUNT(*) FROM nodes_normativa").fetchone()[0]
        print(f"  normativa:      {n:>6} nodi (deduplicated by URN)")
    else:
        con.execute("CREATE TABLE nodes_normativa (id VARCHAR)")
        print("  normativa:      NON TROVATO")

    # 2. Costituzione — articoli (from costituzione-italiana clean)
    if ARTICOLI.exists():
        con.execute(f"""
            CREATE TABLE nodes_articoli AS
            SELECT
                'costituzione:art:' || CAST(articolo AS VARCHAR) AS id,
                'COSTITUZIONE' AS tipo,
                NULL AS data,
                CAST(articolo AS VARCHAR) AS numero,
                heading AS title,
                'Costituzione' AS collezione,
                NULL AS source_filename,
                'costituzione' AS source,
                NULL AS anno,
                NULL AS length_chars,
                commi AS length_words,
                NULL AS celex,
                NULL AS vigente
            FROM read_parquet('{ARTICOLI}')
            WHERE articolo IS NOT NULL
        """)
        n = con.execute("SELECT COUNT(*) FROM nodes_articoli").fetchone()[0]
        print(f"  costituzione:   {n:>6} nodi")
    else:
        con.execute("""CREATE TABLE nodes_articoli (
            id VARCHAR, tipo VARCHAR, data VARCHAR, numero VARCHAR, title VARCHAR,
            collezione VARCHAR, source_filename VARCHAR, source VARCHAR, anno INTEGER,
            length_chars BIGINT, length_words BIGINT, celex VARCHAR, vigente BOOLEAN
        )""")
        print("  costituzione:   NON TROVATO")

    # 3. Revisioni costituzionali (from costituzione-italiana clean)
    if REVISIONI.exists():
        con.execute(f"""
            CREATE TABLE nodes_revisioni AS
            SELECT
                'revisione:' || urn AS id,
                'LEGGE COSTITUZIONALE' AS tipo,
                CAST(data AS VARCHAR) AS data,
                NULL AS numero,
                titolo AS title,
                'Leggi costituzionali' AS collezione,
                NULL AS source_filename,
                'revisioni' AS source,
                YEAR(CAST(data AS DATE)) AS anno,
                NULL AS length_chars,
                n_articoli AS length_words,
                NULL AS celex,
                NULL AS vigente
            FROM read_parquet('{REVISIONI}')
        """)
        n = con.execute("SELECT COUNT(*) FROM nodes_revisioni").fetchone()[0]
        print(f"  revisioni:      {n:>6} nodi")
    else:
        con.execute("""CREATE TABLE nodes_revisioni (
            id VARCHAR, tipo VARCHAR, data VARCHAR, numero VARCHAR, title VARCHAR,
            collezione VARCHAR, source_filename VARCHAR, source VARCHAR, anno INTEGER,
            length_chars BIGINT, length_words BIGINT, celex VARCHAR, vigente BOOLEAN
        )""")
        print("  revisioni:      NON TROVATO")

    # 4. GU acts (only those with URN — they link to normativa)
    if GU_ACTS.exists():
        con.execute(f"""
            CREATE TABLE nodes_gu AS
            SELECT
                COALESCE(NULLIF(urn_normattiva, ''), 'gu:' || id) AS id,
                tipo_atto AS tipo,
                data_pubblicazione AS data,
                NULL AS numero,
                titolo AS title,
                serie AS collezione,
                id AS source_filename,
                'gu' AS source,
                YEAR(CAST(data_pubblicazione AS DATE)) AS anno,
                NULL AS length_chars,
                NULL AS length_words,
                NULL AS celex,
                NULL AS vigente
            FROM read_parquet('{GU_ACTS}')
            WHERE NULLIF(urn_normattiva, '') IS NOT NULL
        """)
        n = con.execute("SELECT COUNT(*) FROM nodes_gu").fetchone()[0]
        print(f"  gu (con URN):   {n:>6} nodi")
    else:
        con.execute("""CREATE TABLE nodes_gu (
            id VARCHAR, tipo VARCHAR, data VARCHAR, numero VARCHAR, title VARCHAR,
            collezione VARCHAR, source_filename VARCHAR, source VARCHAR, anno INTEGER,
            length_chars BIGINT, length_words BIGINT, celex VARCHAR, vigente BOOLEAN
        )""")
        print("  gu:             NON TROVATO")

    # 5. Senato DDL (proposed legislation)
    #    Use URN when available, fall back to senato:id_ddl
    senato_ddl_files = sorted(WORKSPACE.glob("open-politica/out/data/clean/senato_ddl/*/senato_ddl_*_clean.parquet"))
    if senato_ddl_files:
        globs = ", ".join(f"'{f}'" for f in senato_ddl_files)
        con.execute(f"""
            CREATE TABLE nodes_senato AS
            SELECT
                id, tipo, data, numero, title, collezione, source_filename,
                source, anno, length_chars, length_words, celex, vigente
            FROM (
                SELECT
                    COALESCE(
                        NULLIF(urn_normattiva, ''),
                        'senato:' || CAST(id_ddl AS VARCHAR)
                    ) AS id,
                    COALESCE(natura, 'legge') AS tipo,
                    CAST(data_legge AS VARCHAR) AS data,
                    CAST(numero_legge AS VARCHAR) AS numero,
                    COALESCE(titolo_breve, titolo) AS title,
                    'Senato DDL' AS collezione,
                    ddl_url AS source_filename,
                    'senato' AS source,
                    YEAR(data_presentazione) AS anno,
                    NULL AS length_chars,
                    NULL AS length_words,
                    NULL AS celex,
                    NULL AS vigente,
                    ROW_NUMBER() OVER (
                        PARTITION BY COALESCE(NULLIF(urn_normattiva, ''), 'senato:' || CAST(id_ddl AS VARCHAR))
                        ORDER BY id_ddl
                    ) AS _rn
                FROM read_parquet([{globs}])
            ) WHERE _rn = 1
        """)
        n = con.execute("SELECT COUNT(*) FROM nodes_senato").fetchone()[0]
        print(f"  senato_ddl:     {n:>6} nodi ({len(senato_ddl_files)} legislature)")
    else:
        con.execute("""CREATE TABLE nodes_senato (
            id VARCHAR, tipo VARCHAR, data VARCHAR, numero VARCHAR, title VARCHAR,
            collezione VARCHAR, source_filename VARCHAR, source VARCHAR, anno INTEGER,
            length_chars BIGINT, length_words BIGINT, celex VARCHAR, vigente BOOLEAN
        )""")
        print("  senato_ddl:     NON TROVATO")

    # 6. Senato Corpus (testi atti legislativi)
    #    Deduplicate by atto_num (same act can appear in multiple documents)
    if SENATO_CORPUS.exists():
        con.execute(f"""
            CREATE TABLE nodes_corpus AS
            SELECT
                id, tipo, data, numero, title, collezione, source_filename,
                source, anno, length_chars, length_words, celex, vigente
            FROM (
                SELECT
                    'senato:atto:' || CAST(atto_num AS VARCHAR) AS id,
                    tipologia AS tipo,
                    CAST(work_date AS VARCHAR) AS data,
                    FRBRnumber AS numero,
                    doc_title AS title,
                    'Senato Corpus' AS collezione,
                    document_id AS source_filename,
                    'senato_corpus' AS source,
                    YEAR(work_date) AS anno,
                    text_len AS length_chars,
                    paragraphs_count AS length_words,
                    NULL AS celex,
                    NULL AS vigente,
                    ROW_NUMBER() OVER (
                        PARTITION BY atto_num
                        ORDER BY work_date
                    ) AS _rn
                FROM read_parquet('{SENATO_CORPUS}')
                WHERE atto_num IS NOT NULL
            ) WHERE _rn = 1
        """)
        n = con.execute("SELECT COUNT(*) FROM nodes_corpus").fetchone()[0]
        print(f"  senato_corpus:  {n:>6} nodi")
    else:
        con.execute("""CREATE TABLE nodes_corpus (
            id VARCHAR, tipo VARCHAR, data VARCHAR, numero VARCHAR, title VARCHAR,
            collezione VARCHAR, source_filename VARCHAR, source VARCHAR, anno INTEGER,
            length_chars BIGINT, length_words BIGINT, celex VARCHAR, vigente BOOLEAN
        )""")
        print("  senato_corpus: NON TROVATO")

    # 7. Senato Emendamenti
    #    Include legislature in ID for uniqueness across legislature
    #    Deduplicate by (legislatura, emend_id) — same amendment can appear in multiple documents
    if SENATO_EMEND.exists():
        con.execute(f"""
            CREATE TABLE nodes_emend AS
            SELECT
                id, tipo, data, numero, title, collezione, source_filename,
                source, anno, length_chars, length_words, celex, vigente
            FROM (
                SELECT
                    'senato:emend:' || CAST(regexp_extract(legislatura, '(\\d+)', 1) AS BIGINT) || ':' || emend_id AS id,
                    tipologia AS tipo,
                    CAST(work_date AS VARCHAR) AS data,
                    emend_id AS numero,
                    LEFT(text_integrale, 100) AS title,
                    'Senato Emendamenti' AS collezione,
                    document_id AS source_filename,
                    'senato_emend' AS source,
                    YEAR(work_date) AS anno,
                    text_len AS length_chars,
                    NULL AS length_words,
                    NULL AS celex,
                    NULL AS vigente,
                    ROW_NUMBER() OVER (
                        PARTITION BY CAST(regexp_extract(legislatura, '(\\d+)', 1) AS BIGINT), emend_id
                        ORDER BY document_id
                    ) AS _rn
                FROM read_parquet('{SENATO_EMEND}')
                WHERE emend_id IS NOT NULL AND legislatura IS NOT NULL
            ) WHERE _rn = 1
        """)
        n = con.execute("SELECT COUNT(*) FROM nodes_emend").fetchone()[0]
        print(f"  senato_emend:   {n:>6} nodi (deduplicated)")
    else:
        con.execute("""CREATE TABLE nodes_emend (
            id VARCHAR, tipo VARCHAR, data VARCHAR, numero VARCHAR, title VARCHAR,
            collezione VARCHAR, source_filename VARCHAR, source VARCHAR, anno INTEGER,
            length_chars BIGINT, length_words BIGINT, celex VARCHAR, vigente BOOLEAN
        )""")
        print("  senato_emend:   NON TROVATO")

    # 8. Senato Dibattito
    #    Deduplicate by (senatore_id, data_seduta, ordine_intervento)
    if SENATO_DIB.exists():
        con.execute(f"""
            CREATE TABLE nodes_dib AS
            SELECT
                id, tipo, data, numero, title, collezione, source_filename,
                source, anno, length_chars, length_words, celex, vigente
            FROM (
                SELECT
                    'senato:dib:' || CAST(senatore_id AS VARCHAR) || ':' || CAST(data_seduta AS VARCHAR) || ':' || CAST(ordine_intervento AS VARCHAR) AS id,
                    tipologia AS tipo,
                    CAST(data_seduta AS VARCHAR) AS data,
                    nome_oratore AS numero,
                    LEFT(text_intervento, 100) AS title,
                    'Senato Dibattito' AS collezione,
                    document_id AS source_filename,
                    'senato_dib' AS source,
                    YEAR(data_seduta) AS anno,
                    text_len AS length_chars,
                    NULL AS length_words,
                    NULL AS celex,
                    NULL AS vigente,
                    ROW_NUMBER() OVER (
                        PARTITION BY senatore_id, data_seduta, ordine_intervento
                        ORDER BY document_id
                    ) AS _rn
                FROM read_parquet('{SENATO_DIB}')
                WHERE senatore_id IS NOT NULL
            ) WHERE _rn = 1
        """)
        n = con.execute("SELECT COUNT(*) FROM nodes_dib").fetchone()[0]
        print(f"  senato_dib:     {n:>6} nodi (deduplicated)")
    else:
        con.execute("""CREATE TABLE nodes_dib (
            id VARCHAR, tipo VARCHAR, data VARCHAR, numero VARCHAR, title VARCHAR,
            collezione VARCHAR, source_filename VARCHAR, source VARCHAR, anno INTEGER,
            length_chars BIGINT, length_words BIGINT, celex VARCHAR, vigente BOOLEAN
        )""")
        print("  senato_dib:     NON TROVATO")

    # 8b. Senatore nodes (extracted from dibattito — needed for edge targets)
    if SENATO_DIB.exists():
        con.execute(f"""
            CREATE TABLE nodes_senatori AS
            SELECT
                id, tipo, data, numero, title, collezione, source_filename,
                source, anno, length_chars, length_words, celex, vigente
            FROM (
                SELECT
                    'senatore:' || CAST(senatore_id AS VARCHAR) AS id,
                    'SENATORE' AS tipo,
                    CAST(data_seduta AS VARCHAR) AS data,
                    nome_oratore AS numero,
                    nome_oratore AS title,
                    'Senato Dibattito' AS collezione,
                    NULL AS source_filename,
                    'senato_dib' AS source,
                    YEAR(data_seduta) AS anno,
                    NULL AS length_chars,
                    NULL AS length_words,
                    NULL AS celex,
                    NULL AS vigente,
                    ROW_NUMBER() OVER (
                        PARTITION BY senatore_id
                        ORDER BY data_seduta
                    ) AS _rn
                FROM read_parquet('{SENATO_DIB}')
                WHERE senatore_id IS NOT NULL
            ) WHERE _rn = 1
        """)
        n = con.execute("SELECT COUNT(*) FROM nodes_senatori").fetchone()[0]
        print(f"  senatori:       {n:>6} nodi (from dibattito)")
    else:
        con.execute("""CREATE TABLE nodes_senatori (
            id VARCHAR, tipo VARCHAR, data VARCHAR, numero VARCHAR, title VARCHAR,
            collezione VARCHAR, source_filename VARCHAR, source VARCHAR, anno INTEGER,
            length_chars BIGINT, length_words BIGINT, celex VARCHAR, vigente BOOLEAN
        )""")

    # 9. EU legislation (from EUR-Lex enrichment)
    eu_nodes_file = OUTDIR / "legal_nodes_eu.parquet"
    if eu_nodes_file.exists():
        con.execute(f"""
            CREATE TABLE nodes_eu AS
            SELECT
                id,
                tipo,
                NULLIF(date_document, '') AS data,
                NULL AS numero,
                title,
                'EUR-Lex' AS collezione,
                NULL AS source_filename,
                'eu' AS source,
                NULL AS anno,
                NULL AS length_chars,
                NULL AS length_words,
                celex,
                NULL AS vigente
            FROM read_parquet('{eu_nodes_file}')
        """)
        n = con.execute("SELECT COUNT(*) FROM nodes_eu").fetchone()[0]
        print(f"  eu (EUR-Lex):  {n:>6} nodi")
    else:
        con.execute("""CREATE TABLE nodes_eu (
            id VARCHAR, tipo VARCHAR, data VARCHAR, numero VARCHAR, title VARCHAR,
            collezione VARCHAR, source_filename VARCHAR, source VARCHAR, anno INTEGER,
            length_chars BIGINT, length_words BIGINT, celex VARCHAR, vigente BOOLEAN
        )""")
        print("  eu (EUR-Lex):  NON TROVATO (eseguire eu_enrichment.py)")

    # 10. Pronunce Corte Costituzionale (sentenze come nodi)
    if PRONUNCE.exists():
        con.execute(f"""
            CREATE TABLE nodes_pronunce AS
            SELECT
                'sentenza:' || CAST(anno_pronuncia AS VARCHAR) || '-' || LPAD(CAST(numero_pronuncia AS VARCHAR), 4, '0') AS id,
                COALESCE(tipologia_pronuncia, 'SENTENZA') AS tipo,
                CAST(data_decisione AS VARCHAR) AS data,
                CAST(numero_pronuncia AS VARCHAR) AS numero,
                COALESCE(ecli, '') AS title,
                'Corte Costituzionale' AS collezione,
                ecli AS source_filename,
                'costituzione' AS source,
                anno_pronuncia AS anno,
                NULL AS length_chars,
                NULL AS length_words,
                NULL AS celex,
                NULL AS vigente
            FROM read_parquet('{PRONUNCE}')
            WHERE anno_pronuncia IS NOT NULL AND numero_pronuncia IS NOT NULL
        """)
        n = con.execute("SELECT COUNT(*) FROM nodes_pronunce").fetchone()[0]
        print(f"  pronunce:      {n:>6} nodi")
    else:
        con.execute("""CREATE TABLE nodes_pronunce (
            id VARCHAR, tipo VARCHAR, data VARCHAR, numero VARCHAR, title VARCHAR,
            collezione VARCHAR, source_filename VARCHAR, source VARCHAR, anno INTEGER,
            length_chars BIGINT, length_words BIGINT, celex VARCHAR, vigente BOOLEAN
        )""")
        print("  pronunce:      NON TROVATO")

    # 11. Giudici Corte Costituzionale
    if GIUDICI.exists():
        con.execute(f"""
            CREATE TABLE nodes_giudici AS
            SELECT
                'giudice:' || REPLACE(REPLACE(nome_cognome, ' ', '_'), '.', '') AS id,
                'GIUDICE' AS tipo,
                CAST(data_nomina AS VARCHAR) AS data,
                NULL AS numero,
                nome_cognome AS title,
                'Corte Costituzionale' AS collezione,
                NULL AS source_filename,
                'costituzione' AS source,
                NULL AS anno,
                NULL AS length_chars,
                NULL AS length_words,
                NULL AS celex,
                NULL AS vigente
            FROM read_parquet('{GIUDICI}')
            WHERE nome_cognome IS NOT NULL
        """)
        n = con.execute("SELECT COUNT(*) FROM nodes_giudici").fetchone()[0]
        print(f"  giudici:       {n:>6} nodi")
    else:
        con.execute("""CREATE TABLE nodes_giudici (
            id VARCHAR, tipo VARCHAR, data VARCHAR, numero VARCHAR, title VARCHAR,
            collezione VARCHAR, source_filename VARCHAR, source VARCHAR, anno INTEGER,
            length_chars BIGINT, length_words BIGINT, celex VARCHAR, vigente BOOLEAN
        )""")
        print("  giudici:       NON TROVATO")

    # 12. Norme (from massime — target of impugna edges)
    #     Create norma nodes with descriptive titles from massime metadata
    massime_file = WORKSPACE / "costituzione-italiana" / "out" / "data" / "clean" / "massime_corte_costituzionale" / "2026" / "massime_corte_costituzionale_2026_clean.parquet"
    if massime_file.exists():
        con.execute(f"""
            CREATE TABLE nodes_norme AS
            SELECT
                id, tipo, data, numero, title, collezione, source_filename,
                source, anno, length_chars, length_words, celex, vigente
            FROM (
                SELECT
                    'norma:' || LOWER(COALESCE(norma_descrizione, 'legge'))
                           || ':' || CAST(norma_numero AS VARCHAR)
                           || ':' || CAST(YEAR(TRY_CAST(norma_data AS DATE)) AS VARCHAR) AS id,
                    UPPER(COALESCE(norma_descrizione, 'NORMA')) AS tipo,
                    CAST(norma_data AS VARCHAR) AS data,
                    CAST(norma_numero AS VARCHAR) AS numero,
                    COALESCE(norma_descrizione, 'norma') || ' n. ' || norma_numero
                        || ' del ' || CAST(YEAR(TRY_CAST(norma_data AS DATE)) AS VARCHAR) AS title,
                    'Massime Corte' AS collezione,
                    NULL AS source_filename,
                    'costituzione' AS source,
                    YEAR(TRY_CAST(norma_data AS DATE)) AS anno,
                    NULL AS length_chars,
                    NULL AS length_words,
                    NULL AS celex,
                    NULL AS vigente,
                    ROW_NUMBER() OVER (
                        PARTITION BY norma_descrizione, norma_numero, YEAR(TRY_CAST(norma_data AS DATE))
                        ORDER BY norma_data DESC
                    ) AS _rn
                FROM read_parquet('{massime_file}')
                WHERE NULLIF(norma_numero, '') IS NOT NULL
                  AND TRY_CAST(norma_data AS DATE) IS NOT NULL
            ) WHERE _rn = 1
        """)
        n = con.execute("SELECT COUNT(*) FROM nodes_norme").fetchone()[0]
        print(f"  norme:         {n:>6} nodi (from massime)")
    else:
        con.execute("""CREATE TABLE nodes_norme (
            id VARCHAR, tipo VARCHAR, data VARCHAR, numero VARCHAR, title VARCHAR,
            collezione VARCHAR, source_filename VARCHAR, source VARCHAR, anno INTEGER,
            length_chars BIGINT, length_words BIGINT, celex VARCHAR, vigente BOOLEAN
        )""")

    # 13. Promovimento (source of evoca_parametro edges)
    prom_file = WORKSPACE / "costituzione-italiana" / "out" / "data" / "clean" / "atti_promovimento_corte_costituzionale" / "2026" / "atti_promovimento_corte_costituzionale_2026_clean.parquet"
    if prom_file.exists():
        con.execute(f"""
            CREATE TABLE nodes_promovimento AS
            SELECT
                'promovimento:' || CAST(anno AS VARCHAR) || '-' || LPAD(CAST(numero_atto AS VARCHAR), 4, '0') AS id,
                'PROMOVIMENTO' AS tipo,
                CAST(anno AS VARCHAR) AS data,
                CAST(numero_atto AS VARCHAR) AS numero,
                COALESCE(norma_descrizione, '') AS title,
                'Corte Costituzionale' AS collezione,
                NULL AS source_filename,
                'costituzione' AS source,
                anno AS anno,
                NULL AS length_chars,
                NULL AS length_words,
                NULL AS celex,
                NULL AS vigente
            FROM read_parquet('{prom_file}')
            WHERE anno IS NOT NULL AND numero_atto IS NOT NULL
        """)
        n = con.execute("SELECT COUNT(*) FROM nodes_promovimento").fetchone()[0]
        print(f"  promovimento: {n:>6} nodi")
    else:
        con.execute("""CREATE TABLE nodes_promovimento (
            id VARCHAR, tipo VARCHAR, data VARCHAR, numero VARCHAR, title VARCHAR,
            collezione VARCHAR, source_filename VARCHAR, source VARCHAR, anno INTEGER,
            length_chars BIGINT, length_words BIGINT, celex VARCHAR, vigente BOOLEAN
        )""")

    # Union all — deduplicate on id
    con.execute("""
        CREATE TABLE legal_nodes AS
        SELECT * FROM nodes_normativa
        UNION ALL
        SELECT * FROM nodes_articoli
            WHERE id NOT IN (SELECT id FROM nodes_normativa)
        UNION ALL
        SELECT * FROM nodes_revisioni
            WHERE id NOT IN (SELECT id FROM nodes_normativa)
        UNION ALL
        SELECT * FROM nodes_gu
            WHERE id NOT IN (SELECT id FROM nodes_normativa)
        UNION ALL
        SELECT * FROM nodes_senato
            WHERE id NOT IN (SELECT id FROM nodes_normativa)
        UNION ALL
        SELECT * FROM nodes_corpus
            WHERE id NOT IN (SELECT id FROM nodes_normativa)
        UNION ALL
        SELECT * FROM nodes_emend
            WHERE id NOT IN (SELECT id FROM nodes_normativa)
        UNION ALL
        SELECT * FROM nodes_dib
            WHERE id NOT IN (SELECT id FROM nodes_normativa)
        UNION ALL
        SELECT * FROM nodes_senatori
            WHERE id NOT IN (SELECT id FROM nodes_normativa)
        UNION ALL
        SELECT * FROM nodes_eu
            WHERE id NOT IN (SELECT id FROM nodes_normativa)
        UNION ALL
        SELECT * FROM nodes_pronunce
            WHERE id NOT IN (SELECT id FROM nodes_normativa)
        UNION ALL
        SELECT * FROM nodes_giudici
            WHERE id NOT IN (SELECT id FROM nodes_normativa)
        UNION ALL
        SELECT * FROM nodes_norme
            WHERE id NOT IN (SELECT id FROM nodes_normativa)
        UNION ALL
        SELECT * FROM nodes_promovimento
            WHERE id NOT IN (SELECT id FROM nodes_normativa)
    """)

    # Map internal type codes to human-readable names
    con.execute("""
        UPDATE legal_nodes SET tipo = CASE tipo
            WHEN 'emend' THEN 'Emendamento'
            WHEN 'emendc' THEN 'Emendamento con contributo'
            WHEN 'sommcomm' THEN 'Sommario commissione'
            WHEN 'resaula' THEN 'Risoluzione assemblea'
            WHEN 'ordinaria' THEN 'Legge ordinaria'
            WHEN 'ddlpres' THEN 'DDL del Presidente'
            WHEN 'ddldcomm' THEN 'DDL commissione'
            WHEN 'ddlmess' THEN 'DDL messaggio'
            WHEN 'S' THEN 'Sentenza'
            WHEN 'O' THEN 'Ordinanza'
            WHEN 'DIR_DEL' THEN 'Direttiva delegata'
            WHEN 'DIR_IMPL' THEN 'Direttiva di implementazione'
            WHEN 'REG_DEL' THEN 'Regolamento delegato'
            WHEN 'REG_IMPL' THEN 'Regolamento di implementazione'
            WHEN 'SENATORE' THEN 'Senatore'
            WHEN 'GIUDICE' THEN 'Giudice'
            WHEN 'PROMOVIMENTO' THEN 'Atto di promovimento'
            ELSE tipo
        END
    """)

    # Fix types that are dates (should be from DDL with wrong tipo)
    con.execute("""
        UPDATE legal_nodes SET tipo = 'DDL'
        WHERE tipo LIKE '____-__-__' AND source = 'senato'
    """)

    # Fix types that are codes (C.1234, S.1234)
    con.execute("""
        UPDATE legal_nodes SET tipo = 'DDL'
        WHERE (tipo LIKE 'C.%' OR tipo LIKE 'S.%') AND source = 'senato'
    """)

    n = con.execute("SELECT COUNT(*) FROM legal_nodes").fetchone()[0]
    print(f"\n  TOTALE:         {n:>6} nodi unici")


def main() -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTDIR / "legal_nodes.parquet"

    con = duckdb.connect(":memory:")

    print("Building legal nodes...")
    build_nodes(con)

    con.execute(f"COPY legal_nodes TO '{out_file}' (FORMAT PARQUET, COMPRESSION 'zstd')")
    print(f"\nSalvato: {out_file}")

    # Summary
    by_source = con.execute("""
        SELECT source, COUNT(*) as n
        FROM legal_nodes
        GROUP BY source
        ORDER BY n DESC
    """).fetchall()
    print("\nPer sorgente:")
    for source, count in by_source:
        print(f"  {source:15s}: {count:>6}")

    by_tipo = con.execute("""
        SELECT tipo, COUNT(*) as n
        FROM legal_nodes
        GROUP BY tipo
        ORDER BY n DESC
        LIMIT 10
    """).fetchall()
    print("\nPer tipo (top 10):")
    for tipo, count in by_tipo:
        print(f"  {tipo:40s}: {count:>6}")

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
