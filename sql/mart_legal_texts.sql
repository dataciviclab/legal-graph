-- mart_legal_texts.sql — testi da mart (senza fetch rete a runtime)
--
-- Ordine: dopo mart_legal_nodes (e support già dichiarati in dataset.yml).
-- Copre: articoli Cost. (testo) + pronunce Corte (testo/dispositivo).
-- Massime: vedi mart_legal_massime (campi chiave per search/query).

WITH articoli AS (
    SELECT
        'costituzione:art:' || CAST(a.articolo AS VARCHAR) AS id,
        'articolo' AS kind,
        a.heading AS title,
        a.testo AS testo,
        NULL::VARCHAR AS dispositivo,
        NULL::VARCHAR AS ecli,
        NULL::VARCHAR AS esito
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
        p.ecli AS ecli,
        NULL::VARCHAR AS esito
    FROM read_parquet('{support.pronunce_corte_costituzionale.path}') p
    WHERE p.anno_pronuncia IS NOT NULL AND p.numero_pronuncia IS NOT NULL
)

SELECT id, kind, title, testo, dispositivo, ecli, esito
FROM articoli
UNION ALL
SELECT id, kind, title, testo, dispositivo, ecli, esito
FROM pronunce
