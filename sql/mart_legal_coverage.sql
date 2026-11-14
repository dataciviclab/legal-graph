-- mart_legal_coverage.sql — bilancio di copertura del grafo
--
-- Domanda a cui risponde: quante righe upstream entrano in una relazione,
-- quante diventano coppie distinte, quante arrivano nel mart come edge.
--
-- `edge_pct` = edge / coppie distinte (non / righe): le righe upstream
-- contengono gli snapshot della stessa norma, quindi confrontarle con gli
-- edge misura i duplicati, non la copertura. Il denominatore giusto per
-- "quanto entra nel grafo" è la coppia distinta.
--
-- Perché esiste: il compose filtra le righe che non riesce a collegare
-- (es. bersaglio senza nodo in `edges_riferimenti`) e i test vedono un grafo
-- con 0 dangling. Il "buco" non è un errore: è un numero che prima non
-- esisteva. Qui quel numero è esplicito e verificabile.
--
-- Regola: `edges` NON viene ricalcolato — si legge da mart_legal_edges
-- (stessa sessione DuckDB, ordine obbligatorio in dataset.yml). Così il
-- bilancio non può divergere dal grafo pubblicato: se una relazione perde
-- edge, la perdita si vede qui.
--
-- Le espressioni di filtro delle righe upstream replicano volutamente quelle
-- di sql/mart_legal_edges.sql (blocchi edges_riferimenti, edges_abro +
-- edges_akn_abroga, edges_senato + edges_camera_leggi). Se cambi il filtro
-- là, aggiorna anche qui: il resto del funnel è una misura, non una logica.
--
-- Le relazioni senza funnel dichiarato sono 1:1 (una riga upstream → un
-- edge): per loro upstream_rows e natural_pairs restano NULL invece di
-- inventare un denominatore.

-- Denominatore e numeratore nello **stesso spazio di chiavi**: il mart
-- `mart_riferimenti_urn` è già al grain (fonte_urn, bersaglio_urn, rel_type),
-- lo stesso che usa `edges_riferimenti`. Prima del 2026-11 il denominatore
-- contava coppie su filename: rapportarlo agli edge URN non misurava la
-- stessa cosa (i due spazi divergevano con la canonicalizzazione).
WITH rif AS (
    SELECT
        count(*) AS upstream_rows,
        count(DISTINCT (fonte_urn, bersaglio_urn)) AS natural_pairs
    FROM read_parquet('{support.riferimenti_urn.path}')
    WHERE rel_type = 'citation'
),

-- Abroga regex: coppie URN dal mart (`rel_type='abroga'`); la risoluzione
-- anno+numero→URN e la guardia temporale vivono in italia-corpus.
abro_raw AS (
    SELECT
        count(*) AS upstream_rows,
        count(DISTINCT (fonte_urn, bersaglio_urn)) AS natural_pairs
    FROM read_parquet('{support.riferimenti_urn.path}')
    WHERE rel_type = 'abroga'
),

ddl_legge AS (
    -- Una legge può comparire sia come DDL Senato sia come legge Camera:
    -- upstream_rows conta le righe, natural_pairs gli URN distinti.
    SELECT
        count(*) AS upstream_rows,
        count(DISTINCT urn) AS natural_pairs
    FROM (
        SELECT urn_normattiva AS urn
        FROM read_parquet({support.senato_ddl.outputs}, union_by_name = true)
        WHERE NULLIF(urn_normattiva, '') IS NOT NULL
        UNION ALL
        SELECT urn_normattiva AS urn
        FROM read_parquet({support.camera_leggi.outputs}, union_by_name = true)
        WHERE NULLIF(urn_normattiva, '') IS NOT NULL
    )
),

-- Emendamenti: il denominatore sono gli emendamenti distinti
-- (legislatura, **fase**, emend_id), la stessa chiave di `edges_emend`.
-- `emend_id` è il numero dentro l'atto ("1.1" esiste in centinaia di atti):
-- usare `atto_num` (la directory, replicata fino a 57 volte) gonfiava le coppie
-- di 6.982 e sottostimava la copertura (85,8% invece di 86,9%).
emend AS (
    SELECT
        count(*) AS upstream_rows,
        count(DISTINCT (
            CAST(regexp_extract(legislatura, '(\d+)', 1) AS INTEGER),
            fase,
            emend_id
        )) AS natural_pairs
    FROM read_parquet('{support.senato_emendamenti.path}')
    WHERE emend_id IS NOT NULL AND NULLIF(fase, '') IS NOT NULL
),

-- Parametri delle massime: `parametro_codice` dice di quale atto è l'articolo
-- (25 = Costituzione). Il grafo tiene solo i parametri costituzionali: gli
-- altri non hanno un nodo articolo nel corpus. `upstream_rows` conta tutte le
-- righe con un parametro (per vedere il funnel completo), `natural_pairs`
-- solo le coppie costituzionali che possono diventare edge.
parametri_cost AS (
    SELECT
        count(*) FILTER (
            WHERE TRY_CAST(parametro_articolo AS INTEGER) <> 0
        ) AS upstream_rows,
        count(DISTINCT (
            anno_pronuncia,
            numero_pronuncia,
            TRY_CAST(parametro_articolo AS INTEGER)
        )) FILTER (
            WHERE parametro_codice = '25'
              AND TRY_CAST(parametro_articolo AS INTEGER) BETWEEN 1 AND 139
        ) AS natural_pairs
    FROM read_parquet('{support.massime_parametri.path}')
),

