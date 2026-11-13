-- mart_legal_edges.sql — relazioni del Legal Knowledge Graph
--
-- Una riga per (source_id, relation, target_id) deduplicata.
-- Stesso schema dello storico data/legal_edges.parquet.
-- attua_delega: riusa riferimenti + tipi/oggetti da normativa
-- (non dal mart nodi — il compose e' indipendente per tabella).

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
-- Locator AKN (#67, italia-corpus #142): due fonti, in ordine di precisione.
-- 1. `articolo`: forma normalizzata `art_N[-suffisso][-comM]`. Può essere una
--    lista (`art_3,art_47`) quando un edge copre più articoli → si splitta.
-- 2. fallback su `detail`: fragment `#art_...` delle citazioni (righe prodotte
--    prima di #142 e collezioni non ancora riprocessate).
-- Aggregazione per (fonte, target, rel_type) con lista ordinata: l'ordine è
-- deterministico tra run. Filtro consigliato sui multi-locator:
--   (',' || evidence_locator || ',') LIKE '%,art_3,%'
-- La regex ripete la grammatica di `articolo_da_frammento` (suffisso incluso:
-- `art_47-bis` ≠ `art_47`), altrimenti il fallback perderebbe il suffisso.
-- RE2 (DuckDB) non ha il lookahead: si usa `\b` con gli ordinali lunghi prima
-- (`terdecies` non deve essere troncato a `ter`).
akn_locator AS (
    SELECT
        fonte_urn,
        target_urn,
        rel_type,
        array_to_string(list_sort(list(DISTINCT trim(loc))), ',') AS locators
    FROM (
        SELECT
            fonte_urn,
            target_urn,
            rel_type,
            unnest(string_split(
                COALESCE(
                    NULLIF(articolo, ''),
                    regexp_extract(
                        detail,
                        '#(art_[0-9]+(?:-(?:terdecies|quaterdecies|septendecies|quindecies|octodecies|novodecies|duodecies|undecies|quinquies|sedecies|sexies|septies|octies|novies|decies|quater|vicies|bis|ter)\b)?(?:-com[0-9]+)?)',
                        1
                    )
                ),
                ','
            )) AS loc
        FROM read_parquet('{support.akn_relations.path}')
    )
    WHERE trim(loc) <> ''
    GROUP BY 1, 2, 3
),
urn_lookup AS (
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

-- Alias URN che non sono atti NIR citabili per numero: la Costituzione è
-- citata come **atto intero** (`...;const`, 4.971 righe) e ha un nodo suo
-- (`costituzione:atto`), non un articolo. Regola esplicita, in un punto solo:
-- mapparla su `costituzione:art:N` sarebbe sbagliato (la citazione non
-- individua un articolo).
urn_aliases AS (
    SELECT * FROM (VALUES
        ('urn:nir:stato:costituzione:1947-12-27;const', 'costituzione:atto')
    ) AS t(urn, node_id)
    -- Direttive UE: le due grafie URN di Normattiva (`direttivaue` e
    -- `direttiva.ue`) puntano allo stesso nodo CELEX. La lista arriva dal
    -- dataset italia-corpus `direttive_ue` (una riga per direttiva).
    UNION ALL
    SELECT trim(u) AS urn, 'celex:' || d.celex AS node_id
    FROM read_parquet('{support.direttive_ue.path}') d,
         -- `urn_varianti` è separato da `|`: gli URN contengono `;`.
         UNNEST(string_split(d.urn_varianti, '|')) AS t(u)
    WHERE NULLIF(trim(u), '') IS NOT NULL
      AND d.stato = 'risolto'
),

-- Citation: `mart_riferimenti_urn` porta già la URN canonica su entrambi i
-- lati (normalizzazione in italia-corpus). Qui join esatto sui nodi: nessuna
-- chiave annuale duplicata nel compose.
edges_riferimenti AS (
    SELECT
        r.fonte_urn AS source_id,
        'riferimento' AS relation,
        n.id AS target_id,
        r.peso AS weight,
        r.fonte_anno AS source_year,
        r.bersaglio_anno AS target_year,
        LEFT(r.contesto, 200) AS evidence
    FROM read_parquet('{support.riferimenti_urn.path}') r
    LEFT JOIN urn_aliases ua
      ON ua.urn = r.bersaglio_urn
    JOIN mart_legal_nodes n
      ON n.id = COALESCE(ua.node_id, r.bersaglio_urn)
    WHERE r.rel_type = 'citation'
      -- Temporal guard: un atto non può riferire un atto pubblicato dopo di
      -- esso (tolleranza 1 anno). L'eccezione per gli edge regex deboli
      -- (peso < 5) resta: anni anomali lì sono rumore di metadati.
      AND (
          r.fonte_anno IS NULL
          OR r.bersaglio_anno IS NULL
          OR r.fonte_anno >= r.bersaglio_anno - 1
          OR (r.da_regex AND r.peso < 5)
      )
),

edges_citazioni AS (
    SELECT
        COALESCE(lu.urn, 'file:' || regexp_extract(c.fonte_filename, '/([^/]+)$', 1)) AS source_id,
        'cita_costituzione' AS relation,
        c.bersaglio_id AS target_id,
        1 AS weight,
        c.fonte_anno AS source_year,
        NULL::INTEGER AS target_year,
        LEFT(c.contesto, 200) AS evidence
    FROM read_parquet('{support.riferimenti_clean.path}') c
    LEFT JOIN urn_lookup lu
      ON regexp_extract(c.fonte_filename, '/([^/]+)$', 1) = lu.filename
    WHERE c.origine = 'costituzione'
      AND c.bersaglio_id IS NOT NULL
      -- Guardrail: la Costituzione ha 139 articoli. Oggi la fonte è pulita
      -- (max 139, 0 righe fuori range), il filtro evita che un drift upstream
      -- crei target inesistenti in silenzio.
      AND TRY_CAST(regexp_extract(c.bersaglio_id, ':art:([0-9]+)$', 1) AS INTEGER) BETWEEN 1 AND 139
),

-- Lookup: mappa (norma_codice, numero, anno) dalle massime Corte → URN ID.
-- Chiuso 2026-10 (gap documentato in COMPOSE.md/ANALISI.md/COVERAGE.md).
-- ~959 / 2.444 norme state mappabili; i non-match sono atti pre-1960
-- non presenti nel corpus normativa.
-- Codici Corte → URN tipo: solo state-level (regionali esclusi: lr*).
-- `norma_data`: il mart di costituzione-italiana la espone come stringa
-- dd/mm/yyyy (#42); accettiamo anche ISO per non legarci al formato.
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
        YEAR(m.norma_data_date) AS norma_anno,
        n.id AS urn_id
    FROM (
        SELECT DISTINCT
            norma_codice,
            norma_numero,
            norma_data_date
        FROM massime_norme_dated
        WHERE NULLIF(norma_numero, '') IS NOT NULL
          AND norma_data_date IS NOT NULL
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
      AND n.anno = YEAR(m.norma_data_date)
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY m.norma_codice, m.norma_numero, YEAR(m.norma_data_date)
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
                   || ':' || CAST(YEAR(m.norma_data_date) AS VARCHAR)
        ) AS target_id,
        1 AS weight,
        m.anno_pronuncia AS source_year,
        NULL::INTEGER AS target_year,
        m.norma_descrizione AS evidence
    FROM massime_norme_dated m
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
      AND ul.norma_anno = YEAR(m.norma_data_date)
    WHERE m.esito IN ('illegittimo', 'misto')
      AND NULLIF(m.norma_numero, '') IS NOT NULL
      AND m.norma_data_date IS NOT NULL
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
    FROM read_parquet('{support.massime_parametri.path}')
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
        'camera:' || CAST(l.legislatura AS VARCHAR) || ':' || CAST(l.ddl_numero AS VARCHAR) AS source_id,
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
          WHERE n.id = 'camera:' || CAST(l.legislatura AS VARCHAR) || ':' || CAST(l.ddl_numero AS VARCHAR)
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

-- Bridge atto Senato: `fase` (S.NNN) è la chiave, `atto_num` è la directory
-- di archiviazione e può essere replicata (fino a 57 copie per fase; dedup
-- upstream 2026-11). Risoluzione del nodo atto in due passi, nell'ordine:
--   1) fase + legislatura → `senato_ddl.id_ddl`  → `senato:{id_ddl}`
--   2) fase non in `senato_ddl` e directory unica (`n_atti_fase = 1`)
--      → `senato:{atto_num}`
-- Se resta ambiguo (fase fuori da open-politica e directory replicata) non si
-- emette l'edge: meglio un buco dichiarato in coverage che un atto sbagliato.
ddl_fase AS (
    SELECT
        CAST(legislatura AS INTEGER) AS legislatura,
        fase,
        -- 11 fasi hanno più di un id_ddl: scelta deterministica
        MIN(id_ddl) AS id_ddl
    FROM read_parquet({support.senato_ddl.outputs}, union_by_name = true)
    WHERE id_ddl IS NOT NULL AND NULLIF(fase, '') IS NOT NULL
    GROUP BY 1, 2
),

emend_target AS (
    SELECT
        e.*,
        COALESCE(
            'senato:' || CAST(df.id_ddl AS VARCHAR),
            CASE WHEN e.n_atti_fase = 1
                 THEN 'senato:' || CAST(e.atto_num AS VARCHAR) END
        ) AS target_id
    FROM read_parquet('{support.senato_emendamenti.path}') e
    LEFT JOIN ddl_fase df
      ON df.legislatura = CAST(regexp_extract(e.legislatura, '(\d+)', 1) AS INTEGER)
     AND df.fase = e.fase
),

edges_emend AS (
    SELECT
        'senato:emend:' || CAST(regexp_extract(t.legislatura, '(\d+)', 1) AS BIGINT)
            || ':' || t.fase || ':' || t.emend_id AS source_id,
        'emendamento' AS relation,
        t.target_id,
        1 AS weight,
        YEAR(t.work_date) AS source_year,
        NULL::INTEGER AS target_year,
        LEFT(t.text_integrale, 100) AS evidence
    FROM emend_target t
    WHERE t.emend_id IS NOT NULL
      AND t.target_id IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n WHERE n.id = t.target_id
      )
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
-- `evidence` è lo snippet citante (da `riferimenti`), NON il titolo della
-- legge bersaglio: l'evidenza deve mostrare dove l'atto nomina la delega.
-- `assertion_type` resta `inferred`: la citazione non prova da sola che sia
-- la legge delegante (per quello c'è `edges_delega_titolo`).
edges_delega AS (
    SELECT
        e.source_id,
        'attua_delega' AS relation,
        e.target_id,
        e.weight,
        e.source_year,
        e.target_year,
        e.evidence,
        FALSE AS from_title
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
        n1.oggetto AS evidence,
        TRUE AS from_title
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

-- Abrogazioni: `rel_type='abroga'` dal mart URN-based di italia-corpus.
-- La risoluzione (fonte→URN, target anno+numero→URN, guardia temporale) vive
-- in IC (`grafo_riferimenti._archi_da_abrogazioni`): qui nessun join normativa.
edges_abro AS (
    SELECT
        r.fonte_urn AS source_id,
        'abroga' AS relation,
        r.bersaglio_urn AS target_id,
        r.peso AS weight,
        r.fonte_anno AS source_year,
        r.bersaglio_anno AS target_year,
        LEFT(r.contesto, 200) AS evidence
    FROM read_parquet('{support.riferimenti_urn.path}') r
    WHERE r.rel_type = 'abroga'
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
        'camera:' || CAST(f.legislatura AS VARCHAR) || ':' || CAST(f.atto_id AS VARCHAR) AS target_id,
        1 AS weight,
        -- H2 (feedback 2026-11-13): `legislatura` non è un anno.
        NULL::INTEGER AS source_year,
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
          WHERE n.id = 'camera:' || CAST(f.legislatura AS VARCHAR) || ':' || CAST(f.atto_id AS VARCHAR)
      )
),

-- Relatori Camera (open-politica camera_relatori #62): deputato → atto
-- L'atto Camera è `camera:{legislatura}:{id_ddl}` (stessa identità dei nodi
-- `camera_ddl`): il vecchio namespace `camera:atto:{leg}_{id}` era un
-- duplicato dello stesso atto ed è rimosso (M1). Il vecchio id resta come
-- alias in `mart_legal_entity_alias`. Copertura parziale per design
-- (~19% Leg19): atto_camera NULL = incarico senza atto LOD.
edges_relatore_camera AS (
    SELECT
        'deputato:' || CAST(r.deputato_id AS VARCHAR) AS source_id,
        'relatore' AS relation,
        'camera:' || split_part(r.atto_id_leg, '_', 1)
            || ':' || split_part(r.atto_id_leg, '_', 2) AS target_id,
        1 AS weight,
        YEAR(r.data) AS source_year,
        NULL::INTEGER AS target_year,
        LEFT(COALESCE(r.tipo, ''), 120) AS evidence
    FROM read_parquet({support.camera_relatori.outputs}, union_by_name = true) r
    WHERE r.deputato_id IS NOT NULL
      AND regexp_matches(r.atto_id_leg, '^[0-9]+_[0-9]+$')
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'deputato:' || CAST(r.deputato_id AS VARCHAR)
      )
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'camera:' || split_part(r.atto_id_leg, '_', 1)
              || ':' || split_part(r.atto_id_leg, '_', 2)
      )
),

-- DL conversione (open-politica `atti`): nodo del DL → atto di conversione.
-- La fonte identifica il DL con anno+numero; il nodo canonico è l'URN del
-- corpus quando esiste (in quel caso `nodes_dl` non crea `dl:*`).
-- Un DL può avere più atti di conversione **nello stesso ramo** (due DDL
-- Senato per lo stesso decreto, es. DL 91/2013 → senato:38744 + senato:38770):
-- il mart ne emette uno per atto. La Camera non porta identità DL
-- (`dl_numero` è 0 sul ramo Camera), quindi i doppi sono sempre Senato:
-- per contare i DL si deduplica su `source_id`, non sulle righe.
conversione_dl AS (
    SELECT
        a.atto_id,
        a.dl_numero,
        a.dl_anno,
        a.data_legge,
        d.esito,
        d.giorni_conversione,
        COALESCE(
            (
                -- numero/anno dall'id URN, non dalle colonne: i nodi `gu`
                -- hanno `numero` NULL ma l'id è un URN completo.
                SELECT MIN(n.id) FROM mart_legal_nodes n
                WHERE n.id LIKE 'urn:nir:stato:decreto.legge:%'
                  AND TRY_CAST(regexp_extract(
                          n.id, 'urn:nir:stato:[^:]+:([0-9]{4})-[0-9]{2}-[0-9]{2};[0-9]+$', 1
                      ) AS INTEGER) = a.dl_anno
                  AND TRY_CAST(regexp_extract(
                          n.id, 'urn:nir:stato:[^:]+:[0-9]{4}-[0-9]{2}-[0-9]{2};([0-9]+)$', 1
                      ) AS BIGINT) = a.dl_numero
            ),
            'dl:' || CAST(a.dl_anno AS VARCHAR) || '-' || CAST(a.dl_numero AS VARCHAR)
        ) AS source_id
    FROM read_parquet('{support.atti.path}') a
    JOIN read_parquet('{support.dl.path}') d
      ON d.dl_numero = a.dl_numero
     AND d.dl_anno = a.dl_anno
    WHERE a.dl_numero IS NOT NULL
      AND a.dl_anno IS NOT NULL
),

-- Esito per coppia DL→atto, univoco: serve alla colonna additiva `esito`.
conversione_esito AS (
    SELECT source_id, atto_id, MAX(esito) AS esito
    FROM conversione_dl
    GROUP BY 1, 2
),

edges_converte_dl AS (
    SELECT
        d.source_id,
        'converte_decreto_legge' AS relation,
        d.atto_id AS target_id,
        1 AS weight,
        d.dl_anno AS source_year,
        YEAR(d.data_legge) AS target_year,
        d.esito
          || COALESCE(' (' || CAST(d.giorni_conversione AS VARCHAR) || 'gg)', '')
          AS evidence
    FROM conversione_dl d
    WHERE EXISTS (
          SELECT 1 FROM mart_legal_nodes n WHERE n.id = d.source_id
      )
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n WHERE n.id = d.atto_id
      )
),

-- Firmatari Senato (open-politica senato_firmatari): persona → ddl
-- Speculare a camera_firmatari. Solo firme attive (ritiro NULL).
-- La fonte ha due namespace di identità: `senatore_id` per i senatori e
-- `deputato_url` per i deputati che firmano in Senato (25.630 righe su
-- 38.567). Il secondo va risolto sullo stesso nodo `deputato:{persona_id}`
-- della Camera, non scartato: senza, 3.210 DDL restano senza firmatari
-- (1.409 atti isolati nel grafo).
-- Dedup (persona, ddl): la fonte ha più righe per dataAggiuntaFirma.
edges_firmatario_sen AS (
    SELECT
        CASE
            WHEN f.senatore_id IS NOT NULL
                THEN 'senatore:' || CAST(f.senatore_id AS VARCHAR)
            ELSE 'deputato:' || regexp_extract(
                f.deputato_url, '/([a-z]?)([0-9]+)_[0-9]+$', 2
            )
        END AS source_id,
        'firmatario' AS relation,
        'senato:' || CAST(f.ddl_id AS VARCHAR) AS target_id,
        CASE WHEN BOOL_OR(f.primo_firmatario) THEN 2 ELSE 1 END AS weight,
        NULL::INTEGER AS source_year,
        NULL::INTEGER AS target_year,
        CASE
            WHEN BOOL_OR(f.primo_firmatario) THEN 'primo_firmatario'
            WHEN BOOL_OR(f.senatore_id IS NULL) THEN 'firmatario_deputato'
            ELSE 'firmatario'
        END AS evidence
    FROM read_parquet('{support.senato_firmatari.path}') f
    WHERE f.ddl_id IS NOT NULL
      AND f.data_ritiro_firma IS NULL
      AND (
          f.senatore_id IS NOT NULL
          OR NULLIF(f.deputato_url, '') IS NOT NULL
      )
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = CASE
              WHEN f.senatore_id IS NOT NULL
                  THEN 'senatore:' || CAST(f.senatore_id AS VARCHAR)
              ELSE 'deputato:' || regexp_extract(
                  f.deputato_url, '/([a-z]?)([0-9]+)_[0-9]+$', 2
              )
          END
      )
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'senato:' || CAST(f.ddl_id AS VARCHAR)
      )
    GROUP BY 1, 3
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
        NULL::INTEGER AS source_year,  -- H2: legislatura ≠ anno
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
-- Iter Cost.: proposta DDL → atto Camera/Senato.
-- L'id `itercost:*` è interno a questo compose; il **target** è un atto del
-- grafo e usa la chiave di `atti`: `camera:{legislatura}:{num}` (l'`atto_num`
-- Camera riparte ogni legislatura) oppure `senato:{num}`.
-- Il join è solo su proposte Cost. vere: l'atto Camera ha un DDL ordinario
-- con lo stesso numero, quindi si filtra su titolo/tipo.
itercost_proposta AS (
    SELECT
        i.*,
        'itercost:' || i.camera_o_senato || ':' || CAST(i.atto_num AS VARCHAR) AS source_id,
        CASE
            WHEN i.camera_o_senato = 'camera'
                THEN 'camera:' || CAST(i.legislatura AS VARCHAR) || ':'
                     || CAST(i.atto_num AS VARCHAR)
            ELSE 'senato:' || CAST(i.atto_num AS VARCHAR)
        END AS target_id
    FROM read_parquet({support.iter_costituzionale.outputs}, union_by_name = true) i
    WHERE NULLIF(i.camera_o_senato, '') IS NOT NULL
      AND i.atto_num IS NOT NULL
),

