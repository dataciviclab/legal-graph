-- mart_legal_nodes.sql — nodi unici del Legal Knowledge Graph
--
-- Una riga per nodo (id unico). Colonne allineate allo storico
-- data/legal_nodes.parquet per non rompere MCP/dashboard.
-- Dedup: ROW_NUMBER per sorgente + esclusione id gia' presenti in
-- normativa (backbone).
-- Qualità IC (stato/materia/qualita_score/sunsetting_score): colonne
-- in coda; NULL per sorgenti non-normativa.
-- EIV (akn_act_meta): entrata in vigore effettiva AKN, colonna in coda.
-- vigenza_fonte (normativa clean, IC #74): origine di `stato`
-- (vigenza_snapshot|akn|regex|unknown), in coda; NULL per non-normativa.

WITH akn_meta AS (
    SELECT
        fonte_urn,
        MIN(eiv) AS eiv
    FROM read_parquet('{support.akn_act_meta.path}')
    WHERE NULLIF(fonte_urn, '') IS NOT NULL
      AND eiv IS NOT NULL
    GROUP BY fonte_urn
),

nodes_normativa AS (
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
        NULLIF(codice_redazionale, '') AS codice_redazionale,
        NULLIF(stato, '') AS stato,
        NULLIF(materia, '') AS materia,
        qualita_score,
        sunsetting_score,
        ROW_NUMBER() OVER (PARTITION BY urn ORDER BY filename) AS _rn
    FROM read_parquet('{support.normativa.path}')
    WHERE NULLIF(urn, '') IS NOT NULL
),

-- stato per URN: uno snapshot ORIGINALE non porta mai tombstone (testo
-- originale, non consolidato) e finisce per marcarsi "vigente" anche se
-- l'atto è abrogato. Lo snapshot VIGENZA è il testo consolidato e la sua
-- `stato` è quella affidabile. Preferiamo quindi la riga VIGENZA (a parità,
-- filename) mantenendo gli altri metadati dalla riga dedotta da
-- nodes_normativa. Issue #67 (D.Lgs 50/2016).
-- Le URN con più snapshot VIGENZA hanno stato concorde (verificato).
stato_per_urn AS (
    SELECT
        urn,
        FIRST(stato ORDER BY
            CASE WHEN filename LIKE '%\_VIGENZA\_%' THEN 0 ELSE 1 END,
            filename
        ) AS stato_best,
        FIRST(vigenza_fonte ORDER BY
            CASE WHEN filename LIKE '%\_VIGENZA\_%' THEN 0 ELSE 1 END,
            filename
        ) AS fonte_best
    FROM read_parquet('{support.normativa.path}')
    WHERE NULLIF(urn, '') IS NOT NULL
    GROUP BY urn
),
normativa AS (
    SELECT
        n.id, n.tipo, n.data, n.numero, n.title, n.collezione, n.source_filename,
        n.source, n.anno, n.length_chars, n.length_words, n.celex, n.codice_redazionale,
        s.stato_best AS stato, n.materia, n.qualita_score, n.sunsetting_score
    FROM nodes_normativa n
    LEFT JOIN stato_per_urn s ON n.id = s.urn
    WHERE n._rn = 1
),

nodes_articoli AS (
    SELECT
        'costituzione:art:' || CAST(articolo AS VARCHAR) AS id,
        'COSTITUZIONE' AS tipo,
        NULL::VARCHAR AS data,
        CAST(articolo AS VARCHAR) AS numero,
        heading AS title,
        'Costituzione' AS collezione,
        NULL::VARCHAR AS source_filename,
        'costituzione' AS source,
        NULL::INTEGER AS anno,
        NULL::BIGINT AS length_chars,
        commi AS length_words,
        NULL::VARCHAR AS celex,
        NULL::VARCHAR AS codice_redazionale,
        NULL::VARCHAR AS stato,
        NULL::VARCHAR AS materia,
        NULL::INTEGER AS qualita_score,
        NULL::INTEGER AS sunsetting_score
    FROM read_parquet('{support.articoli_costituzione.path}')
    WHERE articolo IS NOT NULL
),

nodes_revisioni AS (
    SELECT
        'revisione:' || urn AS id,
        'LEGGE COSTITUZIONALE' AS tipo,
        CAST(data AS VARCHAR) AS data,
        NULL::VARCHAR AS numero,
        titolo AS title,
        'Leggi costituzionali' AS collezione,
        NULL::VARCHAR AS source_filename,
        'revisioni' AS source,
        YEAR(CAST(data AS DATE)) AS anno,
        NULL::BIGINT AS length_chars,
        n_articoli AS length_words,
        NULL::VARCHAR AS celex,
        NULLIF(codice_redazionale, '') AS codice_redazionale,
        NULL::VARCHAR AS stato,
        NULL::VARCHAR AS materia,
        NULL::INTEGER AS qualita_score,
        NULL::INTEGER AS sunsetting_score
    FROM read_parquet('{support.revisioni_costituzionali.path}')
),

nodes_gu AS (
    SELECT
        COALESCE(NULLIF(urn_normattiva, ''), 'gu:' || id) AS id,
        tipo_atto AS tipo,
        data_pubblicazione AS data,
        NULL::VARCHAR AS numero,
        titolo AS title,
        serie AS collezione,
        id AS source_filename,
        'gu' AS source,
        YEAR(CAST(data_pubblicazione AS DATE)) AS anno,
        NULL::BIGINT AS length_chars,
        NULL::BIGINT AS length_words,
        NULL::VARCHAR AS celex,
        NULL::VARCHAR AS codice_redazionale,
        NULL::VARCHAR AS stato,
        NULL::VARCHAR AS materia,
        NULL::INTEGER AS qualita_score,
        NULL::INTEGER AS sunsetting_score
    FROM read_parquet('{support.gu_acts.path}')
    WHERE NULLIF(urn_normattiva, '') IS NOT NULL
),

nodes_senato AS (
    SELECT
        id, tipo, data, numero, title, collezione, source_filename,
        source, anno, length_chars, length_words, celex, codice_redazionale,
        stato, materia, qualita_score, sunsetting_score
    FROM (
        SELECT
            'senato:' || CAST(id_ddl AS VARCHAR) AS id,
            COALESCE(natura, 'legge') AS tipo,
            CAST(data_legge AS VARCHAR) AS data,
            CAST(numero_legge AS VARCHAR) AS numero,
            COALESCE(titolo_breve, titolo) AS title,
            'Senato DDL' AS collezione,
            ddl_url AS source_filename,
            'senato' AS source,
            YEAR(data_presentazione) AS anno,
            NULL::BIGINT AS length_chars,
            NULL::BIGINT AS length_words,
            NULL::VARCHAR AS celex,
            NULL::VARCHAR AS codice_redazionale,
            NULL::VARCHAR AS stato,
            NULL::VARCHAR AS materia,
            NULL::INTEGER AS qualita_score,
            NULL::INTEGER AS sunsetting_score,
            ROW_NUMBER() OVER (PARTITION BY id_ddl ORDER BY id_ddl) AS _rn
        FROM read_parquet({support.senato_ddl.outputs}, union_by_name = true)
        WHERE id_ddl IS NOT NULL
    ) WHERE _rn = 1
),

nodes_camera_ddl AS (
    SELECT
        id, tipo, data, numero, title, collezione, source_filename,
        source, anno, length_chars, length_words, celex, codice_redazionale,
        stato, materia, qualita_score, sunsetting_score
    FROM (
        SELECT
            'camera:' || CAST(id_ddl AS VARCHAR) AS id,
            COALESCE(tipo, 'DDL') AS tipo,
            CAST(data_presentazione AS VARCHAR) AS data,
            CAST(id_ddl AS VARCHAR) AS numero,
            COALESCE(titolo, '') AS title,
            'Camera DDL' AS collezione,
            atto_camera AS source_filename,
            'camera_ddl' AS source,
            anno AS anno,
            NULL::BIGINT AS length_chars,
            NULL::BIGINT AS length_words,
            NULL::VARCHAR AS celex,
            NULL::VARCHAR AS codice_redazionale,
            NULL::VARCHAR AS stato,
            NULL::VARCHAR AS materia,
            NULL::INTEGER AS qualita_score,
            NULL::INTEGER AS sunsetting_score,
            ROW_NUMBER() OVER (
                PARTITION BY id_ddl
                ORDER BY data_presentazione DESC
            ) AS _rn
        FROM read_parquet({support.camera_ddl.outputs}, union_by_name = true)
        WHERE id_ddl IS NOT NULL
    ) WHERE _rn = 1
),

nodes_corpus AS (
    SELECT
        id, tipo, data, numero, title, collezione, source_filename,
        source, anno, length_chars, length_words, celex, codice_redazionale,
        stato, materia, qualita_score, sunsetting_score
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
            NULL::VARCHAR AS celex,
            NULL::VARCHAR AS codice_redazionale,
            NULL::VARCHAR AS stato,
            NULL::VARCHAR AS materia,
            NULL::INTEGER AS qualita_score,
            NULL::INTEGER AS sunsetting_score,
            ROW_NUMBER() OVER (PARTITION BY atto_num ORDER BY work_date) AS _rn
        FROM read_parquet('{support.senato_corpus.path}')
        WHERE atto_num IS NOT NULL
    ) WHERE _rn = 1
),