funnel AS (
    SELECT * FROM (VALUES
        (
            'riferimento',
            'italia-corpus.riferimenti__mart_riferimenti_urn',
            (SELECT upstream_rows FROM rif),
            (SELECT natural_pairs FROM rif),
            'Denominatore = coppie URN del mart (`rel_type=citation`): stesso spazio di chiavi degli edge. Si perde per guardia temporale e per target senza nodo (direttive UE, URN ambigui/malformati). Il clean filename-based resta solo per i mart analitici.'
        ),
        (
            'abroga',
            'italia-corpus.riferimenti(mart, rel_type=abroga: regex + akn)',
            (SELECT upstream_rows FROM abro_raw),
            (SELECT natural_pairs FROM abro_raw),
            'Regex (abrogations_raw) e AKN repeal (lifecycle) arrivano entrambi dal mart URN: la risoluzione e la guardia vivono in IC. `origine` distingue l''estrattore. Si perde per target senza nodo e guardia temporale.'
        ),
        (
            'diventa_legge',
            'open-politica.senato_ddl + camera_leggi',
            (SELECT upstream_rows FROM ddl_legge),
            (SELECT natural_pairs FROM ddl_legge),
            'Solo DDL/leggi con urn_normattiva: il resto non è collegabile perché l''atto non è nel corpus normativa.'
        ),
        (
            'emendamento',
            'senato-akn.senato_emendamenti',
            (SELECT upstream_rows FROM emend),
            (SELECT natural_pairs FROM emend),
            'Target = atto della fase (`fase + legislatura → senato_ddl.id_ddl`); fallback su `atto_num` solo quando la directory è unica (`n_atti_fase = 1`). Si perde dove la fase non ha un DDL e la directory è replicata (ambiguo) o l''atto è fuori corpus. Denominatore: emendamenti distinti (leg, fase, numero), non nodi.'
        ),
        (
            'invoca_parametro',
            'costituzione-italiana.massime (parametro_codice=25) + atti_promovimento',
            (SELECT upstream_rows FROM parametri_cost),
            (SELECT natural_pairs FROM parametri_cost),
            'Solo parametri costituzionali (parametro_codice 25) e articoli 1..139: gli altri parametri della massima (statuti regionali, codici, CEDU/TFUE) non hanno un nodo articolo nel grafo. Prima del 2026-11 finivano tutti su costituzione:art:N (13.649 edge su 66.368).'
        )
    ) AS t(relation, upstream, upstream_rows, natural_pairs, note)
),

-- Perdite di `riferimento` **per causa**: prima erano un numero solo
-- (`100 - edge_pct`), che mescolava tre lavori diversi. Il denominatore conta
-- coppie URN, quindi qui si spiega perché una coppia non arriva nel grafo:
--
--   temporal              nodo presente, guardia temporale che scarta
--   senza_nodo_in_catalogo atto noto a `atti_index` senza nodo → stub
--   senza_nodo_ue         direttiva UE con CELEX ma senza nodo
--   senza_nodo_fuori_perimetro  fuori da ogni perimetro: si dichiara
--   senza_nodo_in_corpus  dovrebbe essere 0: se non lo è, gli alias URN di
--                         `mart_legal_edges.sql` sono divergenti da qui
--   collapsed             la coppia produce edge ma due URN cadono sullo
--                         stesso nodo (non è una perdita)
--
-- Replica i filtri di `edges_riferimenti` (nodo + guardia temporale): è una
-- misura, non una logica. Se cambi quei filtri là, aggiorna anche qui.
rif_pairs AS (
    SELECT
        r.fonte_urn, r.bersaglio_urn, r.bersaglio_celex, r.esito,
        r.fonte_anno, r.fonte_anno_espressione, r.bersaglio_anno, r.da_regex, r.peso
    FROM read_parquet('{support.riferimenti_urn.path}') r
    WHERE r.rel_type = 'citation'
),

