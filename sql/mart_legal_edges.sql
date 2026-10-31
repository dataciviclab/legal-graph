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
    SELECT urn, tipo, oggetto, numero, anno_atto
    FROM read_parquet('{support.normativa.path}')
    WHERE NULLIF(urn, '') IS NOT NULL
    UNION ALL
    -- Atti fetchati via API Normattiva (leggi ordinarie assenti dalle
    -- collezioni): servono da target per l'euristica title-regex di
    -- attua_delega. Anti-join per non duplicare URN gia' in normativa.
    SELECT
        a.urn,
        a.tipo_descrizione AS tipo,
        a.titolo AS oggetto,
        CAST(a.numero AS VARCHAR) AS numero,
        a.anno AS anno_atto
    FROM read_parquet('{support.atti_api.path}') a
    WHERE NULLIF(a.urn, '') IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM read_parquet('{support.normativa.path}') n
          WHERE n.urn = a.urn
      )
),

edges_riferimenti AS (
    SELECT
        COALESCE(lu.urn, 'file:' || regexp_extract(r.fonte_filename, '/([^/]+)$', 1)) AS source_id,
        'riferimento' AS relation,
        COALESCE(lu2.urn, 'file:' || r.bersaglio_filename) AS target_id,
        r.peso AS weight,
        r.fonte_anno AS source_year,
        r.bersaglio_anno AS target_year,
        -- Evidence: contesto del link capturato da grafo_riferimenti.py
        -- (colonna 'contesto' nel parquet; NULL se upstream non la produce ancora)
        LEFT(r.contesto, 200) AS evidence
    FROM read_parquet('{support.riferimenti.path}') r
    LEFT JOIN urn_lookup lu
      ON regexp_extract(r.fonte_filename, '/([^/]+)$', 1) = lu.filename
    LEFT JOIN urn_lookup lu2
      ON r.bersaglio_filename = lu2.filename
    WHERE r.risolto = true
      -- Guard: fonte_filename vuoto/malformato → source_id 'file:' dangling
      -- (drift upstream IC 2026-10; zero nodi file: nel grafo)
      AND NULLIF(regexp_extract(r.fonte_filename, '/([^/]+)$', 1), '') IS NOT NULL
      -- Temporal guard: un atto non può riferire un atto pubblicato dopo di esso.
      -- Tolleranza 1y su anno (date imprecise nei metadati upstream).
      -- Applicata solo ad edge con peso ≥5: edge deboli (citazioni isolate)
      -- con anno anomalo possono essere falsi positivi dei metadati, non del link.
      -- (cross-check LcnT 2026-10: 852 edge con source_year < target_year - 5)
      AND (
          r.fonte_anno IS NULL
          OR r.bersaglio_anno IS NULL
          OR r.fonte_anno >= r.bersaglio_anno - 1
          OR (r.origine = 'regex' AND r.peso < 5)
      )
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
      -- Guardrail: la Costituzione ha 139 articoli. Oggi la fonte è pulita
      -- (max 139, 0 righe fuori range), il filtro evita che un drift upstream
      -- crei target inesistenti in silenzio.
      AND TRY_CAST(c.articolo AS INTEGER) BETWEEN 1 AND 139
),

-- Lookup: mappa (norma_codice, numero, anno) dalle massime Corte → URN ID.
-- Chiuso 2026-10 (gap documentato in COMPOSE.md/ANALISI.md/COVERAGE.md).
-- ~959 / 2.444 norme state mappabili; i non-match sono atti pre-1960
-- non presenti nel corpus normativa.
-- Codici Corte → URN tipo: solo state-level (regionali esclusi: lr*).
norma_urn_lookup AS (
    SELECT
        CASE m.norma_codice
            WHEN 'l'   THEN 'legge'
            WHEN 'dl'  THEN 'decreto.legge'
            WHEN 'dlgs' THEN 'decreto.legislativo'
            WHEN 'dpr' THEN 'decreto.del.presidente.della.repubblica'
            WHEN 'rd'  THEN 'regio.decreto'
            WHEN 'rdl' THEN 'regio.decreto.legge'
        END AS urn_tipo,
        TRY_CAST(m.norma_numero AS BIGINT) AS norma_numero,
        YEAR(TRY_CAST(m.norma_data AS DATE)) AS norma_anno,
        n.id AS urn_id
    FROM (
        SELECT DISTINCT
            norma_codice,
            norma_numero,
            norma_data
        FROM read_parquet('{support.massime_corte_costituzionale.path}')
        WHERE NULLIF(norma_numero, '') IS NOT NULL
          AND TRY_CAST(norma_data AS DATE) IS NOT NULL
          AND norma_codice IN ('l', 'dl', 'dlgs', 'dpr', 'rd', 'rdl')
    ) m
    JOIN (
        SELECT
            id,
            regexp_extract(id, 'urn:nir:stato:([^:]+):\d{4}-\d{2}-\d{2};(\d+)$', 1) AS tipo,
            CAST(regexp_extract(id, 'urn:nir:stato:([^:]+):\d{4}-\d{2}-\d{2};(\d+)$', 2) AS BIGINT) AS numero,
            CAST(regexp_extract(id, 'urn:nir:stato:[^:]+:(\d{4})-\d{2}-\d{2};\d+$', 1) AS INTEGER) AS anno
        FROM mart_legal_nodes
        WHERE id LIKE 'urn:nir:stato:%'
    ) n
      ON  n.tipo = CASE m.norma_codice
            WHEN 'l'   THEN 'legge'
            WHEN 'dl'  THEN 'decreto.legge'
            WHEN 'dlgs' THEN 'decreto.legislativo'
            WHEN 'dpr' THEN 'decreto.del.presidente.della.repubblica'
            WHEN 'rd'  THEN 'regio.decreto'
            WHEN 'rdl' THEN 'regio.decreto.legge'
          END
      AND n.numero = TRY_CAST(m.norma_numero AS BIGINT)
      AND n.anno = YEAR(TRY_CAST(m.norma_data AS DATE))
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY m.norma_codice, m.norma_numero, YEAR(TRY_CAST(m.norma_data AS DATE))
        ORDER BY n.id
    ) = 1
),

