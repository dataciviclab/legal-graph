-- mart_legal_nodes.sql — nodi unici del Legal Knowledge Graph
--
-- Una riga per nodo (id unico). Schema canonico del mart: il layer legacy
-- (`data/*.parquet`) e la dashboard sono stati rimossi, qui non ci sono più
-- fallback.
-- Dedup: `is_current` per normativa (identità dell'atto in italia-corpus:
-- una sola espressione per URN) + ROW_NUMBER per le altre sorgenti +
-- esclusione id gia' presenti in normativa (backbone).
-- Qualità IC (stato/materia/qualita_score/sunsetting_score): colonne
-- in coda; NULL per sorgenti non-normativa.
-- EIV (akn_act_meta): entrata in vigore effettiva AKN, colonna in coda.
-- vigenza_fonte (normativa clean, IC): origine di `stato`
-- (normattiva|non_coperto|esclusione), in coda; `atti_api` per gli atti fuori
-- collezione IC (fetch dettaglio-atto-urn); NULL per non-normativa.

WITH massime_norme_dated AS (
    -- `norma_data` arriva dai mart di costituzione-italiana come stringa
    -- dd/mm/yyyy (#42); il vecchio clean la dava DATE. Accettiamo entrambi
    -- qui, in un solo punto: join, filtri e partizioni usano `norma_data_date`.
    SELECT *,
        (CASE WHEN regexp_matches(norma_data, '^\d{1,2}/\d{1,2}/\d{4}$')
              THEN TRY_CAST(strptime(norma_data, '%d/%m/%Y') AS DATE)
              ELSE TRY_CAST(norma_data AS DATE) END) AS norma_data_date
    FROM read_parquet('{support.massime_norme.path}')
),
akn_meta AS (
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
        sunsetting_score
    FROM read_parquet('{support.normativa.path}')
    WHERE NULLIF(urn, '') IS NOT NULL
      -- una sola espressione corrente per URN (italia-corpus #88): è già la
      -- VIGENZA più recente quando esiste, altrimenti l'ORIGINALE
      AND is_current
),

-- vigenza_fonte dell'espressione corrente, per URN.
--
-- Il vecchio workaround `stato_per_urn` (FIRST + CASE su '_VIGENZA_') nasceva
-- dal presupposto che `stato` si leggesse dal testo: uno snapshot ORIGINALE non
-- porta tombstone e si sarebbe marcato "vigente" (issue #67).
--
-- Oggi non è più così: `stato` arriva da `status.parquet` via `integra_vigenza`,
-- è **atto-level** (join su codice_redazionale + data GU) e viene scritto
-- **identico su tutte le espressioni**. Per questo `stato`/`vigenza_fonte` non
-- possono cambiare qualunque riga si scelga: **il delta 0 è strutturale, non
-- fortuito**. `is_current` seleziona l'espressione corrente; qui si aggancia
-- solo `vigenza_fonte` (che `all_nodes` non porta).
--
-- `GROUP BY urn`: robustezza se un URN avesse due righe `is_current` (bug di
-- dati): la LEFT JOIN non deve duplicare il nodo (il test lo intercetta, ma
-- meglio non dipenderne).
normativa_fonte AS (
    SELECT
        urn,
        MAX(NULLIF(vigenza_fonte, '')) AS vigenza_fonte
    FROM read_parquet('{support.normativa.path}')
    WHERE NULLIF(urn, '') IS NOT NULL
      AND is_current
    GROUP BY urn
),
normativa AS (
    SELECT
        n.id, n.tipo, n.data, n.numero, n.title, n.collezione, n.source_filename,
        n.source, n.anno, n.length_chars, n.length_words, n.celex, n.codice_redazionale,
        n.stato, n.materia, n.qualita_score, n.sunsetting_score
    FROM nodes_normativa n
),

-- Nodi "catalogo": atti del catalogo Normattiva (`atti_index`) che **non**
-- sono nel corpus ma sono citati da un atto che lo è. Senza questi nodi le
-- citation fuori corpus restano dangling e il compose le scarta. Solo i
-- citati: l'intero catalogo sarebbe 205k nodi senza testo né relazioni. Le
-- direttive UE hanno un nodo dedicato da `direttive_ue` (sotto), non dal
-- catalogo.
--
-- La lista dei citati viene dal **mart** URN (stessa sorgente di
-- `edges_riferimenti`), non dal clean: clean e mart possono divergere (il
-- clean porta citazioni che il mart filtra) e i nodi creati dal clean
-- restavano senza edge. La guardia temporale è replicata qui perché un nodo
-- catalogo senza edge non serve a nessun consumer.
cited AS (
    SELECT DISTINCT
        regexp_replace(
            regexp_replace(
                regexp_extract(r.bersaglio_urn, '^urn:nir:[^:]+:([^:]+):', 1),
                '[.\-]', '', 'g'
            ),
            'della|delle|dello|degli|dei|del', '', 'g'
        ) AS tipo_slug,
        regexp_extract(r.bersaglio_urn, ':(\d{4})(?:-\d{2}-\d{2})?;(\d+)$', 1) AS anno,
        regexp_extract(r.bersaglio_urn, ':(\d{4})(?:-\d{2}-\d{2})?;(\d+)$', 2) AS numero
    FROM read_parquet('{support.riferimenti_urn.path}') r
    WHERE r.rel_type = 'citation'
      AND r.bersaglio_urn LIKE 'urn:nir:%'
      -- stessa guardia di edges_riferimenti
      AND (
          r.fonte_anno IS NULL
          OR r.bersaglio_anno IS NULL
          OR r.fonte_anno >= r.bersaglio_anno - 1
          OR (r.da_regex AND r.peso < 5)
      )
),

nodes_catalogo AS (
    SELECT
        id, tipo, data, numero, title, collezione, source_filename,
        source, anno, length_chars, length_words, celex, codice_redazionale,
        stato, materia, qualita_score, sunsetting_score
    FROM (
        SELECT
            i.urn AS id,
            i.denominazione AS tipo,
            regexp_extract(i.urn, ':(\d{4}-\d{2}-\d{2});', 1) AS data,
            CAST(i.numero AS VARCHAR) AS numero,
            COALESCE(NULLIF(i.titolo, ''), i.descrizione) AS title,
            'Catalogo Normattiva' AS collezione,
            -- source dedicato: sono nodi di **identità** senza testo, qualità o
            -- materia. Tenerli fuori da `source='normativa'` preserva gli
            -- invarianti dei nodi di corpus (materia/vigenza_fonte/cardinalità).
            'catalogo' AS source,
            -- source_filename = URN: il testo non è nel corpus (legal_text
            -- restituisce il link Normattiva invece di un 404 interno).
            i.urn AS source_filename,
            i.anno AS anno,
            NULL::BIGINT AS length_chars,
            NULL::BIGINT AS length_words,
            NULL::VARCHAR AS celex,
            NULLIF(i.codice_redazionale, '') AS codice_redazionale,
            CASE
                WHEN CAST(i.classe AS VARCHAR) IN ('1', '2') THEN 'vigente'
                ELSE 'abrogato'
            END AS stato,
            -- materia dal catalogo (classificatore IC): senza, i nodi catalogo
            -- sarebbero invisibili a `legal_search(materia=...)`.
            NULLIF(i.materia, '') AS materia,
            NULL::INTEGER AS qualita_score,
            NULL::INTEGER AS sunsetting_score,
            ROW_NUMBER() OVER (PARTITION BY i.urn ORDER BY i.data_gu) AS _rn
        FROM read_parquet('{support.atti_index.path}') i
        JOIN cited c
          ON  c.anno = CAST(i.anno AS VARCHAR)
          AND c.numero = CAST(i.numero AS VARCHAR)
          -- stessa normalizzazione del tipo usata in mart_legal_edges.sql
          AND c.tipo_slug = regexp_replace(
                  regexp_replace(lower(i.denominazione), '[^a-z]', '', 'g'),
                  'della|delle|dello|degli|dei|del', '', 'g'
              )
        WHERE NULLIF(i.urn, '') IS NOT NULL
          AND NOT i.in_corpus
    ) WHERE _rn = 1
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

-- Nodo dell'atto **Costituzione nel suo insieme** (`costituzione`): serve alle
-- 4.971 citation che puntano a `urn:nir:stato:costituzione:1947-12-27;const`,
-- cioè alla Costituzione senza articolo. Gli articoli restano
-- `costituzione:art:N`: l'atto intero non è un articolo e non va mappato su uno.
nodes_costituzione AS (
    SELECT
        'costituzione:atto' AS id,
        'COSTITUZIONE' AS tipo,
        '1947-12-27' AS data,
        NULL::VARCHAR AS numero,
        'Costituzione della Repubblica italiana' AS title,
        'Costituzione' AS collezione,
        NULL::VARCHAR AS source_filename,
        'costituzione' AS source,
        1947 AS anno,
        NULL::BIGINT AS length_chars,
        NULL::BIGINT AS length_words,
        NULL::VARCHAR AS celex,
        NULL::VARCHAR AS codice_redazionale,
        'vigente' AS stato,
        NULL::VARCHAR AS materia,
        NULL::INTEGER AS qualita_score,
        NULL::INTEGER AS sunsetting_score
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
        atto_id                            AS id,
        tipo,
        CAST(data AS VARCHAR)              AS data,
        CAST(numero_legge AS VARCHAR)      AS numero,
        COALESCE(titolo_breve, titolo)     AS title,
        'Senato DDL'                       AS collezione,
        atto_uri                           AS source_filename,
        'senato'                           AS source,
        anno,
        NULL::BIGINT  AS length_chars,
        NULL::BIGINT  AS length_words,
        NULL::VARCHAR AS celex,
        NULL::VARCHAR AS codice_redazionale,
        NULL::VARCHAR AS stato,
        NULL::VARCHAR AS materia,
        NULL::INTEGER AS qualita_score,
        NULL::INTEGER AS sunsetting_score
    FROM read_parquet('{support.atti.path}')
    WHERE ramo = 'senato'
),

nodes_camera_ddl AS (
    SELECT
        atto_id                            AS id,
        COALESCE(tipo, 'DDL')              AS tipo,
        CAST(data AS VARCHAR)              AS data,
        CAST(numero_atto AS VARCHAR)       AS numero,
        COALESCE(titolo, '')               AS title,
        'Camera DDL'                       AS collezione,
        atto_uri                           AS source_filename,
        'camera_ddl'                       AS source,
        anno,
        NULL::BIGINT  AS length_chars,
        NULL::BIGINT  AS length_words,
        NULL::VARCHAR AS celex,
        NULL::VARCHAR AS codice_redazionale,
        NULL::VARCHAR AS stato,
        NULL::VARCHAR AS materia,
        NULL::INTEGER AS qualita_score,
        NULL::INTEGER AS sunsetting_score
    FROM read_parquet('{support.atti.path}')
    WHERE ramo = 'camera'
),

-- Documento AKN del corpus Senato. Esiste come nodo separato (`senato:atto:X`)
-- solo quando c'è un atto canonico `senato:X` da open-politica: il documento
-- è il testo, l'atto è il DDL. Quando il corpus è l'unica fonte, il nodo
-- canonico è `senato:X` (nodes_corpus_ddl) e creare anche `senato:atto:X`
-- duplicava lo stesso atto in search (2.990 coppie nel mart 2026-10).
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
          AND ('senato:' || CAST(atto_num AS VARCHAR)) IN (
              SELECT id FROM nodes_senato
          )
    ) WHERE _rn = 1
),

-- Nodi DDL canonici per atti del corpus Senato assenti da open-politica.
-- Portano gli attributi del corpus (testo, lunghezza) perché in questo caso
-- il documento È l'atto: un solo nodo per lo stesso oggetto.
-- Evita anche i target dangling di testo_atto (Fase 0, 2026-10-17).
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
            -- identità: (legislatura, fase, numero). `emend_id` è il numero
            -- dentro l'atto ("1.1" esiste in centinaia di atti): senza la fase
            -- il nodo aggregava emendamenti diversi dello stesso numero.
            'senato:emend:' || CAST(regexp_extract(legislatura, '(\d+)', 1) AS BIGINT)
                || ':' || fase || ':' || emend_id AS id,
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
                PARTITION BY CAST(regexp_extract(legislatura, '(\d+)', 1) AS BIGINT), fase, emend_id
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

-- Seduta e nome d'aula del senatore (prima seduta): conserva `data`,
-- `numero` e la label `senato_dib` che il nodo aveva prima della migrazione.
dibattito AS (
    SELECT
        senatore_id, data_seduta, nome_oratore,
        ROW_NUMBER() OVER (PARTITION BY senatore_id ORDER BY data_seduta) AS _rn
    FROM read_parquet('{support.senato_dibattito.path}')
    WHERE senatore_id IS NOT NULL
),

-- Bersagli di `collega`: il ponte Camera↔Senato usa `deputato:{persona_id}`
-- anche per persone fuori dal perimetro 13-19 (Regno, Repubblica 1-12).
ponte_persona AS (
    SELECT DISTINCT CAST(persona_id AS BIGINT) AS persona_id
    FROM read_parquet('{support.ponte_persona.path}')
    WHERE persona_id IS NOT NULL
),

-- Deputati fuori perimetro ma referenziati dal ponte: senza il nodo l'edge
-- `collega` sparisce (ne mancavano 101).
ponte_dep AS (
    SELECT c.persona_id, c.nome, c.cognome
    FROM (
        SELECT
            c.*,
            ROW_NUMBER() OVER (PARTITION BY c.persona_id ORDER BY c.legislatura DESC) AS _rn
        FROM read_parquet({support.camera_deputati_legislature.outputs}, union_by_name = true) c
        WHERE c.persona_id IN (SELECT persona_id FROM ponte_persona)
          AND c.persona_id NOT IN (
              SELECT persona_id FROM read_parquet('{support.persone.path}')
              WHERE ramo = 'camera' AND persona_id IS NOT NULL
          )
    ) c
    WHERE c._rn = 1
),

-- Persona (open-politica `persone`): un nodo per persona, non per
-- legislatura. Gli id restano `senatore:{senatore_id}` e
-- `deputato:{persona_id}`: gli archi `firmatario`, `relatore`, `vota`,
-- `collega` continuano a puntare lì.
--
-- `anno` è il numero di legislatura (prima, per i senatori visti in aula,
-- era l'anno della prima seduta: stessa colonna con due semantiche).
nodes_persone AS (
    SELECT
        id, tipo, data, numero, title, collezione, source_filename,
        source, anno, length_chars, length_words, celex, codice_redazionale,
        stato, materia, qualita_score, sunsetting_score
    FROM (
        SELECT
            CASE p.ramo
                WHEN 'senato' THEN 'senatore:' || CAST(p.senatore_id AS VARCHAR)
                ELSE 'deputato:' || CAST(p.persona_id AS VARCHAR)
            END AS id,
            CASE p.ramo WHEN 'senato' THEN 'SENATORE' ELSE 'DEPUTATO' END AS tipo,
            CAST(d.data_seduta AS VARCHAR) AS data,
            d.nome_oratore AS numero,
            COALESCE(
                NULLIF(p.nome_completo, ''),
                CASE p.ramo WHEN 'senato' THEN 'Senatore ' ELSE 'Deputato ' END
                    || CAST(COALESCE(p.senatore_id, p.persona_id) AS VARCHAR)
            ) AS title,
            CASE
                WHEN d.senatore_id IS NOT NULL           THEN 'Senato Dibattito'
                WHEN p.fonte = 'senato_anagrafica'       THEN 'Senato Anagrafica'
                WHEN p.fonte = 'camera_firmatari'        THEN 'Camera DDL'
                ELSE 'Camera Anagrafica'
            END AS collezione,
            CAST(NULL AS VARCHAR) AS source_filename,
            CASE
                WHEN d.senatore_id IS NOT NULL           THEN 'senato_dib'
                WHEN p.fonte = 'senato_anagrafica'       THEN 'senato_anag'
                WHEN p.fonte = 'camera_firmatari'        THEN 'camera_firmatari'
                ELSE 'camera_deputati_legislature'
            END AS source,
            p.legislatura AS anno,
            CAST(NULL AS BIGINT)  AS length_chars,
            CAST(NULL AS BIGINT)  AS length_words,
            CAST(NULL AS VARCHAR) AS celex,
            CAST(NULL AS VARCHAR) AS codice_redazionale,
            CAST(NULL AS VARCHAR) AS stato,
            CAST(NULL AS VARCHAR) AS materia,
            CAST(NULL AS INTEGER) AS qualita_score,
            CAST(NULL AS INTEGER) AS sunsetting_score,
            1 AS _ord
        FROM read_parquet('{support.persone.path}') p
        LEFT JOIN (SELECT * FROM dibattito WHERE _rn = 1) d
               ON p.ramo = 'senato' AND d.senatore_id = p.senatore_id
        WHERE p.legislatura IS NOT NULL
          AND (p.senatore_id IS NOT NULL OR p.persona_id IS NOT NULL)

        UNION ALL

        SELECT
            'deputato:' || CAST(c.persona_id AS VARCHAR) AS id,
            'DEPUTATO' AS tipo,
            CAST(NULL AS VARCHAR) AS data,
            CAST(NULL AS VARCHAR) AS numero,
            COALESCE(
                NULLIF(TRIM(CONCAT(COALESCE(c.nome, ''), ' ', COALESCE(c.cognome, ''))), ''),
                'Deputato ' || CAST(c.persona_id AS VARCHAR)
            ) AS title,
            'Camera Anagrafica' AS collezione,
            CAST(NULL AS VARCHAR) AS source_filename,
            'camera_deputati_legislature' AS source,
            CAST(NULL AS INTEGER) AS anno,
            CAST(NULL AS BIGINT)  AS length_chars,
            CAST(NULL AS BIGINT)  AS length_words,
            CAST(NULL AS VARCHAR) AS celex,
            CAST(NULL AS VARCHAR) AS codice_redazionale,
            CAST(NULL AS VARCHAR) AS stato,
            CAST(NULL AS VARCHAR) AS materia,
            CAST(NULL AS INTEGER) AS qualita_score,
            CAST(NULL AS INTEGER) AS sunsetting_score,
            2 AS _ord
        FROM ponte_dep c
    ) q
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY id ORDER BY _ord, anno DESC NULLS LAST
    ) = 1
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
                   || ':' || CAST(YEAR(norma_data_date) AS VARCHAR) AS id,
            UPPER(COALESCE(norma_descrizione, 'NORMA')) AS tipo,
            CAST(norma_data_date AS VARCHAR) AS data,
            CAST(norma_numero AS VARCHAR) AS numero,
            COALESCE(norma_descrizione, 'norma') || ' n. ' || norma_numero
                || ' del ' || CAST(YEAR(norma_data_date) AS VARCHAR) AS title,
            'Massime Corte' AS collezione,
            NULL::VARCHAR AS source_filename,
            'costituzione' AS source,
            YEAR(norma_data_date) AS anno,
            NULL::BIGINT AS length_chars,
            NULL::BIGINT AS length_words,
            NULL::VARCHAR AS celex,
            NULL::VARCHAR AS codice_redazionale,
            NULL::VARCHAR AS stato,
            NULL::VARCHAR AS materia,
            NULL::INTEGER AS qualita_score,
            NULL::INTEGER AS sunsetting_score,
            ROW_NUMBER() OVER (
                PARTITION BY norma_descrizione, norma_numero, YEAR(norma_data_date)
                ORDER BY norma_data DESC
            ) AS _rn
        FROM massime_norme_dated
        WHERE NULLIF(norma_numero, '') IS NOT NULL
          AND norma_data_date IS NOT NULL
          -- Solo le norme che diventano target di un edge `impugna`
          -- (esito illegittimo/misto, come in mart_legal_edges.sql): prima
          -- ogni norma citata da una massima creava un nodo, e 5.000
          -- restavano senza nessuna relazione.
          AND esito IN ('illegittimo', 'misto')
          -- Se l'atto ha già un nodo URN nel corpus, il fallback non serve:
          -- l'edge punta all'URN. Replica il lookup di `norma_urn_lookup`
          -- (edges): il test sugli orfani intercetta un eventuale drift.
          AND NOT EXISTS (
              -- normativa (collezioni IC) + atti_api (fuori collezione):
              -- sono entrambe fonti di nodi URN, e `norma_urn_lookup` in
              -- mart_legal_edges.sql cerca su mart_legal_nodes (entrambe).
              -- tutte le fonti di nodi URN del grafo (normativa, atti_api, gu):
              -- è lo stesso insieme su cui cerca `norma_urn_lookup` in
              -- mart_legal_edges.sql, che parsa numero/anno dall'id URN.
              SELECT 1 FROM (
                  SELECT urn AS id FROM read_parquet('{support.normativa.path}') WHERE is_current AND NULLIF(urn, '') IS NOT NULL
                  UNION ALL SELECT urn AS id FROM read_parquet('{support.atti_api.path}') WHERE NULLIF(urn, '') IS NOT NULL
                  UNION ALL SELECT COALESCE(NULLIF(urn_normattiva, ''), 'gu:' || id) AS id FROM read_parquet('{support.gu_acts.path}')
                  UNION ALL SELECT id FROM nodes_catalogo
              ) n
              WHERE n.id LIKE 'urn:nir:stato:%'
                AND TRY_CAST(regexp_extract(
                        n.id, 'urn:nir:stato:[^:]+:([0-9]{4})-[0-9]{2}-[0-9]{2};[0-9]+$', 1
                    ) AS INTEGER) = YEAR(norma_data_date)
                AND TRY_CAST(regexp_extract(
                        n.id, 'urn:nir:stato:[^:]+:[0-9]{4}-[0-9]{2}-[0-9]{2};([0-9]+)$', 1
                    ) AS BIGINT) = TRY_CAST(norma_numero AS BIGINT)
                -- stesso `urn_tipo` di `norma_urn_lookup` in mart_legal_edges.sql
                AND regexp_extract(
                        n.id,
                        'urn:nir:stato:([^:]+):[0-9]{4}-[0-9]{2}-[0-9]{2};[0-9]+$',
                        1
                    ) = CASE norma_codice
                        WHEN 'l' THEN 'legge'
                        WHEN 'dl' THEN 'decreto.legge'
                        WHEN 'dlgs' THEN 'decreto.legislativo'
                        WHEN 'dpr' THEN 'decreto.del.presidente.della.repubblica'
                        WHEN 'rd' THEN 'regio.decreto'
                        WHEN 'rdl' THEN 'regio.decreto.legge'
                    END
          )
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
            -- materia: stesso classificatore del corpus (italia-corpus
            -- fetch_urn_acts); senza, questi atti sono invisibili ai filtri
            NULLIF(materia, '') AS materia,
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

-- DL emanati (open-politica `atti`): nodo dl:* per i DL senza URN nel
-- corpus; l'esito di conversione sta sull'edge `converte_decreto_legge`
-- (stato resta NULL: esito non è vigenza).
nodes_dl AS (
    SELECT
        'dl:' || CAST(d.dl_anno AS VARCHAR) || '-' || CAST(d.dl_numero AS VARCHAR) AS id,
        'DECRETO-LEGGE' AS tipo,
        CAST(d.data_dl AS VARCHAR) AS data,
        CAST(d.dl_numero AS VARCHAR) AS numero,
        d.titolo AS title,
        'DL conversione' AS collezione,
        NULL::VARCHAR AS source_filename,
        'decreti_legge' AS source,
        d.dl_anno AS anno,
        NULL::BIGINT AS length_chars,
        NULL::BIGINT AS length_words,
        NULL::VARCHAR AS celex,
        NULL::VARCHAR AS codice_redazionale,
        NULL::VARCHAR AS stato,
        NULL::VARCHAR AS materia,
        NULL::INTEGER AS qualita_score,
        NULL::INTEGER AS sunsetting_score
        FROM read_parquet('{support.dl.path}') d
    WHERE d.dl_numero IS NOT NULL AND d.dl_anno IS NOT NULL
      -- Se l'atto esiste già come URN nel corpus, il nodo canonico è quello:
      -- `dl:*` sarebbe un duplicato (213 casi nel mart 2026-10).
      AND NOT EXISTS (
          -- stesso insieme e stesso parsing di `conversione_dl`
          -- (mart_legal_edges.sql): normativa + atti_api + gu.
          SELECT 1 FROM (
              SELECT urn AS id FROM read_parquet('{support.normativa.path}') WHERE is_current AND NULLIF(urn, '') IS NOT NULL
              UNION ALL SELECT urn AS id FROM read_parquet('{support.atti_api.path}') WHERE NULLIF(urn, '') IS NOT NULL
              UNION ALL SELECT COALESCE(NULLIF(urn_normattiva, ''), 'gu:' || id) AS id FROM read_parquet('{support.gu_acts.path}')
              UNION ALL SELECT id FROM nodes_catalogo
          ) n
          WHERE n.id LIKE 'urn:nir:stato:decreto.legge:%'
            AND TRY_CAST(regexp_extract(
                    n.id, 'urn:nir:stato:[^:]+:([0-9]{4})-[0-9]{2}-[0-9]{2};[0-9]+$', 1
                ) AS INTEGER) = d.dl_anno
            AND TRY_CAST(regexp_extract(
                    n.id, 'urn:nir:stato:[^:]+:[0-9]{4}-[0-9]{2}-[0-9]{2};([0-9]+)$', 1
                ) AS BIGINT) = d.dl_numero
      )
),

nodes_ue AS (
    SELECT
        'celex:' || d.celex AS id,
        'DIRETTIVA' AS tipo,
        CAST(d.data AS VARCHAR) AS data,
        CAST(TRY_CAST(regexp_extract(d.celex, 'L(\d{4})$', 1) AS INTEGER) AS VARCHAR) AS numero,
        d.titolo AS title,
        'Direttive UE' AS collezione,
        NULL::VARCHAR AS source_filename,
        'ue' AS source,
        TRY_CAST(substr(d.celex, 2, 4) AS INTEGER) AS anno,
        NULL::BIGINT AS length_chars,
        NULL::BIGINT AS length_words,
        d.celex AS celex,
        NULL::VARCHAR AS codice_redazionale,
        CASE
            WHEN d.in_vigore THEN 'vigente'
            WHEN d.in_vigore = FALSE THEN 'abrogato'
            ELSE NULL
        END AS stato,
        NULL::VARCHAR AS materia,
        NULL::INTEGER AS qualita_score,
        NULL::INTEGER AS sunsetting_score
    FROM read_parquet('{support.direttive_ue.path}') d
    WHERE d.stato = 'risolto'
      AND NULLIF(d.celex, '') IS NOT NULL
),

all_nodes AS (
    SELECT * FROM normativa
    -- atti_api prima di gu: stesso URN può arrivare da entrambi (atto fuori
    -- collezione IC); vince la riga del corpus, che porta stato e materia.
    UNION ALL
    SELECT * FROM nodes_atti_api WHERE id NOT IN (SELECT id FROM normativa)
    -- catalogo dopo atti_api: un atto citato che ha già il nodo `atti_api`
    -- non deve produrre un secondo nodo.
    UNION ALL
    SELECT * FROM nodes_catalogo
    WHERE id NOT IN (SELECT id FROM normativa)
      AND id NOT IN (SELECT id FROM nodes_atti_api)
    UNION ALL
    SELECT * FROM nodes_articoli WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_costituzione WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_revisioni WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_gu
    WHERE id NOT IN (SELECT id FROM normativa)
      AND id NOT IN (SELECT id FROM nodes_atti_api)
      AND id NOT IN (SELECT id FROM nodes_catalogo)
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
    SELECT * FROM nodes_persone WHERE id NOT IN (SELECT id FROM normativa)
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
    SELECT * FROM nodes_promovimento WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_dl WHERE id NOT IN (SELECT id FROM normativa)
    UNION ALL
    SELECT * FROM nodes_ue WHERE id NOT IN (SELECT id FROM normativa)
)

SELECT
    id,
    CASE
        -- codici grezzi delle fonti Senato (emendamenti / dibattito)
        WHEN tipo IN ('emend', 'emendc', 'Emendamento', 'Emendamento con contributo') THEN 'EMENDAMENTO'
        WHEN tipo IN ('sommcomm', 'Sommario commissione') THEN 'SOMMARIO'
        WHEN tipo IN ('resaula', 'Risoluzione assemblea') THEN 'RISOLUZIONE'
        WHEN tipo IN ('ddlpres', 'ddldcomm', 'ddlmess', 'DDL del Presidente',
                      'DDL commissione', 'DDL messaggio') THEN 'DDL'
        WHEN tipo IN ('S', 'Sentenza') THEN 'SENTENZA'
        WHEN tipo IN ('O', 'Ordinanza') THEN 'ORDINANZA'
        WHEN tipo IN ('SENATORE', 'Senatore') THEN 'SENATORE'
        WHEN tipo IN ('DEPUTATO', 'Deputato') THEN 'DEPUTATO'
        WHEN tipo IN ('VOTAZIONE', 'Votazione') THEN 'VOTAZIONE'
        WHEN tipo IN ('GIUDICE', 'Giudice') THEN 'GIUDICE'
        WHEN tipo IN ('PROMOVIMENTO', 'Atto di promovimento') THEN 'ATTO DI PROMOVIMENTO'
        -- Atti parlamentari: open-politica mette la `natura` nel campo tipo
        -- (a volte una data, per drift upstream). Sono DDL, non leggi.
        WHEN source IN ('senato', 'senato_corpus', 'camera_ddl', 'iter_costituzionale')
             AND LOWER(tipo) LIKE '%costituz%' THEN 'DDL COSTITUZIONALE'
        WHEN source IN ('senato', 'senato_corpus', 'camera_ddl', 'iter_costituzionale') THEN 'DDL'
        -- Norme regionali/provinciali citate dalla Corte: un solo tipo canonico
        WHEN tipo LIKE 'LEGGE DELLA REGIONE%' OR tipo LIKE 'LEGGE REGIONE%'
          OR tipo LIKE 'LEGGE PROVINCIA%' OR tipo LIKE 'LEGGE DELLA PROVINCIA%'
          OR LOWER(tipo) = 'legge regionale' THEN 'LEGGE REGIONALE'
        -- Descrizioni delle massime (`norma:*`): minuscole, spesso con
        -- qualificazioni regionali/ministeriali. Normalizzo solo i tipi
        -- ricorrenti; il resto resta ALTRO (0,1% dei nodi).
        WHEN LOWER(tipo) = 'legge' THEN 'LEGGE'
        WHEN LOWER(tipo) = 'legge di conversione' THEN 'LEGGE'
        WHEN LOWER(tipo) = 'decreto legge' THEN 'DECRETO-LEGGE'
        WHEN LOWER(tipo) = 'decreto legge luogotenenziale' THEN 'DECRETO LEGISLATIVO LUOGOTENENZIALE'
        WHEN LOWER(tipo) = 'decreto legislativo' THEN 'DECRETO LEGISLATIVO'
        WHEN LOWER(tipo) = 'legge costituzionale' THEN 'LEGGE COSTITUZIONALE'
        WHEN LOWER(tipo) = 'decreto del presidente della repubblica'
            THEN 'DECRETO DEL PRESIDENTE DELLA REPUBBLICA'
        WHEN LOWER(tipo) = 'decreto ministeriale' OR LOWER(tipo) LIKE 'decreto del ministro%'
            THEN 'DECRETO MINISTERIALE'
        -- Tipi normativi canonici (normativa, costituzione, gu)
        WHEN tipo = 'COSTITUZIONE' THEN 'COSTITUZIONE'
        WHEN tipo IN ('LEGGE', 'legge', 'Legge ordinaria') THEN 'LEGGE'
        WHEN tipo = 'LEGGE COSTITUZIONALE' THEN 'LEGGE COSTITUZIONALE'
        WHEN tipo = 'DECRETO-LEGGE' THEN 'DECRETO-LEGGE'
        WHEN tipo = 'DECRETO LEGISLATIVO' THEN 'DECRETO LEGISLATIVO'
        WHEN tipo = 'DECRETO LEGISLATIVO LUOGOTENENZIALE' THEN 'DECRETO LEGISLATIVO LUOGOTENENZIALE'
        WHEN tipo = 'DECRETO' THEN 'DECRETO'
        WHEN tipo = 'DECRETO DEL PRESIDENTE DELLA REPUBBLICA' THEN 'DECRETO DEL PRESIDENTE DELLA REPUBBLICA'
        WHEN tipo IN ('DECRETO DEL PRESIDENTE DEL CONSIGLIO DEI MINISTRI',
                      'DECRETO DEL PRESIDENTE DEL CONSIGLIO')
            THEN 'DECRETO DEL PRESIDENTE DEL CONSIGLIO DEI MINISTRI'
        WHEN tipo = 'REGIO DECRETO' THEN 'REGIO DECRETO'
        WHEN tipo IN ('REGIO DECRETO LEGGE', 'REGIO DECRETO-LEGGE') THEN 'REGIO DECRETO LEGGE'
        WHEN tipo = 'REGIO DECRETO LEGISLATIVO' THEN 'REGIO DECRETO LEGISLATIVO'
        WHEN tipo = 'DECRETO LUOGOTENENZIALE' THEN 'DECRETO LUOGOTENENZIALE'
        WHEN tipo = 'DECRETO MINISTERIALE' THEN 'DECRETO MINISTERIALE'
        WHEN tipo = 'DIRETTIVA' THEN 'DIRETTIVA'
        WHEN tipo = 'REGOLAMENTO' THEN 'REGOLAMENTO'
        WHEN source = 'decreti_legge' THEN 'DECRETO-LEGGE'
        WHEN source = 'revisioni' THEN 'LEGGE COSTITUZIONALE'
        ELSE 'ALTRO'
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
    COALESCE(
        nf.vigenza_fonte,
        -- atti fuori collezione IC (fetch dettaglio-atto-urn): non passano dal
        -- clean → nessun `vigenza_fonte` upstream; valore dedicato.
        CASE
            WHEN n.collezione = 'Normattiva API' THEN 'atti_api'
            -- direttive UE: `stato` da `in_vigore` di Cellar, non dalla
            -- vigenza italiana (source='normativa').
            WHEN n.source = 'ue' THEN 'cellar'
        END
    ) AS vigenza_fonte,
    -- Provenance/assertion: additiva, conservativa, mai `verified` in automatico.
    CASE
        WHEN n.source IN (
            'normativa', 'senato', 'camera_ddl', 'senato_corpus', 'costituzione',
            'revisioni', 'gu', 'camera_deputati_legislature', 'senato_anag',
            'senato_dib', 'senato_votazioni_oggetto', 'camera_relatori',
            'iter_costituzionale', 'senato_emend'
        ) THEN 'extracted'
        WHEN n.source IN ('catalogo', 'ue', 'decreti_legge') THEN 'resolved'
        ELSE 'unknown'
    END AS assertion_type,
    CASE
        WHEN n.collezione = 'Normattiva API' THEN 'api'
        WHEN n.source IN ('senato_corpus', 'costituzione', 'revisioni', 'normativa') THEN 'akn'
        WHEN n.source = 'ue' THEN 'api'
        ELSE 'join'
    END AS method,
    CASE WHEN n.source_filename IS NULL THEN 'none' ELSE 'locator' END AS evidence_kind,
    n.source_filename AS evidence_locator,
    CASE
        WHEN n.id LIKE 'norma:%' OR n.id LIKE 'dl:%' THEN 'weak'
        ELSE 'strong'
    END AS conf_identity,
    CASE
        WHEN n.source IN ('catalogo', 'ue', 'decreti_legge') THEN 'weak'
        WHEN n.source_filename IS NULL AND n.source NOT IN ('costituzione') THEN 'weak'
        ELSE 'strong'
    END AS conf_extraction,
    CASE
        WHEN n.id LIKE 'norma:%' OR n.id LIKE 'dl:%'
          OR n.source IN ('catalogo', 'decreti_legge') THEN 'weak'
        ELSE 'strong'
    END AS conf_semantic,
    CASE
        WHEN n.source IN (
            'senato', 'camera_ddl', 'normativa', 'senato_corpus', 'gu',
            'revisioni', 'ue', 'catalogo', 'decreti_legge', 'costituzione',
            'senato_emend'
        ) AND n.anno IS NOT NULL THEN 'strong'
        WHEN n.source IN (
            'camera_deputati_legislature', 'senato_anag', 'senato_dib',
            'senato_votazioni_oggetto'
        ) AND n.anno IS NOT NULL THEN 'weak'
        ELSE 'unknown'
    END AS conf_temporal,
    'unreviewed' AS review_status,
    NULL::VARCHAR AS snapshot_id
FROM all_nodes n
LEFT JOIN akn_meta a ON n.id = a.fonte_urn
LEFT JOIN normativa_fonte nf ON n.id = nf.urn
-- Ordinamento fisico: DuckDB scrive i row group in ordine di tabella e ne
-- registra min/max. Le query MCP filtrano per id (nodi) e source_id (archi):
-- con i parquet ordinati il row-group pruning salta la maggior parte del file
-- (misurato: lookup nodo 70ms → 12ms, scan archi 52ms → 14ms) e il file è
-- anche più piccolo perché ZSTD comprime meglio dati ordinati.
ORDER BY n.id