-- Nodi DDL minimi per atti del corpus Senato assenti da senato_ddl.
-- Evita i target dangling di testo_atto (Fase 0, 2026-10-17).
nodes_corpus_ddl AS (
    SELECT
        id, tipo, data, numero, title, collezione, source_filename,
        source, anno, length_chars, length_words, celex, codice_redazionale,
        stato, materia, qualita_score, sunsetting_score
    FROM (
        SELECT
            'senato:' || CAST(atto_num AS VARCHAR) AS id,
            COALESCE(tipologia, 'DDL del Presidente') AS tipo,
            CAST(work_date AS VARCHAR) AS data,
            FRBRnumber AS numero,
            doc_title AS title,
            'Senato Corpus' AS collezione,
            document_id AS source_filename,
            'senato_corpus' AS source,
            YEAR(work_date) AS anno,
            text_len AS length_chars,
            paragraphs_count AS length_words,
            NULL::VARCHAR AS celex,
            NULL::VARCHAR AS codice_redazionale,
            NULL::VARCHAR AS stato,
            NULL::VARCHAR AS materia,
            NULL::INTEGER AS qualita_score,
            NULL::INTEGER AS sunsetting_score,
            ROW_NUMBER() OVER (PARTITION BY atto_num ORDER BY work_date) AS _rn
        FROM read_parquet('{support.senato_corpus.path}')
        WHERE atto_num IS NOT NULL
          AND ('senato:' || CAST(atto_num AS VARCHAR)) NOT IN (
              SELECT id FROM nodes_senato
          )
    ) WHERE _rn = 1
),

