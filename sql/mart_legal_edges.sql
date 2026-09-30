-- mart_legal_edges.sql — relazioni del Legal Knowledge Graph
--
-- Una riga per (source_id, relation, target_id) deduplicata.
-- Stesso schema dello storico data/legal_edges.parquet.
-- attua_delega: riusa riferimenti + tipi/oggetti da normativa
-- (non dal mart nodi — il compose e' indipendente per tabella).

WITH urn_lookup AS (
    SELECT * FROM (
        SELECT
            filename,
            urn,
            ROW_NUMBER() OVER (PARTITION BY filename ORDER BY urn) AS _rn
        FROM read_parquet('{support.normativa.path}')
        WHERE NULLIF(urn, '') IS NOT NULL
    ) WHERE _rn = 1
),

normativa_types AS (
    SELECT urn, tipo, oggetto
    FROM read_parquet('{support.normativa.path}')
    WHERE NULLIF(urn, '') IS NOT NULL
),

edges_riferimenti AS (
    SELECT
        COALESCE(lu.urn, 'file:' || regexp_extract(r.fonte_filename, '/([^/]+)$', 1)) AS source_id,
        'riferimento' AS relation,
        COALESCE(lu2.urn, 'file:' || r.bersaglio_filename) AS target_id,
        r.peso AS weight,
        r.fonte_anno AS source_year,
        r.bersaglio_anno AS target_year,
        NULL::VARCHAR AS evidence
    FROM read_parquet('{support.riferimenti.path}') r
    LEFT JOIN urn_lookup lu
      ON regexp_extract(r.fonte_filename, '/([^/]+)$', 1) = lu.filename
    LEFT JOIN urn_lookup lu2
      ON r.bersaglio_filename = lu2.filename
    WHERE r.risolto = true
),

edges_citazioni AS (
    SELECT
        COALESCE(lu.urn, 'file:' || regexp_extract(c.fonte_filename, '/([^/]+)$', 1)) AS source_id,
        'cita_costituzione' AS relation,
        'costituzione:art:' || CAST(CAST(c.articolo AS INTEGER) AS VARCHAR) AS target_id,
        1 AS weight,
        c.fonte_anno AS source_year,
        NULL::INTEGER AS target_year,
        LEFT(c.contesto, 200) AS evidence
    FROM read_parquet('{support.citazioni_costituzionali.path}') c
    LEFT JOIN urn_lookup lu
      ON regexp_extract(c.fonte_filename, '/([^/]+)$', 1) = lu.filename
    WHERE c.articolo IS NOT NULL
),

edges_massime AS (
    SELECT
        'sentenza:' || CAST(anno_pronuncia AS VARCHAR) || '-' || LPAD(CAST(numero_pronuncia AS VARCHAR), 4, '0') AS source_id,
        'impugna' AS relation,
        'norma:' || LOWER(COALESCE(norma_descrizione, 'legge'))
               || ':' || CAST(norma_numero AS VARCHAR)
               || ':' || CAST(YEAR(TRY_CAST(norma_data AS DATE)) AS VARCHAR) AS target_id,
        1 AS weight,
        anno_pronuncia AS source_year,
        NULL::INTEGER AS target_year,
        norma_descrizione AS evidence
    FROM read_parquet('{support.massime_corte_costituzionale.path}')
    WHERE esito IN ('illegittimo', 'misto')
      AND NULLIF(norma_numero, '') IS NOT NULL
      AND TRY_CAST(norma_data AS DATE) IS NOT NULL
),

edges_parametri AS (
    SELECT
        'sentenza:' || CAST(anno_pronuncia AS VARCHAR) || '-' || LPAD(CAST(numero_pronuncia AS VARCHAR), 4, '0') AS source_id,
        'invoca_parametro' AS relation,
        'costituzione:art:' || CAST(CAST(parametro_articolo AS INTEGER) AS VARCHAR) AS target_id,
        1 AS weight,
        anno_pronuncia AS source_year,
        NULL::INTEGER AS target_year,
        NULL::VARCHAR AS evidence
    FROM read_parquet('{support.massime_corte_costituzionale.path}')
    WHERE NULLIF(CAST(parametro_articolo AS VARCHAR), '') IS NOT NULL
      AND CAST(parametro_articolo AS VARCHAR) != '0'
),

edges_promovimento AS (
    SELECT
        'promovimento:' || CAST(anno AS VARCHAR) || '-' || LPAD(CAST(numero_atto AS VARCHAR), 4, '0') AS source_id,
        'evoca_parametro' AS relation,
        'costituzione:art:' || CAST(parametro_articolo AS VARCHAR) AS target_id,
        1 AS weight,
        anno AS source_year,
        NULL::INTEGER AS target_year,
        NULL::VARCHAR AS evidence
    FROM read_parquet('{support.atti_promovimento.path}')
),

edges_senato AS (
    SELECT
        'senato:' || CAST(s.id_ddl AS VARCHAR) AS source_id,
        'diventa_legge' AS relation,
        s.urn_normattiva AS target_id,
        1 AS weight,
        YEAR(s.data_presentazione) AS source_year,
        YEAR(s.data_legge) AS target_year,
        s.titolo_breve AS evidence
    FROM read_parquet({support.senato_ddl.outputs}, union_by_name = true) s
    WHERE NULLIF(s.urn_normattiva, '') IS NOT NULL
      AND s.id_ddl IS NOT NULL
),

edges_camera_leggi AS (
    SELECT
        'camera:' || CAST(l.ddl_numero AS VARCHAR) AS source_id,
        'diventa_legge' AS relation,
        l.urn_normattiva AS target_id,
        1 AS weight,
        l.anno AS source_year,
        l.anno AS target_year,
        l.titolo AS evidence
    FROM read_parquet({support.camera_leggi.outputs}, union_by_name = true) l
    WHERE NULLIF(l.urn_normattiva, '') IS NOT NULL
      AND l.ddl_numero IS NOT NULL
),

ddl_lookup AS (
    SELECT * FROM (
        SELECT
            id_ddl,
            'senato:' || CAST(id_ddl AS VARCHAR) AS resolved_id,
            ROW_NUMBER() OVER (PARTITION BY id_ddl ORDER BY legislatura DESC) AS _rn
        FROM read_parquet({support.senato_ddl.outputs}, union_by_name = true)
        WHERE id_ddl IS NOT NULL
    ) WHERE _rn = 1
),

edges_corpus AS (
    SELECT
        'senato:atto:' || CAST(c.atto_num AS VARCHAR) AS source_id,
        'testo_atto' AS relation,
        COALESCE(d.resolved_id, 'senato:' || CAST(c.atto_num AS VARCHAR)) AS target_id,
        1 AS weight,
        YEAR(c.work_date) AS source_year,
        NULL::INTEGER AS target_year,
        c.famiglia AS evidence
    FROM read_parquet('{support.senato_corpus.path}') c
    LEFT JOIN ddl_lookup d ON c.atto_num = d.id_ddl
    WHERE c.atto_num IS NOT NULL
),

edges_emend AS (
    SELECT
        'senato:emend:' || CAST(regexp_extract(e.legislatura, '(\d+)', 1) AS BIGINT) || ':' || e.emend_id AS source_id,
        'emendamento' AS relation,
        COALESCE(ddl.resolved_id, 'senato:' || CAST(d.id_ddl AS VARCHAR)) AS target_id,
        1 AS weight,
        YEAR(e.work_date) AS source_year,
        NULL::INTEGER AS target_year,
        LEFT(e.text_integrale, 100) AS evidence
    FROM read_parquet('{support.senato_emendamenti.path}') e
    JOIN read_parquet({support.senato_ddl.outputs}, union_by_name = true) d
      ON e.fase = d.fase
     AND CAST(regexp_extract(e.legislatura, '(\d+)', 1) AS BIGINT) = d.legislatura
    LEFT JOIN ddl_lookup ddl ON d.id_ddl = ddl.id_ddl
    WHERE e.emend_id IS NOT NULL AND e.fase IS NOT NULL
      AND e.legislatura IS NOT NULL
),

edges_dib AS (
    SELECT
        'senato:dib:' || CAST(senatore_id AS VARCHAR) || ':' || CAST(data_seduta AS VARCHAR) || ':' || CAST(ordine_intervento AS VARCHAR) AS source_id,
        'intervento' AS relation,
        'senatore:' || CAST(senatore_id AS VARCHAR) AS target_id,
        1 AS weight,
        YEAR(data_seduta) AS source_year,
        NULL::INTEGER AS target_year,
        nome_oratore AS evidence
    FROM read_parquet('{support.senato_dibattito.path}')
    WHERE senatore_id IS NOT NULL
),

-- Heuristica documentata: D.Lgs che referenzia leggi con "Delega al Governo"
-- nel titolo con peso >= 5. Stessa logica dello storico build_legal_edges.py.
edges_delega AS (
    SELECT
        e.source_id,
        'attua_delega' AS relation,
        e.target_id,
        e.weight,
        e.source_year,
        e.target_year,
        n2.oggetto AS evidence
    FROM edges_riferimenti e
    JOIN normativa_types n1 ON e.source_id = n1.urn
    JOIN normativa_types n2 ON e.target_id = n2.urn
    WHERE n1.tipo = 'DECRETO LEGISLATIVO'
      AND n2.tipo = 'LEGGE'
      AND LOWER(n2.oggetto) LIKE '%delega al governo%'
      AND e.weight >= 5
),

edges_pnrr AS (
    SELECT
        lu.urn AS source_id,
        'referenzia_pnrr' AS relation,
        'pnrr:missione:' || CAST(r.missione AS VARCHAR) AS target_id,
        1 AS weight,
        r.missione AS source_year,
        NULL::INTEGER AS target_year,
        'M' || CAST(r.missione AS VARCHAR) || 'C' || CAST(r.componente AS VARCHAR)
            || CASE WHEN r.investimento IS NOT NULL THEN '-I' || r.investimento ELSE '' END AS evidence
    FROM read_parquet('{support.pnrr_references.path}') r
    JOIN (
        SELECT filename, urn FROM urn_lookup
    ) lu ON r.filename = lu.filename
    WHERE r.missione IS NOT NULL
),

abro_source AS (
    SELECT
        lu.urn AS source_id,
        a.abrogated_year,
        a.abrogated_number,
        a.context
    FROM read_parquet('{support.abrogations_raw.path}') a
    JOIN urn_lookup lu ON a.abrogating_file = lu.filename
),

edges_abro AS (
    SELECT
        s.source_id,
        'abroga' AS relation,
        n.urn AS target_id,
        1 AS weight,
        s.abrogated_year AS source_year,
        NULL::INTEGER AS target_year,
        LEFT(s.context, 200) AS evidence
    FROM abro_source s
    JOIN (
        SELECT urn, anno_atto, numero
        FROM read_parquet('{support.normativa.path}')
        WHERE NULLIF(urn, '') IS NOT NULL
    ) n ON s.abrogated_year = n.anno_atto
       AND s.abrogated_number = TRY_CAST(n.numero AS INTEGER)
)

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
    SELECT * FROM edges_camera_leggi
    UNION ALL
    SELECT * FROM edges_delega
    UNION ALL
    SELECT * FROM edges_corpus
    UNION ALL
    SELECT * FROM edges_emend
    UNION ALL
    SELECT * FROM edges_dib
    UNION ALL
    SELECT * FROM edges_pnrr
    UNION ALL
    SELECT * FROM edges_abro
)
GROUP BY source_id, relation, target_id
