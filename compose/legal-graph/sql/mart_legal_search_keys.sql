-- mart_legal_search_keys.sql — chiavi di ranking per MCP legal_search
--
-- Dipende da mart_legal_nodes (stessa sessione MART, ordine in dataset.yml).
-- Colonne pensate per 1 query di scoring: id_num/id_year/title_folded.
-- (`is_major` è stata rimossa 2026-11: il ranking vive in `_base_order`,
--  nessun consumer leggeva la colonna.)
--
-- Ottimizzazione 2026-10: rimossa search_text (title+numero+tipo+collezione+
-- filename). Era ridondante con title_folded + colonne separate. −25 MB.

WITH folded AS (
    SELECT
        n.*,
        lower(
            translate(
                coalesce(n.title, ''),
                'àáâãäåèéêëìíîïòóôõöùúûüñçÀÁÂÃÄÅÈÉÊËÌÍÎÏÒÓÔÕÖÙÚÛÜÑÇ',
                'aaaaaaeeeeiiiiooooouuuuncAAAAAAEEEEIIIIOOOOOUUUUNC'
            )
        ) AS title_folded
    FROM mart_legal_nodes n
)

SELECT
    id,
    tipo,
    title,
    source,
    anno,
    numero,
    collezione,
    -- numero atto: campo numero | URN ;NUM | norma:tipo:NUM:ANNO
    COALESCE(
        NULLIF(CAST(numero AS VARCHAR), ''),
        regexp_extract(id, ';(\d+)$', 1),
        regexp_extract(id, ':(\d+):\d{4}$', 1)
    ) AS id_num,
    -- anno: colonna | URN :YYYY- | suffix :YYYY (norma:legge:40:2004)
    COALESCE(
        CAST(anno AS VARCHAR),
        regexp_extract(id, ':(\d{4})-', 1),
        regexp_extract(id, ':(\d{4})$', 1)
    ) AS id_year,
    title_folded
FROM folded
