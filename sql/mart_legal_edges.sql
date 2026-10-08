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
      -- Guard: fonte_filename vuoto/malformato → source_id 'file:' dangling
      -- (drift upstream IC 2026-10; zero nodi file: nel grafo)
      AND NULLIF(regexp_extract(r.fonte_filename, '/([^/]+)$', 1), '') IS NOT NULL
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
edges_converte_dl AS (
    SELECT
        'dl:' || CAST(d.dl_anno AS VARCHAR) || '-' || CAST(d.dl_numero AS VARCHAR) AS source_id,
        'converte_decreto_legge' AS relation,
        'senato:' || CAST(d.ddl_id AS VARCHAR) AS target_id,
        1 AS weight,
        d.dl_anno AS source_year,
        YEAR(d.data_conversione) AS target_year,
        d.esito || ' (' || CAST(d.giorni_conversione AS VARCHAR) || 'gg)' AS evidence
    FROM read_parquet('{support.decreti_legge.path}') d
    WHERE d.ddl_id IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'dl:' || CAST(d.dl_anno AS VARCHAR) || '-' || CAST(d.dl_numero AS VARCHAR)
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
)

SELECT
    source_id,
    relation,
    target_id,
    SUM(weight) AS weight,
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
    SELECT * FROM edges_votazione
    UNION ALL
    SELECT * FROM edges_itercost_atto
    UNION ALL
    SELECT * FROM edges_itercost_rev
)
GROUP BY source_id, relation, target_id