nodes_emend AS (
    SELECT
        id, tipo, data, numero, title, collezione, source_filename,
        source, anno, length_chars, length_words, celex, codice_redazionale,
        stato, materia, qualita_score, sunsetting_score
    FROM (
        SELECT
            'senato:emend:' || CAST(regexp_extract(legislatura, '(\d+)', 1) AS BIGINT) || ':' || emend_id AS id,
            tipologia AS tipo,
            CAST(work_date AS VARCHAR) AS data,
            emend_id AS numero,
            LEFT(text_integrale, 100) AS title,
            'Senato Emendamenti' AS collezione,
            document_id AS source_filename,
            'senato_emend' AS source,
            YEAR(work_date) AS anno,
            text_len AS length_chars,
            NULL::BIGINT AS length_words,
            NULL::VARCHAR AS celex,
            NULL::VARCHAR AS codice_redazionale,
            NULL::VARCHAR AS stato,
            NULL::VARCHAR AS materia,
            NULL::INTEGER AS qualita_score,
            NULL::INTEGER AS sunsetting_score,
            ROW_NUMBER() OVER (
                PARTITION BY CAST(regexp_extract(legislatura, '(\d+)', 1) AS BIGINT), emend_id
                ORDER BY document_id
            ) AS _rn
        FROM read_parquet('{support.senato_emendamenti.path}')
        WHERE emend_id IS NOT NULL AND legislatura IS NOT NULL
    ) WHERE _rn = 1
),

nodes_dib AS (
    SELECT
        id, tipo, data, numero, title, collezione, source_filename,
        source, anno, length_chars, length_words, celex, codice_redazionale,
        stato, materia, qualita_score, sunsetting_score
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
            NULL::BIGINT AS length_words,
            NULL::VARCHAR AS celex,
            NULL::VARCHAR AS codice_redazionale,
            NULL::VARCHAR AS stato,
            NULL::VARCHAR AS materia,
            NULL::INTEGER AS qualita_score,
            NULL::INTEGER AS sunsetting_score,
            ROW_NUMBER() OVER (
                PARTITION BY senatore_id, data_seduta, ordine_intervento
                ORDER BY document_id
            ) AS _rn
        FROM read_parquet('{support.senato_dibattito.path}')
        WHERE senatore_id IS NOT NULL
    ) WHERE _rn = 1
),

