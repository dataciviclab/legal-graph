-- mart_legal_texts.sql — testi da mart (senza fetch rete a runtime)
--
-- Ordine: dopo mart_legal_nodes (e support già dichiarati in dataset.yml).
-- Copre: articoli Cost. (testo) + pronunce Corte (testo/dispositivo).
-- `esito` rimossa 2026-11: era NULL su tutte le righe, nessun consumer.
-- Massime: vedi mart_legal_massime (campi chiave per search/query).

WITH articoli AS (
    SELECT
        'costituzione:art:' || CAST(a.articolo AS VARCHAR) AS id,
        'articolo' AS kind,
        a.heading AS title,
        a.testo AS testo,
        NULL::VARCHAR AS dispositivo,
        NULL::VARCHAR AS ecli
    FROM read_parquet('{support.articoli_costituzione.path}') a
    WHERE a.articolo IS NOT NULL AND NULLIF(a.testo, '') IS NOT NULL
),

pronunce AS (
    SELECT
        'sentenza:' || CAST(p.anno_pronuncia AS VARCHAR) || '-'
            || LPAD(CAST(p.numero_pronuncia AS VARCHAR), 4, '0') AS id,
        'pronuncia' AS kind,
        COALESCE(p.ecli, 'sentenza') AS title,
        p.testo AS testo,
        p.dispositivo AS dispositivo,
        p.ecli AS ecli
    FROM read_parquet('{support.pronunce_corte_costituzionale.path}') p
    WHERE p.anno_pronuncia IS NOT NULL AND p.numero_pronuncia IS NOT NULL
)

SELECT id, kind, title, testo, dispositivo, ecli
FROM articoli
UNION ALL
SELECT id, kind, title, testo, dispositivo, ecli
FROM pronunce
-- Ordinato per `id` perché è l'unica chiave di lettura (`legal_text`): con i
-- dati ordinati i min/max di ogni row group sono strette e il pruning di
-- parquet salta i gruppi che non contengono l'id. Il numero di righe per
-- gruppo si dichiara in dataset.yml (`row_group_size`): senza, le 22.567
-- righe stanno in un unico blocco da 80 MB e un lookup da GCS legge tutto.
ORDER BY id
