-- stg_riferimenti.sql — staging interno: una lettura di `mart_riferimenti_urn`
--
-- Perché esiste: lo stesso parquet (13,7 MiB) è letto 8 volte in un run —
-- 1 in `mart_legal_nodes` (`cited`), 4 in `mart_legal_edges` (riferimenti,
-- abroga, delega, clausola di lettura) e 3 in `mart_legal_coverage`.
-- Misurato: 52,9 MiB per run. Materializzato una volta qui, le altre tabelle
-- lo leggono dalla stessa sessione DuckDB (ordine in dataset.yml).
--
-- `internal: true` in dataset.yml: il parquet resta in out/ (utile a debug e
-- ai run parziali) ma non è un contratto pubblico e non va nel registry.
--
-- Nessuna trasformazione: è il mart URN-based di italia-corpus, che è già al
-- grain (fonte_urn, bersaglio_urn, rel_type). La normalizzazione delle chiavi
-- resta a monte (una sola fonte di verità).

SELECT * FROM read_parquet('{support.riferimenti_urn.path}')