nodes_senatori AS (
    SELECT
        id, tipo, data, numero, title, collezione, source_filename,
        source, anno, length_chars, length_words, celex, codice_redazionale,
        stato, materia, qualita_score, sunsetting_score
    FROM (
        SELECT
            'senatore:' || CAST(senatore_id AS VARCHAR) AS id,
            'SENATORE' AS tipo,
            CAST(data_seduta AS VARCHAR) AS data,
            nome_oratore AS numero,
            nome_oratore AS title,
            'Senato Dibattito' AS collezione,
            NULL::VARCHAR AS source_filename,
            'senato_dib' AS source,
            YEAR(data_seduta) AS anno,
            NULL::BIGINT AS length_chars,
            NULL::BIGINT AS length_words,
            NULL::VARCHAR AS celex,
            NULL::VARCHAR AS codice_redazionale,
            NULL::VARCHAR AS stato,
            NULL::VARCHAR AS materia,
            NULL::INTEGER AS qualita_score,
            NULL::INTEGER AS sunsetting_score,
            ROW_NUMBER() OVER (PARTITION BY senatore_id ORDER BY data_seduta) AS _rn
        FROM read_parquet('{support.senato_dibattito.path}')
        WHERE senatore_id IS NOT NULL
    ) WHERE _rn = 1
),

-- Senatori da senato_anagrafica (open-politica Leg 13-19).
-- Copre i gap di senato_dibattito: molti senatori non hanno interventi
-- ma compaiono nel ponte_persona. Issue #24.
-- Dedup: 1 row per senatore_id (può apparire in più legislature),
-- solo ID non già presenti da senato_dibattito.
nodes_senatore_anagrafica AS (
    SELECT
        id, tipo, data, numero, title, collezione, source_filename,
        source, anno, length_chars, length_words, celex, codice_redazionale,
        stato, materia, qualita_score, sunsetting_score
    FROM (
        SELECT
            'senatore:' || CAST(senatore_id AS VARCHAR) AS id,
            'SENATORE' AS tipo,
            NULL::VARCHAR AS data,
            NULL::VARCHAR AS numero,
            COALESCE(
                NULLIF(TRIM(CONCAT(COALESCE(nome, ''), ' ', COALESCE(cognome, ''))), ''),
                'Senatore ' || CAST(senatore_id AS VARCHAR)
            ) AS title,
            'Senato Anagrafica' AS collezione,
            NULL::VARCHAR AS source_filename,
            'senato_anag' AS source,
            legislatura AS anno,
            NULL::BIGINT AS length_chars,
            NULL::BIGINT AS length_words,
            NULL::VARCHAR AS celex,
            NULL::VARCHAR AS codice_redazionale,
            NULL::VARCHAR AS stato,
            NULL::VARCHAR AS materia,
            NULL::INTEGER AS qualita_score,
            NULL::INTEGER AS sunsetting_score
        FROM read_parquet({support.senato_anagrafica.outputs}, union_by_name = true)
        WHERE senatore_id IS NOT NULL
        QUALIFY ROW_NUMBER() OVER (PARTITION BY senatore_id ORDER BY legislatura DESC) = 1
    ) x
    WHERE NOT EXISTS (
        SELECT 1 FROM nodes_senatori d
        WHERE d.id = x.id
    )
),

