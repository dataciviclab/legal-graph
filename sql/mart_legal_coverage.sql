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
-- (es. `WHERE r.risolto` in mart_legal_edges.sql) e i test vedono un grafo
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

WITH rif AS (
    SELECT
        count(*) AS upstream_rows,
        count(DISTINCT (regexp_extract(fonte_filename, '/([^/]+)$', 1), bersaglio_filename))
            AS natural_pairs
    FROM read_parquet('{support.riferimenti.path}')
    WHERE risolto
      AND NULLIF(regexp_extract(fonte_filename, '/([^/]+)$', 1), '') IS NOT NULL
),

abro_raw AS (
    SELECT
        count(*) AS upstream_rows,
        count(DISTINCT (abrogating_file, abrogated_year, abrogated_number)) AS natural_pairs
    FROM read_parquet('{support.abrogations_raw.path}')
),

abro_akn AS (
    SELECT
        count(*) AS upstream_rows,
        count(DISTINCT (fonte_urn, target_urn)) AS natural_pairs
    FROM read_parquet('{support.akn_relations.path}')
    WHERE rel_type = 'repeal'
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
    FROM read_parquet('{support.massime_corte_costituzionale.path}')
),

funnel AS (
    SELECT * FROM (VALUES
        (
            'riferimento',
            'italia-corpus.riferimenti',
            (SELECT upstream_rows FROM rif),
            (SELECT natural_pairs FROM rif),
            'Copertura del compose, non della fonte: i riferimenti che italia-corpus non risolve non arrivano nemmeno qui (IC #86). Coppie naturali su filename, prima della mappatura a URN e della guardia temporale.'
        ),
        (
            'abroga',
            'italia-corpus.abrogations_raw + akn_relations(repeal)',
            (SELECT upstream_rows FROM abro_raw) + (SELECT upstream_rows FROM abro_akn),
            (SELECT natural_pairs FROM abro_raw) + (SELECT natural_pairs FROM abro_akn),
            'Target per anno+numero (regex) e per URN (AKN): si perde dove l''atto abrogante/abrogato non è nel corpus o il contesto è ambiguo. Nessun evento AKN estratto (akn_n_repeal_events = 0 upstream).'
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

all_edges AS (
    SELECT
        relation,
        count(*) AS edges,
        -- edge con evidenza formulaica (>50 atti con lo stesso snippet):
        -- restano nel conteggio, il funnel non "migliora" solo perché
        -- sono etichettati.
        count(*) FILTER (WHERE is_formulaic) AS formulaic_edges,
        count(*) FILTER (WHERE is_reading_clause) AS reading_clause_edges,
        -- evidenza assente (`akn:ref`): oggi 56% dei riferimento,
        -- target 0 quando italia-corpus#109 popola il contesto AKN
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
    COALESCE(f.note, '1:1: una riga upstream produce un edge') AS note,
    COALESCE(e.formulaic_edges, 0) AS formulaic_edges,
    ROUND(
        100.0 * COALESCE(e.formulaic_edges, 0) / NULLIF(e.edges, 0), 1
    ) AS formulaic_pct,
    COALESCE(e.reading_clause_edges, 0) AS reading_clause_edges,
    COALESCE(e.missing_evidence_edges, 0) AS missing_evidence_edges,
    ROUND(
        100.0 * COALESCE(e.missing_evidence_edges, 0) / NULLIF(e.edges, 0), 1
    ) AS missing_evidence_pct
FROM all_edges e
FULL OUTER JOIN funnel f ON f.relation = e.relation
ORDER BY edges DESC, relation