edges_massime AS (
    SELECT
        'sentenza:' || CAST(anno_pronuncia AS VARCHAR) || '-' || LPAD(CAST(numero_pronuncia AS VARCHAR), 4, '0') AS source_id,
        'impugna' AS relation,
        -- Target: URN se mappato, altrimenti fallback norma:* (backward-compat)
        COALESCE(
            ul.urn_id,
            'norma:' || LOWER(COALESCE(m.norma_descrizione, 'legge'))
                   || ':' || CAST(m.norma_numero AS VARCHAR)
                   || ':' || CAST(YEAR(TRY_CAST(m.norma_data AS DATE)) AS VARCHAR)
        ) AS target_id,
        1 AS weight,
        m.anno_pronuncia AS source_year,
        NULL::INTEGER AS target_year,
        m.norma_descrizione AS evidence
    FROM read_parquet('{support.massime_corte_costituzionale.path}') m
    LEFT JOIN norma_urn_lookup ul
      ON  ul.urn_tipo = CASE m.norma_codice
            WHEN 'l'   THEN 'legge'
            WHEN 'dl'  THEN 'decreto.legge'
            WHEN 'dlgs' THEN 'decreto.legislativo'
            WHEN 'dpr' THEN 'decreto.del.presidente.della.repubblica'
            WHEN 'rd'  THEN 'regio.decreto'
            WHEN 'rdl' THEN 'regio.decreto.legge'
          END
      AND ul.norma_numero = TRY_CAST(m.norma_numero AS BIGINT)
      AND ul.norma_anno = YEAR(TRY_CAST(m.norma_data AS DATE))
    WHERE m.esito IN ('illegittimo', 'misto')
      AND NULLIF(m.norma_numero, '') IS NOT NULL
      AND TRY_CAST(m.norma_data AS DATE) IS NOT NULL
),

-- Parametro costituzionale: la massima distingue l'atto del parametro con
-- `parametro_codice` (25 = Costituzione). Senza il filtro ogni parametro
-- diventava un articolo della Costituzione: statuti regionali (91/89/88/93/80),
-- leggi (41), decreti legislativi (32), CEDU (18/23), TFUE (153)… Nel mart
-- 2026-10 erano 13.649 edge su 66.368 (20,6%): es. l'art. 145 del codice
-- civile finiva su `costituzione:art:145` (inesistente: la Costituzione ha
-- 139 articoli).
-- `BETWEEN 1 AND 139` è anche il guardrail sui target: nessun edge verso un
-- articolo che non esiste. Le 10 righe con codice 25 e articolo >139 sono
-- errori di fonte (max 1167): si perdono nel funnel di coverage.
edges_parametri AS (
    SELECT
        'sentenza:' || CAST(anno_pronuncia AS VARCHAR) || '-' || LPAD(CAST(numero_pronuncia AS VARCHAR), 4, '0') AS source_id,
        'invoca_parametro' AS relation,
        'costituzione:art:' || CAST(TRY_CAST(parametro_articolo AS INTEGER) AS VARCHAR) AS target_id,
        1 AS weight,
        anno_pronuncia AS source_year,
        NULL::INTEGER AS target_year,
        'Costituzione art. ' || CAST(TRY_CAST(parametro_articolo AS INTEGER) AS VARCHAR) AS evidence
    FROM read_parquet('{support.massime_corte_costituzionale.path}')
    WHERE parametro_codice = '25'
      AND TRY_CAST(parametro_articolo AS INTEGER) BETWEEN 1 AND 139
),