nodes_deputato AS (
    SELECT
        id, tipo, data, numero, title, collezione, source_filename,
        source, anno, length_chars, length_words, celex, codice_redazionale,
        stato, materia, qualita_score, sunsetting_score
    FROM (
        -- Anagrafica Camera: tutte le legislature (fonte primaria).
        -- legislatura e' stringa ('repubblica_19', 'costituente'): anno =
        -- numero legislatura, coerente con nodes_senatore_anagrafica.
        SELECT
            'deputato:' || CAST(persona_id AS VARCHAR) AS id,
            'DEPUTATO' AS tipo,
            NULL::VARCHAR AS data,
            NULL::VARCHAR AS numero,
            COALESCE(
                NULLIF(TRIM(CONCAT(COALESCE(nome, ''), ' ', COALESCE(cognome, ''))), ''),
                'Deputato ' || CAST(persona_id AS VARCHAR)
            ) AS title,
            'Camera Anagrafica' AS collezione,
            NULL::VARCHAR AS source_filename,
            'camera_deputati_legislature' AS source,
            TRY_CAST(regexp_extract(legislatura, '(\d+)$', 1) AS INTEGER) AS anno,
            NULL::BIGINT AS length_chars,
            NULL::BIGINT AS length_words,
            NULL::VARCHAR AS celex,
            NULL::VARCHAR AS codice_redazionale,
            NULL::VARCHAR AS stato,
            NULL::VARCHAR AS materia,
            NULL::INTEGER AS qualita_score,
            NULL::INTEGER AS sunsetting_score,
            ROW_NUMBER() OVER (
                PARTITION BY persona_id
                ORDER BY TRY_CAST(regexp_extract(legislatura, '(\d+)$', 1) AS INTEGER) DESC NULLS LAST
            ) AS _rn
        FROM read_parquet({support.camera_deputati_legislature.outputs}, union_by_name = true)
        WHERE persona_id IS NOT NULL

        UNION ALL

        -- Firmatari: persone non presenti in anagrafica (es. Renzi).
        SELECT
            'deputato:' || CAST(persona_id AS VARCHAR) AS id,
            'DEPUTATO' AS tipo,
            NULL::VARCHAR AS data,
            NULL::VARCHAR AS numero,
            COALESCE(
                NULLIF(ANY_VALUE(deputato_label), ''),
                'Deputato ' || CAST(persona_id AS VARCHAR)
            ) AS title,
            'Camera DDL' AS collezione,
            NULL::VARCHAR AS source_filename,
            'camera_firmatari' AS source,
            ANY_VALUE(legislatura) AS anno,
            NULL::BIGINT AS length_chars,
            NULL::BIGINT AS length_words,
            NULL::VARCHAR AS celex,
            NULL::VARCHAR AS codice_redazionale,
            NULL::VARCHAR AS stato,
            NULL::VARCHAR AS materia,
            NULL::INTEGER AS qualita_score,
            NULL::INTEGER AS sunsetting_score,
            1 AS _rn
        FROM read_parquet({support.camera_firmatari.outputs}, union_by_name = true)
        WHERE persona_id IS NOT NULL
          AND persona_id NOT IN (
              SELECT persona_id
              FROM read_parquet({support.camera_deputati_legislature.outputs}, union_by_name = true)
              WHERE persona_id IS NOT NULL
          )
        GROUP BY persona_id
    ) x
    WHERE _rn = 1
),

-- Votazioni Senato su oggetto/DDL (open-politica senato_votazioni_oggetto)
nodes_votazione AS (
    SELECT
        'votazione:' || votazione_id AS id,
        'VOTAZIONE' AS tipo,
        NULL::VARCHAR AS data,
        NULL::VARCHAR AS numero,
        'Votazione Senato ' || votazione_id
            || ' — ' || COALESCE(esito, 'esito n/a')
            || ' (L.' || CAST(legislatura AS VARCHAR) || ')' AS title,
        'Senato Votazioni' AS collezione,
        votazione_uri AS source_filename,
        'senato_votazioni_oggetto' AS source,
        legislatura AS anno,
        NULL::BIGINT AS length_chars,
        NULL::BIGINT AS length_words,
        NULL::VARCHAR AS celex,
        NULL::VARCHAR AS codice_redazionale,
        NULL::VARCHAR AS stato,
        NULL::VARCHAR AS materia,
        NULL::INTEGER AS qualita_score,
        NULL::INTEGER AS sunsetting_score
    FROM read_parquet({support.senato_votazioni_oggetto.outputs}, union_by_name = true)
    WHERE NULLIF(votazione_id, '') IS NOT NULL
),

