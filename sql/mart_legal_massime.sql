-- mart_legal_massime.sql — massime Corte Cost. (chiavi + testo troncato)
--
-- ~267k righe: testo limitato a 600 char per search/query leggeri.
-- Full testo pronunce: mart_legal_texts. ecli costruito da anno/numero.

SELECT
    'sentenza:' || CAST(anno_pronuncia AS VARCHAR) || '-'
        || LPAD(CAST(numero_pronuncia AS VARCHAR), 4, '0') AS sentenza_id,
    'ECLI:IT:COST:' || CAST(anno_pronuncia AS VARCHAR) || ':'
        || CAST(numero_pronuncia AS VARCHAR) AS ecli,
    id_massima,
    tipologia_pronuncia,
    esito,
    titolo,
    LEFT(COALESCE(testo, ''), 600) AS testo,
    parametro_articolo,
    parametro_comma,
    norma_descrizione,
    CAST(norma_numero AS VARCHAR) AS norma_numero,
    CAST(norma_data AS VARCHAR) AS norma_data,
    norma_articolo,
    norma_comma
FROM read_parquet('{support.massime_corte_costituzionale.path}')
WHERE anno_pronuncia IS NOT NULL
  AND numero_pronuncia IS NOT NULL