-- Atti di promovimento: `parametro_articolo` è sempre un parametro
-- costituzionale (la fonte non espone il codice dell'atto). Restano il
-- guardrail 1..139 e l'evidence.
edges_promovimento AS (
    SELECT
        'promovimento:' || CAST(anno AS VARCHAR) || '-' || LPAD(CAST(numero_atto AS VARCHAR), 4, '0') AS source_id,
        'evoca_parametro' AS relation,
        'costituzione:art:' || CAST(TRY_CAST(parametro_articolo AS INTEGER) AS VARCHAR) AS target_id,
        1 AS weight,
        anno AS source_year,
        NULL::INTEGER AS target_year,
        'Costituzione art. ' || CAST(TRY_CAST(parametro_articolo AS INTEGER) AS VARCHAR) AS evidence
    FROM read_parquet('{support.atti_promovimento.path}')
    WHERE TRY_CAST(parametro_articolo AS INTEGER) BETWEEN 1 AND 139
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
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'senato:' || CAST(s.id_ddl AS VARCHAR)
      )
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = s.urn_normattiva
      )
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
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'camera:' || CAST(l.ddl_numero AS VARCHAR)
      )
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = l.urn_normattiva
      )
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
    -- Documento AKN (senato:atto:X) → atto legislativo (senato:X).
    -- Esiste solo dove l'atto ha un nodo proprio da open-politica: quando il
    -- corpus è l'unica fonte, il documento diventa il nodo canonico
    -- `senato:X` (nodes_corpus_ddl) e non serve un edge verso sé stesso.
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
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'senato:atto:' || CAST(c.atto_num AS VARCHAR)
      )
),

edges_emend AS (
    -- Target = nodo atto del grafo (`senato:X`), che esiste sia per i DDL di
    -- open-politica sia per gli atti presenti solo nel corpus AKN (i nodi
    -- `senato_corpus` creati da nodes_corpus_ddl).
    --
    -- Prima il join era solo su `senato_ddl.id_ddl`: leg 19 → 15 atti su 486
    -- (l'export open-politica si ferma a id 55687, gli emendamenti arrivano a
    -- 60421), 103.829 emendamenti senza arco su 248.127 (42%). Con l'EXISTS sul
    -- mart i collegati passano a 225.750 (91%) e gli edge a 665.278.
    --
    -- NOTA: `fase` non entra nel join — emend.fase è formato Senato (S.NNNN),
    -- ddl.fase è formato Camera (C.NNNN). L'atto è identificato da atto_num.
    SELECT
        'senato:emend:' || CAST(regexp_extract(e.legislatura, '(\d+)', 1) AS BIGINT) || ':' || e.emend_id AS source_id,
        'emendamento' AS relation,
        'senato:' || CAST(e.atto_num AS VARCHAR) AS target_id,
        1 AS weight,
        YEAR(e.work_date) AS source_year,
        NULL::INTEGER AS target_year,
        LEFT(e.text_integrale, 100) AS evidence
    FROM read_parquet('{support.senato_emendamenti.path}') e
    WHERE e.emend_id IS NOT NULL AND e.atto_num IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'senato:' || CAST(e.atto_num AS VARCHAR)
      )
    GROUP BY e.legislatura, e.emend_id, e.work_date, e.text_integrale, e.atto_num
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
-- nel titolo. Soglia peso >= 1 (dal 2026-10): le citazioni nei preamboli
-- recenti ("Visti ...") valgono 1, quindi la vecchia soglia >= 5 azzerava
-- tutti i D.Lgs post-2015. Il peso resta quello reale della citazione:
-- weight = 1 segnala edge debole/euristico, il consumatore puo' filtrarlo.
-- Guardia temporale: un D.Lgs non puo' attuare una delega successiva.
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
      AND e.weight >= 1
      AND (
          e.source_year IS NULL
          OR e.target_year IS NULL
          OR e.source_year >= e.target_year - 1
      )
),