edges_itercost_atto AS (
    SELECT
        p.source_id,
        'proposta_cost' AS relation,
        p.target_id,
        1 AS weight,
        NULL::INTEGER AS source_year,  -- H2: legislatura ≠ anno
        NULL::INTEGER AS target_year,
        LEFT(COALESCE(p.stato, p.proponente, ''), 200) AS evidence
    FROM itercost_proposta p
    WHERE EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = p.source_id
      )
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = p.target_id
            AND (
                LOWER(COALESCE(n.title, '')) LIKE '%costituz%'
                OR UPPER(COALESCE(n.tipo, '')) LIKE '%COSTITUZ%'
            )
      )
),

-- Iter Cost.: proposta → legge di revisione (rev_urn) se ha_legge
edges_itercost_rev AS (
    SELECT
        p.source_id,
        'diventa_revisione' AS relation,
        'revisione:' || p.rev_urn AS target_id,
        1 AS weight,
        NULL::INTEGER AS source_year,  -- H2: legislatura ≠ anno
        YEAR(p.rev_data) AS target_year,
        LEFT(COALESCE(p.rev_titolo, p.rev_urn, ''), 200) AS evidence
    FROM itercost_proposta p
    WHERE p.ha_legge = 1
      AND NULLIF(p.rev_urn, '') IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = p.source_id
      )
      AND EXISTS (
          SELECT 1 FROM mart_legal_nodes n
          WHERE n.id = 'revisione:' || p.rev_urn
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
        -- Per le coppie presenti sia come titolo sia come citazione vince
        -- l'evidenza del titolo (è il locator: l'atto nomina la delega);
        -- altrimenti resta lo snippet. `MIN` da solo poteva scegliere
        -- `akn:ref` o un URL dal ramo regex e contraddire `from_title`.
        COALESCE(
            MIN(CASE WHEN from_title THEN evidence END),
            MIN(evidence)
        ) AS evidence,
        BOOL_OR(from_title) AS from_title
    FROM (
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_riferimenti
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_citazioni
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_massime
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_parametri
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_promovimento
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_senato
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_camera_leggi
    UNION ALL
    SELECT * FROM edges_delega
    UNION ALL
    SELECT * FROM edges_delega_titolo
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_corpus
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_emend
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_dib
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_abro
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_akn_abroga
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_akn_sost
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_akn_modifiche
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_akn_modifica
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_relatore_sentenza
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_relatore
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_relatore_camera
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_converte_dl
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_firmatario
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_firmatario_sen
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_ponte_persona
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_votazione
    UNION ALL
    SELECT *, NULL::BOOLEAN AS from_title FROM edges_itercost_atto
        UNION ALL
        SELECT *, NULL::BOOLEAN AS from_title FROM edges_itercost_rev
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
        r.fonte_urn AS source_id,
        n.id AS target_id
    FROM read_parquet('{support.riferimenti_urn.path}') r
    LEFT JOIN urn_aliases ua
      ON ua.urn = r.bersaglio_urn
    JOIN mart_legal_nodes n
      ON n.id = COALESCE(ua.node_id, r.bersaglio_urn)
    WHERE r.rel_type = 'citation'
      AND r.clausola_lettura
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
    -- Tipo di evidenza legacy (solo per `riferimento`; NULL altrove).
    CASE
        WHEN g.relation <> 'riferimento' THEN NULL
        WHEN g.evidence IS NULL OR g.evidence = '' OR g.evidence = 'akn:ref'
            THEN 'missing'
        WHEN g.evidence LIKE '%urn:nir:%'
          OR g.evidence LIKE '%i-res/N2Ls%'
          OR g.evidence LIKE '%:costituzione:%'
            THEN 'locator'
        ELSE 'quote'
    END AS evidence_kind,
    -- Provenance evidence per tutte le relazioni (non rompe `evidence_kind`).
    CASE
        WHEN g.evidence IS NULL OR g.evidence = '' THEN 'none'
        WHEN g.relation = 'riferimento' THEN
            CASE
                WHEN g.evidence = 'akn:ref' THEN 'none'
                WHEN g.evidence LIKE '%urn:nir:%'
                  OR g.evidence LIKE '%i-res/N2Ls%'
                  OR g.evidence LIKE '%:costituzione:%'
                    THEN 'locator'
                ELSE 'quote'
            END
        -- Delega dal titolo: il titolo dell'atto è la fonte (locator), non
        -- una citazione. Delega da citazione: lo snippet è la quote.
        WHEN g.relation = 'attua_delega' THEN
            CASE
                WHEN COALESCE(g.from_title, FALSE) THEN 'locator'
                WHEN g.evidence IS NULL OR g.evidence = ''
                     OR g.evidence = 'akn:ref' THEN 'none'
                WHEN g.evidence LIKE '%urn:nir:%'
                  OR g.evidence LIKE '%i-res/N2Ls%'
                  OR g.evidence LIKE '%:costituzione:%' THEN 'locator'
                ELSE 'quote'
            END
        WHEN g.evidence LIKE 'akn:%' THEN 'label'
        ELSE 'quote'
    END AS prov_evidence_kind,
    -- Provenance/assertion: additiva, conservativa, mai `verified` in automatico.
    CASE
        WHEN g.relation = 'riferimento'
          AND (g.evidence IS NULL OR g.evidence = '' OR g.evidence = 'akn:ref')
            THEN 'unverifiable'
        -- Il titolo del D.Lgs nomina la legge delegante: l'edge è estratto
        -- (con locator). La citazione generica resta inferita.
        WHEN g.relation = 'attua_delega' THEN
            CASE WHEN COALESCE(g.from_title, FALSE) THEN 'extracted' ELSE 'inferred' END
        WHEN g.relation IN ('modifica', 'abroga', 'sostituisce', 'split', 'join', 'renumbering')
            -- `abroga` dal mart URN di IC è una regex sul testo con quote:
            -- se l'evidenza c'è, è estratta; senza contesto resta inferita.
            -- (Prima era `inferred` per ogni evidenza non-`akn:`, 3.364 edge
            -- con quote dichiarati non verificabili.)
            THEN CASE
                WHEN g.evidence IS NULL OR g.evidence = '' OR g.evidence = 'akn:ref'
                    THEN 'inferred'
                ELSE 'extracted'
            END
        ELSE 'extracted'
    END AS assertion_type,
    CASE
        WHEN g.relation = 'riferimento' THEN 'akn_ref'
        WHEN g.relation IN ('modifica', 'abroga', 'sostituisce', 'split', 'join', 'renumbering')
            -- `abroga` arriva da due fonti: lifecycle AKN (`evidence` =
            -- `akn:activeModification`) e regex sul testo del mart URN IC
            -- (quote). Il metodo segue l'evidenza, non il nome della
            -- relazione: prima 3.364 regex erano dichiarate `akn_lifecycle`.
            THEN CASE WHEN g.evidence LIKE 'akn:%' THEN 'akn_lifecycle' ELSE 'regex' END
        WHEN g.relation = 'attua_delega'
            THEN CASE WHEN COALESCE(g.from_title, FALSE) THEN 'title' ELSE 'regex' END
        WHEN g.relation IN ('impugna', 'invoca_parametro', 'evoca_parametro') THEN 'massima'
        WHEN g.relation = 'cita_costituzione' THEN 'regex'
        WHEN g.relation = 'converte_decreto_legge' THEN 'join'
        WHEN g.relation IN (
            'diventa_legge', 'firmatario', 'relatore', 'vota', 'emendamento',
            'intervento', 'testo_atto', 'collega', 'relatore_sentenza',
            'proposta_cost', 'diventa_revisione'
        ) THEN 'join'
        ELSE 'unknown'
    END AS method,
    l.locators AS evidence_locator,
    -- C3 (feedback 2026-11-13): `convertito` vs tentata (decaduto/in_esame/restituito).
    ce.esito AS esito,
    CASE
        WHEN g.source_id LIKE 'norma:%' OR g.source_id LIKE 'dl:%'
          OR g.target_id LIKE 'norma:%' OR g.target_id LIKE 'dl:%' THEN 'weak'
        ELSE 'strong'
    END AS conf_identity,
    CASE
        WHEN g.relation = 'riferimento'
          AND (g.evidence IS NULL OR g.evidence = '' OR g.evidence = 'akn:ref')
            THEN 'unknown'
        WHEN g.relation = 'attua_delega'
            THEN CASE WHEN COALESCE(g.from_title, FALSE) THEN 'strong' ELSE 'weak' END
        WHEN g.relation IN ('impugna', 'invoca_parametro', 'evoca_parametro') THEN 'weak'
        ELSE 'strong'
    END AS conf_extraction,
    CASE
        WHEN g.relation = 'riferimento'
          AND (g.evidence IS NULL OR g.evidence = '' OR g.evidence = 'akn:ref')
            THEN 'unknown'
        WHEN g.relation IN (
            'riferimento', 'attua_delega', 'converte_decreto_legge',
            'abroga', 'impugna'
        ) THEN 'weak'
        ELSE 'strong'
    END AS conf_semantic,
    CASE
        WHEN g.source_year BETWEEN 1 AND 30 THEN 'weak'
        WHEN g.relation = 'riferimento'
          AND g.source_year IS NOT NULL AND g.target_year IS NOT NULL
          AND g.source_year < g.target_year - 1 THEN 'weak'
        WHEN g.relation = 'diventa_legge'
          AND g.source_id LIKE 'camera:%'
          AND g.source_year = g.target_year THEN 'weak'
        WHEN g.source_year IS NULL AND g.target_year IS NULL THEN 'unknown'
        WHEN g.relation IN ('firmatario', 'emendamento', 'vota')
          AND g.target_year IS NULL THEN 'weak'
        ELSE 'strong'
    END AS conf_temporal,
    'unreviewed' AS review_status,
    NULL::VARCHAR AS snapshot_id
FROM grouped g
LEFT JOIN conversione_esito ce
  ON  ce.source_id = g.source_id
  AND ce.atto_id = g.target_id
LEFT JOIN akn_locator l
  ON  l.fonte_urn = g.source_id
  AND l.target_urn = g.target_id
  AND l.rel_type = CASE g.relation
        WHEN 'riferimento' THEN 'citation'
        WHEN 'abroga' THEN 'repeal'
        WHEN 'modifica' THEN 'insertion'
        WHEN 'sostituisce' THEN 'substitution'
        ELSE g.relation
      END
-- Ordinamento fisico: DuckDB scrive i row group in ordine di tabella e ne
-- registra min/max. Le query MCP filtrano per id (nodi) e source_id (archi):
-- con i parquet ordinati il row-group pruning salta la maggior parte del file
-- (misurato: lookup nodo 70ms → 12ms, scan archi 52ms → 14ms) e il file è
-- anche più piccolo perché ZSTD comprime meglio dati ordinati.
ORDER BY g.source_id, g.target_id, g.relation
