-- stg_emendamenti.sql — staging interno: una lettura del mart emendamenti
--
-- Sostituisce la lettura del clean `senato-akn` (197 MB, tre scansioni:
-- nodi, archi, copertura = 78,8 MiB/run misurati) con il mart a grano
-- emendamento pubblicato dal produttore (28 MB, una scansione).
--
-- Il mart è già deduplicato su (legislatura, fase, emend_id) e ha già
-- escluso il testo integrale (`text_preview` ≤240 char): il dedup e la
-- scelta del documento non si riscrivono più qui.
--
-- `internal: true` in dataset.yml: parquet in out/ per debug e run parziali,
-- nessun contratto pubblico.

SELECT
    legislatura,
    fase,
    emend_id,
    atto_num,
    n_atti_fase,
    tipologia,
    work_date,
    text_preview,
    document_id,
    text_len
FROM read_parquet('{support.senato_emendamenti.path}')
