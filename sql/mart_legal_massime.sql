-- mart_legal_massime.sql — massime Corte Cost. (chiavi + testo troncato)
--
-- Una riga per massima (~46k): testo limitato a 600 char per search/query leggeri.
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
    -- Attesto di appartenenza del parametro: 25 = Costituzione (codice Corte
    -- Cost.). Serve a verificare il filtro di `invoca_parametro` in
    -- mart_legal_edges.sql, che tiene solo i parametri costituzionali.
    -- Una riga per massima (il clean non è più cartesiano): il primo
    -- parametro/norma della lista è il rappresentante nella vista "massime".
    -- Gli edge usano i mart esplosi (massime_parametri/norme).
    json_extract_string(parametri, '$[0].codice') AS parametro_codice,
    json_extract_string(parametri, '$[0].descrizione') AS parametro_descrizione,
    TRY_CAST(json_extract_string(parametri, '$[0].articolo') AS INTEGER) AS parametro_articolo,
    json_extract_string(parametri, '$[0].comma') AS parametro_comma,
    json_extract_string(norme, '$[0].descrizione') AS norma_descrizione,
    json_extract_string(norme, '$[0].numero') AS norma_numero,
    json_extract_string(norme, '$[0].data') AS norma_data,
    json_extract_string(norme, '$[0].articolo') AS norma_articolo,
    json_extract_string(norme, '$[0].comma') AS norma_comma
FROM read_parquet('{support.massime_corte_costituzionale.path}')
WHERE anno_pronuncia IS NOT NULL
  AND numero_pronuncia IS NOT NULL