-- Euristica titolo: "articolo X della legge DD mese YYYY, n. Y" nel D.Lgs
-- → edge attua_delega verso la legge-base (es. D.Lgs 231/2001 → L.300/2000).
edges_delega_titolo AS (
    SELECT DISTINCT
        n1.urn AS source_id,
        'attua_delega' AS relation,
        n2.urn AS target_id,
        5 AS weight,
        n1.anno_atto AS source_year,
        n2.anno_atto AS target_year,
        n1.oggetto AS evidence
    FROM normativa_types n1
    JOIN normativa_types n2
      ON n2.tipo = 'LEGGE'
     AND n2.numero = regexp_extract(
            LOWER(n1.oggetto),
            'legge\s+\d{1,2}\s+[a-zà-ù]+\s+\d{4}\s*,?\s*n\.\s*(\d+)',
            1
        )
     AND CAST(n2.anno_atto AS VARCHAR) = regexp_extract(
            LOWER(n1.oggetto),
            'legge\s+\d{1,2}\s+[a-zà-ù]+\s+(\d{4})',
            1
        )
    WHERE n1.tipo = 'DECRETO LEGISLATIVO'
      AND LOWER(n1.oggetto) LIKE '%articolo%'
      AND LOWER(n1.oggetto) LIKE '%della legge%'
      AND regexp_extract(
            LOWER(n1.oggetto),
            'legge\s+\d{1,2}\s+[a-zà-ù]+\s+\d{4}\s*,?\s*n\.\s*\d+',
            0
        ) != ''
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
        ns.src_anno AS source_year,
        n.anno_atto AS target_year,
        LEFT(s.context, 200) AS evidence
    FROM abro_source s
    JOIN (
        SELECT urn, anno_atto, numero
        FROM read_parquet('{support.normativa.path}')
        WHERE NULLIF(urn, '') IS NOT NULL
    ) n ON s.abrogated_year = n.anno_atto
       AND s.abrogated_number = TRY_CAST(n.numero AS INTEGER)
    LEFT JOIN (
        SELECT urn, MIN(anno_atto) AS src_anno
        FROM read_parquet('{support.normativa.path}')
        WHERE NULLIF(urn, '') IS NOT NULL
        GROUP BY urn
    ) ns ON ns.urn = s.source_id
    -- Guard anno: regex IC a volte risolve la fonte su un atto anteriore
    -- al target (contesto menziona atti multipli) — scarta i temporalmente
    -- impossibili. source_year era prima valorizzato con l'anno del target.
    WHERE ns.src_anno IS NULL
       OR n.anno_atto IS NULL
       OR ns.src_anno >= n.anno_atto
),

-- Repeal AKN (italia-corpus akn_relations): URN→URN, più preciso di
-- abrogations_raw (regex su anno/numero). Stessa semantica di edges_abro:
-- fonte abroga target (activeModification). Dedup finale con edges_abro
-- via GROUP BY (source_id, relation, target_id).
-- Guard anno: esclude archi temporalmente impossibili (source < target,
-- ~30 casi da rumore passiveModification AKN).
edges_akn_abroga AS (
    SELECT
        a.fonte_urn AS source_id,
        'abroga' AS relation,
        a.target_urn AS target_id,
        1 AS weight,
        ns.anno AS source_year,
        nt.anno AS target_year,
        'akn:' || a.origin AS evidence
    FROM read_parquet('{support.akn_relations.path}') a
    JOIN mart_legal_nodes ns ON ns.id = a.fonte_urn
    JOIN mart_legal_nodes nt ON nt.id = a.target_urn
    WHERE a.rel_type = 'repeal'
      AND NULLIF(a.fonte_urn, '') IS NOT NULL
      AND NULLIF(a.target_urn, '') IS NOT NULL
      AND a.fonte_urn != a.target_urn
      AND (ns.anno IS NULL OR nt.anno IS NULL OR ns.anno >= nt.anno)
),

-- Substitution AKN: atto sostituisce porzione di altro atto (URN→URN)
edges_akn_sost AS (
    SELECT
        a.fonte_urn AS source_id,
        'sostituisce' AS relation,
        a.target_urn AS target_id,
        1 AS weight,
        ns.anno AS source_year,
        nt.anno AS target_year,
        'akn:' || a.origin AS evidence
    FROM read_parquet('{support.akn_relations.path}') a
    JOIN mart_legal_nodes ns ON ns.id = a.fonte_urn
    JOIN mart_legal_nodes nt ON nt.id = a.target_urn
    WHERE a.rel_type = 'substitution'
      AND NULLIF(a.fonte_urn, '') IS NOT NULL
      AND NULLIF(a.target_urn, '') IS NOT NULL
      AND a.fonte_urn != a.target_urn
      AND (ns.anno IS NULL OR nt.anno IS NULL OR ns.anno >= nt.anno)
),

-- Split/join/renumbering AKN: successioni strutturali oltre repeal/substitution
edges_akn_modifiche AS (
    SELECT
        a.fonte_urn AS source_id,
        a.rel_type AS relation,
        a.target_urn AS target_id,
        1 AS weight,
        ns.anno AS source_year,
        nt.anno AS target_year,
        'akn:' || a.origin AS evidence
    FROM read_parquet('{support.akn_relations.path}') a
    JOIN mart_legal_nodes ns ON ns.id = a.fonte_urn
    JOIN mart_legal_nodes nt ON nt.id = a.target_urn
    WHERE a.rel_type IN ('split', 'join', 'renumbering')
      AND NULLIF(a.fonte_urn, '') IS NOT NULL
      AND NULLIF(a.target_urn, '') IS NOT NULL
      AND a.fonte_urn != a.target_urn
      AND (ns.anno IS NULL OR nt.anno IS NULL OR ns.anno >= nt.anno)
),