-- Iter revisioni Costituzionali (costituzione-italiana compose #21)
-- Dedup: stessa proposta (camera_o_senato + atto_num) può comparire più
-- volte (più rev_urn / stati); tiene la riga con più informazioni.
nodes_itercost AS (
    SELECT
        id, tipo, data, numero, title, collezione, source_filename,
        source, anno, length_chars, length_words, celex, codice_redazionale,
        stato, materia, qualita_score, sunsetting_score
    FROM (
        SELECT
            'itercost:' || camera_o_senato || ':' || CAST(atto_num AS VARCHAR) AS id,
            'DDL COSTITUZIONALE' AS tipo,
            CAST(data_presentazione AS VARCHAR) AS data,
            CAST(atto_num AS VARCHAR) AS numero,
            LEFT(COALESCE(titolo, 'Proposta revisione Costituzionale'), 200) AS title,
            'Iter Costituzionale' AS collezione,
            atto_uri AS source_filename,
            'iter_costituzionale' AS source,
            legislatura AS anno,
            NULL::BIGINT AS length_chars,
            NULL::BIGINT AS length_words,
            NULL::VARCHAR AS celex,
            NULL::VARCHAR AS codice_redazionale,
            NULL::VARCHAR AS stato,
            NULL::VARCHAR AS materia,
            NULL::INTEGER AS qualita_score,
            NULL::INTEGER AS sunsetting_score,
            ROW_NUMBER() OVER (
                PARTITION BY camera_o_senato, atto_num
                ORDER BY ha_legge DESC, rev_data DESC NULLS LAST,
                         data_presentazione DESC NULLS LAST
            ) AS _rn
        FROM read_parquet({support.iter_costituzionale.outputs}, union_by_name = true)
        WHERE NULLIF(camera_o_senato, '') IS NOT NULL
          AND atto_num IS NOT NULL
    ) WHERE _rn = 1
),

nodes_pronunce AS (
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
        NULL::BIGINT AS length_chars,
        NULL::BIGINT AS length_words,
        NULL::VARCHAR AS celex,
        NULL::VARCHAR AS codice_redazionale,
        NULL::VARCHAR AS stato,
        NULL::VARCHAR AS materia,
        NULL::INTEGER AS qualita_score,
        NULL::INTEGER AS sunsetting_score
    FROM read_parquet('{support.pronunce_corte_costituzionale.path}')
    WHERE anno_pronuncia IS NOT NULL AND numero_pronuncia IS NOT NULL
),

nodes_giudici AS (
    SELECT
        'giudice:' || REPLACE(REPLACE(nome_cognome, ' ', '_'), '.', '') AS id,
        'GIUDICE' AS tipo,
        CAST(data_nomina AS VARCHAR) AS data,
        NULL::VARCHAR AS numero,
        nome_cognome AS title,
        'Corte Costituzionale' AS collezione,
        NULL::VARCHAR AS source_filename,
        'costituzione' AS source,
        NULL::INTEGER AS anno,
        NULL::BIGINT AS length_chars,
        NULL::BIGINT AS length_words,
        NULL::VARCHAR AS celex,
        NULL::VARCHAR AS codice_redazionale,
        NULL::VARCHAR AS stato,
        NULL::VARCHAR AS materia,
        NULL::INTEGER AS qualita_score,
        NULL::INTEGER AS sunsetting_score
    FROM read_parquet('{support.giudici_corte_costituzionale.path}')
    WHERE nome_cognome IS NOT NULL
),

nodes_norme AS (
    SELECT
        id, tipo, data, numero, title, collezione, source_filename,
        source, anno, length_chars, length_words, celex, codice_redazionale,
        stato, materia, qualita_score, sunsetting_score
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
            NULL::VARCHAR AS source_filename,
            'costituzione' AS source,
            YEAR(TRY_CAST(norma_data AS DATE)) AS anno,
            NULL::BIGINT AS length_chars,
            NULL::BIGINT AS length_words,
            NULL::VARCHAR AS celex,
            NULL::VARCHAR AS codice_redazionale,
            NULL::VARCHAR AS stato,
            NULL::VARCHAR AS materia,
            NULL::INTEGER AS qualita_score,
            NULL::INTEGER AS sunsetting_score,
            ROW_NUMBER() OVER (
                PARTITION BY norma_descrizione, norma_numero, YEAR(TRY_CAST(norma_data AS DATE))
                ORDER BY norma_data DESC
            ) AS _rn
        FROM read_parquet('{support.massime_corte_costituzionale.path}')
        WHERE NULLIF(norma_numero, '') IS NOT NULL
          AND TRY_CAST(norma_data AS DATE) IS NOT NULL
    ) WHERE _rn = 1
),

