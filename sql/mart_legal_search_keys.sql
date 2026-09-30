-- mart_legal_search_keys.sql — chiavi di ranking per MCP legal_search
--
-- Dipende da mart_legal_nodes (stessa sessione MART, ordine in dataset.yml).
-- Colonne pensate per 1 query di scoring: id_num/id_year/title_folded/is_major.

WITH folded AS (
    SELECT
        n.*,
        lower(
            translate(
                coalesce(n.title, ''),
                'àáâãäåèéêëìíîïòóôõöùúûüñçÀÁÂÃÄÅÈÉÊËÌÍÎÏÒÓÔÕÖÙÚÛÜÑÇ',
                'aaaaaaeeeeiiiiooooouuuuncAAAAAAEEEEIIIIOOOOOUUUUNC'
            )
        ) AS title_folded,
        lower(
            translate(
                concat_ws(
                    ' ',
                    coalesce(n.title, ''),
                    coalesce(CAST(n.numero AS VARCHAR), ''),
                    coalesce(n.tipo, ''),
                    coalesce(n.collezione, ''),
                    coalesce(n.source_filename, '')
                ),
                'àáâãäåèéêëìíîïòóôõöùúûüñçÀÁÂÃÄÅÈÉÊËÌÍÎÏÒÓÔÕÖÙÚÛÜÑÇ',
                'aaaaaaeeeeiiiiooooouuuuncAAAAAAEEEEIIIIOOOOOUUUUNC'
            )
        ) AS search_text
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
    CASE
        WHEN tipo IN ('DECRETO LEGISLATIVO', 'LEGGE', 'DECRETO-LEGGE', 'DECRETO')
            THEN 1
        WHEN source = 'costituzione' THEN 0
        ELSE 2
    END AS is_major,
    title_folded,
    search_text
FROM folded