-- Modifica AKN: atto che inserisce disposizioni in un altro atto.
-- rel_type 'insertion' e' act-level (URN→URN); solo coppie con entrambi
-- i nodi nel grafo (Fase 0 gap plan, 2026-10-17).
edges_akn_modifica AS (
    SELECT
        a.fonte_urn AS source_id,
        'modifica' AS relation,
        a.target_urn AS target_id,
        1 AS weight,
        ns.anno AS source_year,
        nt.anno AS target_year,
        'akn:' || a.origin AS evidence
    FROM read_parquet('{support.akn_relations.path}') a
    JOIN mart_legal_nodes ns ON ns.id = a.fonte_urn
    JOIN mart_legal_nodes nt ON nt.id = a.target_urn
    WHERE a.rel_type = 'insertion'
      AND NULLIF(a.fonte_urn, '') IS NOT NULL
      AND NULLIF(a.target_urn, '') IS NOT NULL
      AND a.fonte_urn != a.target_urn
      AND (ns.anno IS NULL OR nt.anno IS NULL OR ns.anno >= nt.anno)
),

-- Relatore sentenza: pronunce.relatore_pronuncia → nodo giudice
-- (match anagrafica con stessa normalizzazione di nodes_giudici;
-- presidenti non matchabili: formato storico COGNOME uppercase)
edges_relatore_sentenza AS (
    SELECT
        'sentenza:' || CAST(p.anno_pronuncia AS VARCHAR) || '-'
            || LPAD(CAST(p.numero_pronuncia AS VARCHAR), 4, '0') AS source_id,
        'relatore_sentenza' AS relation,
        'giudice:' || REPLACE(REPLACE(p.relatore_pronuncia, ' ', '_'), '.', '') AS target_id,
        1 AS weight,
        p.anno_pronuncia AS source_year,
        NULL::INTEGER AS target_year,
        p.ecli AS evidence
    FROM read_parquet('{support.pronunce_corte_costituzionale.path}') p
    WHERE NULLIF(p.relatore_pronuncia, '') IS NOT NULL
      AND p.anno_pronuncia IS NOT NULL
      AND p.numero_pronuncia IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'sentenza:' || CAST(p.anno_pronuncia AS VARCHAR) || '-'
              || LPAD(CAST(p.numero_pronuncia AS VARCHAR), 4, '0')
      )
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'giudice:' || REPLACE(REPLACE(p.relatore_pronuncia, ' ', '_'), '.', '')
      )
),

-- Relatori Senato (open-politica senato_relatori): senatore → ddl
-- Solo se entrambi gli endpoint esistono in mart_legal_nodes
edges_relatore AS (
    SELECT
        'senatore:' || CAST(r.senatore_id AS VARCHAR) AS source_id,
        'relatore' AS relation,
        'senato:' || CAST(r.ddl_id AS VARCHAR) AS target_id,
        1 AS weight,
        NULL::INTEGER AS source_year,
        NULL::INTEGER AS target_year,
        LEFT(COALESCE(r.relatore_label, ''), 200) AS evidence
    FROM read_parquet({support.senato_relatori.outputs}, union_by_name = true) r
    WHERE r.senatore_id IS NOT NULL
      AND r.ddl_id IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'senatore:' || CAST(r.senatore_id AS VARCHAR)
      )
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'senato:' || CAST(r.ddl_id AS VARCHAR)
      )
),

-- Firmatari Camera (open-politica camera_firmatari): deputato → atto
edges_firmatario AS (
    SELECT
        'deputato:' || CAST(f.persona_id AS VARCHAR) AS source_id,
        'firmatario' AS relation,
        'camera:' || CAST(f.atto_id AS VARCHAR) AS target_id,
        1 AS weight,
        f.legislatura AS source_year,
        NULL::INTEGER AS target_year,
        LEFT(COALESCE(f.ruolo, ''), 200) AS evidence
    FROM read_parquet({support.camera_firmatari.outputs}, union_by_name = true) f
    WHERE f.persona_id IS NOT NULL
      AND f.atto_id IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'deputato:' || CAST(f.persona_id AS VARCHAR)
      )
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'camera:' || CAST(f.atto_id AS VARCHAR)
      )
),