-- Nodi da Normattiva API (dettaglio-atto-urn): leggi ordinarie non in
-- collezioni ZIP, con vigenza reale dall'API. Aggiunto 2026-10.
-- La dedup vs nodes_normativa è gestita dal WHERE esterno nel UNION ALL.
nodes_atti_api AS (
    SELECT
        id, tipo, data, numero, title, collezione, source_filename,
        source, anno, length_chars, length_words, celex, codice_redazionale,
        stato, materia, qualita_score, sunsetting_score
    FROM (
        SELECT
            urn AS id,
            COALESCE(NULLIF(tipo_descrizione, ''), 'LEGGE') AS tipo,
            vigenza_inizio AS data,
            CAST(numero AS VARCHAR) AS numero,
            titolo AS title,
            'Normattiva API' AS collezione,
            urn AS source_filename,
            'normativa' AS source,
            anno AS anno,
            NULL::BIGINT AS length_chars,
            NULL::BIGINT AS length_words,
            NULL::VARCHAR AS celex,
            NULL::VARCHAR AS codice_redazionale,
            -- Vigenza reale dall'API (non tombstone detection)
            stato AS stato,
            NULL::VARCHAR AS materia,
            NULL::INTEGER AS qualita_score,
            NULL::INTEGER AS sunsetting_score
        FROM read_parquet('{support.atti_api.path}')
        WHERE NULLIF(urn, '') IS NOT NULL
          AND numero IS NOT NULL
    )
),

nodes_promovimento AS (
    SELECT
        id, tipo, data, numero, title, collezione, source_filename,
        source, anno, length_chars, length_words, celex, codice_redazionale,
        stato, materia, qualita_score, sunsetting_score
    FROM (
        SELECT
            'promovimento:' || CAST(anno AS VARCHAR) || '-' || LPAD(CAST(numero_atto AS VARCHAR), 4, '0') AS id,
            'PROMOVIMENTO' AS tipo,
            CAST(anno AS VARCHAR) AS data,
            CAST(numero_atto AS VARCHAR) AS numero,
            COALESCE(norma_descrizione, '') AS title,
            'Corte Costituzionale' AS collezione,
            NULL::VARCHAR AS source_filename,
            'costituzione' AS source,
            anno AS anno,
            NULL::BIGINT AS length_chars,
            NULL::BIGINT AS length_words,
            NULL::VARCHAR AS celex,
            NULL::VARCHAR AS codice_redazionale,
            NULL::VARCHAR AS stato,
            NULL::VARCHAR AS materia,
            NULL::INTEGER AS qualita_score,
            NULL::INTEGER AS sunsetting_score,
            ROW_NUMBER() OVER (PARTITION BY anno, numero_atto ORDER BY anno DESC) AS _rn
        FROM read_parquet('{support.atti_promovimento.path}')
        WHERE anno IS NOT NULL AND numero_atto IS NOT NULL
    ) WHERE _rn = 1
),