rif_class AS (
    SELECT
        CASE
            WHEN target_id IS NOT NULL AND tempo_ok THEN 'edge'
            WHEN target_id IS NOT NULL THEN 'temporal'
            WHEN esito = 'in_catalogo' THEN 'senza_nodo_in_catalogo'
            WHEN esito = 'dominio_ue' THEN 'senza_nodo_ue'
            WHEN esito = 'fuori_perimetro' THEN 'senza_nodo_fuori_perimetro'
            WHEN esito = 'in_corpus' THEN 'senza_nodo_in_corpus'
            ELSE 'senza_nodo_altro'
        END AS causa
    FROM (
        SELECT
            p.*,
            n.id AS target_id,
            (
                p.fonte_anno_espressione IS NULL
                OR p.bersaglio_anno IS NULL
                OR p.fonte_anno_espressione >= p.bersaglio_anno - 1
                OR (p.da_regex AND p.peso < 5)
            ) AS tempo_ok
        FROM rif_pairs p
        -- Stesso alias di `edges_riferimenti` (una riga, la Costituzione come
        -- atto intero): se diverge, `senza_nodo_in_corpus` lo segnala.
        LEFT JOIN (VALUES (
            'urn:nir:stato:costituzione:1947-12-27;const', 'costituzione:atto'
        )) AS ua(urn, node_id) ON ua.urn = p.bersaglio_urn
        LEFT JOIN mart_legal_nodes n
          ON n.id = COALESCE('celex:' || NULLIF(p.bersaglio_celex, ''), ua.node_id, p.bersaglio_urn)
    )
),

rif_lost AS (
    SELECT
        count(*) FILTER (WHERE causa = 'temporal') AS lost_temporal,
        count(*) FILTER (WHERE causa = 'senza_nodo_in_catalogo') AS lost_in_catalogo,
        count(*) FILTER (WHERE causa = 'senza_nodo_ue') AS lost_dominio_ue,
        count(*) FILTER (WHERE causa = 'senza_nodo_fuori_perimetro') AS lost_fuori_perimetro,
        count(*) FILTER (WHERE causa = 'senza_nodo_in_corpus') AS lost_in_corpus,
        count(*) FILTER (WHERE causa = 'senza_nodo_altro') AS lost_altro,
        count(*) FILTER (WHERE causa = 'edge') - (
            SELECT count(*) FROM mart_legal_edges WHERE relation = 'riferimento'
        ) AS collapsed
    FROM rif_class
),

all_edges AS (
    SELECT
        relation,
        count(*) AS edges,
        -- edge con evidenza formulaica (>50 atti con lo stesso snippet):
        -- restano nel conteggio, il funnel non "migliora" solo perché
        -- sono etichettati.
        count(*) FILTER (WHERE is_formulaic) AS formulaic_edges,
        count(*) FILTER (WHERE is_reading_clause) AS reading_clause_edges,
        -- evidenza assente (`akn:ref`): segnaposto delle citation AKN senza
        -- contesto, target 0 quando italia-corpus#109 popola il contesto AKN.
        -- Dal 2026-11 include le citation fuori corpus (emesse con URN).
        count(*) FILTER (WHERE evidence_kind = 'missing') AS missing_evidence_edges
    FROM mart_legal_edges
    GROUP BY relation
)

SELECT
    COALESCE(e.relation, f.relation) AS relation,
    f.upstream,
    f.upstream_rows,
    f.natural_pairs,
    COALESCE(e.edges, 0) AS edges,
    ROUND(100.0 * COALESCE(e.edges, 0) / NULLIF(f.natural_pairs, 0), 1) AS edge_pct,
    CASE
        WHEN COALESCE(e.relation, f.relation) = 'converte_decreto_legge'
            THEN '1:1; `esito` distingue convertito da tentata (decaduto/in_esame/restituito)'
        ELSE COALESCE(f.note, '1:1: una riga upstream produce un edge')
    END AS note,
    COALESCE(e.formulaic_edges, 0) AS formulaic_edges,
    ROUND(
        100.0 * COALESCE(e.formulaic_edges, 0) / NULLIF(e.edges, 0), 1
    ) AS formulaic_pct,
    COALESCE(e.reading_clause_edges, 0) AS reading_clause_edges,
    COALESCE(e.missing_evidence_edges, 0) AS missing_evidence_edges,
    ROUND(
        100.0 * COALESCE(e.missing_evidence_edges, 0) / NULLIF(e.edges, 0), 1
    ) AS missing_evidence_pct,
    -- Perdite per causa: valorizzate solo per `riferimento` (le altre
    -- relazioni hanno funnel propri o sono 1:1).
    CASE WHEN COALESCE(e.relation, f.relation) = 'riferimento' THEN lc.lost_temporal END AS lost_temporal,
    CASE WHEN COALESCE(e.relation, f.relation) = 'riferimento' THEN lc.lost_in_catalogo END AS lost_in_catalogo,
    CASE WHEN COALESCE(e.relation, f.relation) = 'riferimento' THEN lc.lost_dominio_ue END AS lost_dominio_ue,
    CASE WHEN COALESCE(e.relation, f.relation) = 'riferimento' THEN lc.lost_fuori_perimetro END AS lost_fuori_perimetro,
    CASE WHEN COALESCE(e.relation, f.relation) = 'riferimento' THEN lc.lost_in_corpus END AS lost_in_corpus,
    CASE WHEN COALESCE(e.relation, f.relation) = 'riferimento' THEN lc.lost_altro END AS lost_altro,
    CASE WHEN COALESCE(e.relation, f.relation) = 'riferimento' THEN lc.collapsed END AS collapsed
FROM all_edges e
FULL OUTER JOIN funnel f ON f.relation = e.relation
CROSS JOIN rif_lost lc
ORDER BY edges DESC, relation