-- Relatori Camera (open-politica camera_relatori #62): deputato → atto
-- Chiave atto dedicata camera:atto:{leg}_{id} — l'atto Camera non è un DDL
-- (evita la collisione numerica di camera:{ddl}). Copertura parziale per
-- design (~19% Leg19): atto_camera NULL = incarico senza atto LOD.
edges_relatore_camera AS (
    SELECT
        'deputato:' || CAST(r.deputato_id AS VARCHAR) AS source_id,
        'relatore' AS relation,
        'camera:atto:' || r.atto_id_leg AS target_id,
        1 AS weight,
        YEAR(r.data) AS source_year,
        NULL::INTEGER AS target_year,
        LEFT(COALESCE(r.tipo, ''), 120) AS evidence
    FROM read_parquet({support.camera_relatori.outputs}, union_by_name = true) r
    WHERE r.deputato_id IS NOT NULL
      AND NULLIF(r.atto_id_leg, '') IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'deputato:' || CAST(r.deputato_id AS VARCHAR)
      )
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'camera:atto:' || r.atto_id_leg
      )
),

-- DL conversione (open-politica decreti_legge #63): nodo dl:* → DDL di
-- conversione Senato. ddl_id representative (ramo S, data recente) —
-- non univoco se più iter; esito + giorni in evidence.
-- La fonte identifica il DL con anno+numero; il nodo canonico è l'URN del
-- corpus quando esiste (in quel caso `nodes_dl` non crea più `dl:*`).
decreti_legge_urn AS (
    SELECT
        d.*,
        COALESCE(
            (
                -- numero/anno dall'id URN, non dalle colonne: i nodi `gu`
                -- hanno `numero` NULL ma l'id è un URN completo.
                SELECT MIN(n.id) FROM mart_legal_nodes n
                WHERE n.id LIKE 'urn:nir:stato:decreto.legge:%'
                  AND TRY_CAST(regexp_extract(
                          n.id, 'urn:nir:stato:[^:]+:([0-9]{4})-[0-9]{2}-[0-9]{2};[0-9]+$', 1
                      ) AS INTEGER) = d.dl_anno
                  AND TRY_CAST(regexp_extract(
                          n.id, 'urn:nir:stato:[^:]+:[0-9]{4}-[0-9]{2}-[0-9]{2};([0-9]+)$', 1
                      ) AS BIGINT) = d.dl_numero
            ),
            'dl:' || CAST(d.dl_anno AS VARCHAR) || '-' || CAST(d.dl_numero AS VARCHAR)
        ) AS source_id
    FROM read_parquet('{support.decreti_legge.path}') d
    WHERE d.ddl_id IS NOT NULL
),

edges_converte_dl AS (
    SELECT
        d.source_id,
        'converte_decreto_legge' AS relation,
        'senato:' || CAST(d.ddl_id AS VARCHAR) AS target_id,
        1 AS weight,
        d.dl_anno AS source_year,
        YEAR(d.data_conversione) AS target_year,
        d.esito || ' (' || CAST(d.giorni_conversione AS VARCHAR) || 'gg)' AS evidence
    FROM decreti_legge_urn d
    WHERE EXISTS (
          SELECT 1 FROM mart_legal_nodes n WHERE n.id = d.source_id
      )
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'senato:' || CAST(d.ddl_id AS VARCHAR)
      )
),

-- Firmatari Senato (open-politica senato_firmatari): senatore → ddl
-- Speculare a camera_firmatari. Solo firme attive (ritiro NULL) e
-- soli senatori (presentatori On./governativi non hanno senatore_id).
-- Dedup (senatore, ddl): la fonte ha più riga per dataAggiuntaFirma.
edges_firmatario_sen AS (
    SELECT
        'senatore:' || CAST(f.senatore_id AS VARCHAR) AS source_id,
        'firmatario' AS relation,
        'senato:' || CAST(f.ddl_id AS VARCHAR) AS target_id,
        CASE WHEN BOOL_OR(f.primo_firmatario) THEN 2 ELSE 1 END AS weight,
        NULL::INTEGER AS source_year,
        NULL::INTEGER AS target_year,
        CASE WHEN BOOL_OR(f.primo_firmatario) THEN 'primo_firmatario' ELSE 'firmatario' END AS evidence
    FROM read_parquet('{support.senato_firmatari.path}') f
    WHERE f.senatore_id IS NOT NULL
      AND f.ddl_id IS NOT NULL
      AND f.data_ritiro_firma IS NULL
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'senatore:' || CAST(f.senatore_id AS VARCHAR)
      )
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'senato:' || CAST(f.ddl_id AS VARCHAR)
      )
    GROUP BY f.senatore_id, f.ddl_id
),