nodes_pnrr AS (
    -- Missione: tipo='missione' (upstream ha tipo come discriminante;
    -- componente è sempre NOT NULL — il vecchio filtro componente IS NULL
    -- produceva 0 righe, issue #19)
    SELECT DISTINCT
        'pnrr:missione:' || CAST(missione AS VARCHAR) AS id,
        'MISSIONE PNRR' AS tipo,
        NULL::VARCHAR AS data,
        CAST(missione AS VARCHAR) AS numero,
        'Missione ' || CAST(missione AS VARCHAR) || ' PNRR' AS title,
        'PNRR' AS collezione,
        NULL::VARCHAR AS source_filename,
        'pnrr' AS source,
        NULL::INTEGER AS anno,
        NULL::BIGINT AS length_chars,
        NULL::BIGINT AS length_words,
        NULL::VARCHAR AS celex,
        NULL::VARCHAR AS codice_redazionale,
        NULL::VARCHAR AS stato,
        NULL::VARCHAR AS materia,
        NULL::INTEGER AS qualita_score,
        NULL::INTEGER AS sunsetting_score
    FROM read_parquet('{support.pnrr_references.path}')
    WHERE missione IS NOT NULL AND tipo = 'missione'

    UNION

    SELECT DISTINCT
        'pnrr:componente:' || CAST(missione AS VARCHAR) || ':' || CAST(componente AS VARCHAR),
        'COMPONENTE PNRR',
        NULL::VARCHAR,
        CAST(missione AS VARCHAR) || '.' || CAST(componente AS VARCHAR),
        'Missione ' || CAST(missione AS VARCHAR) || ', Componente ' || CAST(componente AS VARCHAR),
        'PNRR',
        NULL::VARCHAR,
        'pnrr',
        NULL::INTEGER,
        NULL::BIGINT,
        NULL::BIGINT,
        NULL::VARCHAR,
        NULL::VARCHAR,
        NULL::VARCHAR,
        NULL::VARCHAR,
        NULL::INTEGER,
        NULL::INTEGER
    FROM read_parquet('{support.pnrr_references.path}')
    WHERE missione IS NOT NULL AND componente IS NOT NULL AND tipo != 'missione'
),

-- Atti Camera da relatori (OP camera_relatori #62): namespace dedicato
-- camera:atto:{leg}_{id} — l'atto Camera NON è un DDL (numerazioni diverse),
-- niente collisione con camera:{ddl}. Titolo assente in fonte: URI in
-- source_filename.
nodes_camera_atti AS (
    SELECT
        'camera:atto:' || c.atto_id_leg AS id,
        'ATTO CAMERA' AS tipo,
        CAST(c.data AS VARCHAR) AS data,
        CAST(c.atto_id AS VARCHAR) AS numero,
        NULL::VARCHAR AS title,
        'Camera atto' AS collezione,
        c.atto_camera AS source_filename,
        'camera_relatori' AS source,
        NULL::INTEGER AS anno,
        NULL::BIGINT AS length_chars,
        NULL::BIGINT AS length_words,
        NULL::VARCHAR AS celex,
        NULL::VARCHAR AS codice_redazionale,
        NULL::VARCHAR AS stato,
        NULL::VARCHAR AS materia,
        NULL::INTEGER AS qualita_score,
        NULL::INTEGER AS sunsetting_score
    FROM (
        SELECT
            atto_id_leg,
            MAX(atto_id) AS atto_id,
            MAX(atto_camera) AS atto_camera,
            MIN(data) AS data
        FROM read_parquet({support.camera_relatori.outputs}, union_by_name = true)
        WHERE NULLIF(atto_id_leg, '') IS NOT NULL
        GROUP BY atto_id_leg
    ) c
),

-- DL emanati (open-politica decreti_legge #63): esito conversione sul
-- edge converte_decreto_legge (stato resta NULL: esito non è vigenza).
nodes_dl AS (
    SELECT
        'dl:' || CAST(dl_anno AS VARCHAR) || '-' || CAST(dl_numero AS VARCHAR) AS id,
        'DECRETO-LEGGE' AS tipo,
        CAST(data_presentazione AS VARCHAR) AS data,
        CAST(dl_numero AS VARCHAR) AS numero,
        titolo AS title,
        'DL conversione' AS collezione,
        NULL::VARCHAR AS source_filename,
        'decreti_legge' AS source,
        dl_anno AS anno,
        NULL::BIGINT AS length_chars,
        NULL::BIGINT AS length_words,
        NULL::VARCHAR AS celex,
        NULL::VARCHAR AS codice_redazionale,
        NULL::VARCHAR AS stato,
        NULL::VARCHAR AS materia,
        NULL::INTEGER AS qualita_score,
        NULL::INTEGER AS sunsetting_score
        FROM read_parquet('{support.decreti_legge.path}')
    WHERE dl_numero IS NOT NULL AND dl_anno IS NOT NULL
),

all_nodes AS (
    SELECT * FROM normativa
    UNION ALL
    SELECT * FROM nodes_articoli WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_revisioni WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_gu WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_senato WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_camera_ddl WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_corpus WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_corpus_ddl WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_emend WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_dib WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_senatori WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_senatore_anagrafica WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_deputato WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_votazione WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_itercost WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_pronunce WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_giudici WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_norme WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_atti_api WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_promovimento WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_pnrr WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_camera_atti WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_dl WHERE id NOT IN (SELECT id FROM normativa)
)

SELECT
    id,
    CASE tipo
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
        WHEN 'DEPUTATO' THEN 'Deputato'
        WHEN 'VOTAZIONE' THEN 'Votazione'
        WHEN 'DDL COSTITUZIONALE' THEN 'DDL Costituzionale'
        WHEN 'GIUDICE' THEN 'Giudice'
        WHEN 'PROMOVIMENTO' THEN 'Atto di promovimento'
        ELSE tipo
    END AS tipo,
    data,
    numero,
    title,
    collezione,
    source_filename,
    source,
    anno,
    length_chars,
    length_words,
    celex,
    codice_redazionale,
    stato,
    materia,
    qualita_score,
    sunsetting_score,
    CAST(a.eiv AS VARCHAR) AS eiv,
    sp.fonte_best AS vigenza_fonte
FROM all_nodes n
LEFT JOIN akn_meta a ON n.id = a.fonte_urn
LEFT JOIN stato_per_urn sp ON n.id = sp.urn