-- Bridge senatore↔deputato via ponte_persona (open-politica).
-- Unifica i namespace senatore:* e deputato:* (issue #20).
-- tipo_match: 'exact' | 'fuzzy' — evidence per filtering.
edges_ponte_persona AS (
    SELECT
        'senatore:' || CAST(p.senatore_id AS VARCHAR) AS source_id,
        'collega' AS relation,
        'deputato:' || CAST(p.persona_id AS VARCHAR) AS target_id,
        CASE WHEN p.tipo_match = 'exact' THEN 2 ELSE 1 END AS weight,
        NULL::INTEGER AS source_year,
        NULL::INTEGER AS target_year,
        p.tipo_match AS evidence
    FROM read_parquet('{support.ponte_persona.path}') p
    WHERE p.senatore_id IS NOT NULL
      AND p.persona_id IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'senatore:' || CAST(p.senatore_id AS VARCHAR)
      )
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'deputato:' || CAST(p.persona_id AS VARCHAR)
      )
),

-- Votazioni Senato → DDL (senato_votazioni_oggetto)
edges_votazione AS (
    SELECT
        'votazione:' || v.votazione_id AS source_id,
        'vota' AS relation,
        'senato:' || CAST(v.ddl_id AS VARCHAR) AS target_id,
        1 AS weight,
        v.legislatura AS source_year,
        NULL::INTEGER AS target_year,
        COALESCE(v.esito, '') || ' | ' || COALESCE(v.tipo_votazione, '') AS evidence
    FROM read_parquet({support.senato_votazioni_oggetto.outputs}, union_by_name = true) v
    WHERE NULLIF(v.votazione_id, '') IS NOT NULL
      AND v.ddl_id IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'votazione:' || v.votazione_id
      )
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'senato:' || CAST(v.ddl_id AS VARCHAR)
      )
),

-- Iter Cost.: proposta DDL → atto Camera/Senato
-- Join solo se il target è davvero una proposta Cost. (atto_num Camera
-- collide col namespace DDL ordinari — filtriamo sul titolo/tipo).
edges_itercost_atto AS (
    SELECT
        'itercost:' || i.camera_o_senato || ':' || CAST(i.atto_num AS VARCHAR) AS source_id,
        'proposta_cost' AS relation,
        i.camera_o_senato || ':' || CAST(i.atto_num AS VARCHAR) AS target_id,
        1 AS weight,
        i.legislatura AS source_year,
        NULL::INTEGER AS target_year,
        LEFT(COALESCE(i.stato, i.proponente, ''), 200) AS evidence
    FROM read_parquet({support.iter_costituzionale.outputs}, union_by_name = true) i
    WHERE NULLIF(i.camera_o_senato, '') IS NOT NULL
      AND i.atto_num IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'itercost:' || i.camera_o_senato || ':' || CAST(i.atto_num AS VARCHAR)
      )
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = i.camera_o_senato || ':' || CAST(i.atto_num AS VARCHAR)
            AND (
                LOWER(COALESCE(n.title, '')) LIKE '%costituz%'
                OR UPPER(COALESCE(n.tipo, '')) LIKE '%COSTITUZ%'
            )
      )
),

-- Iter Cost.: proposta → legge di revisione (rev_urn) se ha_legge
edges_itercost_rev AS (
    SELECT
        'itercost:' || i.camera_o_senato || ':' || CAST(i.atto_num AS VARCHAR) AS source_id,
        'diventa_revisione' AS relation,
        'revisione:' || i.rev_urn AS target_id,
        1 AS weight,
        i.legislatura AS source_year,
        YEAR(i.rev_data) AS target_year,
        LEFT(COALESCE(i.rev_titolo, i.rev_urn, ''), 200) AS evidence
    FROM read_parquet({support.iter_costituzionale.outputs}, union_by_name = true) i
    WHERE NULLIF(i.camera_o_senato, '') IS NOT NULL
      AND i.atto_num IS NOT NULL
      AND i.ha_legge = 1
      AND NULLIF(i.rev_urn, '') IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'itercost:' || i.camera_o_senato || ':' || CAST(i.atto_num AS VARCHAR)
      )
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'revisione:' || i.rev_urn
      )
),

grouped AS (
    SELECT
        source_id,
        relation,
        target_id,
        -- MAX, non SUM: una coppia presente sia come regex sia come AKN non deve
        -- sommare i pesi (double-count). Il peso regex è già per coppia.
        MAX(weight) AS weight,
        MIN(source_year) AS source_year,
        MAX(target_year) AS target_year,
        -- MIN e non FIRST: FIRST dipende dall'ordine fisico di input e fa
        -- fluttuare evidence a ogni rebuild (riproducibilità del mart)
        MIN(evidence) AS evidence
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
    SELECT * FROM edges_delega_titolo
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
    UNION ALL
    SELECT * FROM edges_akn_abroga
    UNION ALL
    SELECT * FROM edges_akn_sost
    UNION ALL
    SELECT * FROM edges_akn_modifiche
    UNION ALL
    SELECT * FROM edges_akn_modifica
    UNION ALL
    SELECT * FROM edges_relatore_sentenza
    UNION ALL
    SELECT * FROM edges_relatore
    UNION ALL
    SELECT * FROM edges_relatore_camera
    UNION ALL
    SELECT * FROM edges_converte_dl
    UNION ALL
    SELECT * FROM edges_firmatario
    UNION ALL
    SELECT * FROM edges_firmatario_sen
    UNION ALL
    SELECT * FROM edges_ponte_persona
    UNION ALL
    SELECT * FROM edges_votazione
    UNION ALL
    SELECT * FROM edges_itercost_atto
        UNION ALL
        SELECT * FROM edges_itercost_rev
    )
    -- Autoanelli: un atto che cita sé stesso (rinvii interni tra articoli
    -- collassati sul nodo-atto) o che "abroga sé stesso" non è una relazione
    -- tra atti. Senza questo filtro l'edge più pesante del grafo è un self-loop
    -- (D.Lgs 58/1998 → sé stesso, weight 436) e le metriche di impatto lo contano.
    WHERE source_id <> target_id
    GROUP BY source_id, relation, target_id
),

-- Evidenza formulaica o non informativa: lo **stesso** snippet usato da più di
-- 50 atti distinti per citare lo stesso target. Caso tipico: la clausola di
-- pubblicazione in ogni preambolo ("…pubblicazioni ufficiali… D.P.R.
-- 1092/1985"). Misurato 2026-11: 7 gruppi, 3.140 edge, un solo target.
--
-- Il match è **esatto**: normalizzare (lower, primi N char) porta a 14.178
-- edge su 1.790 target e cattura citazioni AKN legittime di conversione.
-- Il flag si calcola DOPO il GROUP BY, sulle righe pubblicate: chi legge il
-- mart può ricontarlo e ottenere lo stesso numero.
-- Clausola di lettura: la fonte (`riferimenti.clausola_lettura`) marca le
-- citazioni del tipo "…si legge…" (es. art. 10 del T.U. sulla pubblicazione).
-- È un segnale semantico della fonte, complementare alla frequenza dello
-- snippet: 5.973 righe upstream, tutte verso il D.P.R. 1092/1985.
--
-- Stessa risoluzione filename→URN di `edges_riferimenti`; i due punti devono
-- restare allineati, il test `test_reading_clause_solo_riferimento` lo verifica.
reading_clause_edges AS (
    SELECT DISTINCT
        COALESCE(lu.urn, 'file:' || regexp_extract(r.fonte_filename, '/([^/]+)$', 1)) AS source_id,
        COALESCE(lu2.urn, 'file:' || r.bersaglio_filename) AS target_id
    FROM read_parquet('{support.riferimenti.path}') r
    LEFT JOIN urn_lookup lu
      ON regexp_extract(r.fonte_filename, '/([^/]+)$', 1) = lu.filename
    LEFT JOIN urn_lookup lu2
      ON r.bersaglio_filename = lu2.filename
    WHERE r.clausola_lettura
      AND r.risolto
      AND NULLIF(regexp_extract(r.fonte_filename, '/([^/]+)$', 1), '') IS NOT NULL
),

formulaic_evidence AS (
    SELECT evidence
    FROM grouped
    WHERE relation = 'riferimento'
      AND evidence IS NOT NULL
      AND evidence <> 'akn:ref'
    GROUP BY 1
    HAVING COUNT(DISTINCT source_id) > 50
)

SELECT
    g.source_id,
    g.relation,
    g.target_id,
    g.weight,
    g.source_year,
    g.target_year,
    g.evidence,
    (
        g.relation = 'riferimento'
        AND g.evidence IN (SELECT evidence FROM formulaic_evidence)
    ) AS is_formulaic,
    (
        g.relation = 'riferimento'
        AND EXISTS (
            SELECT 1 FROM reading_clause_edges rc
            WHERE rc.source_id = g.source_id AND rc.target_id = g.target_id
        )
    ) AS is_reading_clause,
    -- Tipo di evidenza (solo per `riferimento`; NULL altrove):
    --   missing → segnaposto `akn:ref`: sappiamo che A cita B, non dove
    --             (italia-corpus#109 deve far sparire questo stato)
    --   locator → frammento di URL/URN, nessun testo
    --   quote   → testo attorno alla citazione
    CASE
        WHEN g.relation <> 'riferimento' THEN NULL
        WHEN g.evidence IS NULL OR g.evidence = '' OR g.evidence = 'akn:ref'
            THEN 'missing'
        WHEN g.evidence LIKE '%urn:nir:%'
          OR g.evidence LIKE '%i-res/N2Ls%'
          OR g.evidence LIKE '%:costituzione:%'
            THEN 'locator'
        ELSE 'quote'
    END AS evidence_kind
FROM grouped g
