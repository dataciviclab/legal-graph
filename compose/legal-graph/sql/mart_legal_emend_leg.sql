-- mart_legal_emend_leg.sql — emendamenti per DDL e legislatura
--
-- Pre-aggregato per vista parliament: COUNT stabili + campioni coerenti.
-- Dipende da mart_legal_edges (ordine dataset.yml).

SELECT
    target_id,
    CAST(regexp_extract(source_id, 'senato:emend:(\d+):', 1) AS INTEGER) AS legislatura,
    COUNT(*) AS n_emend,
    MIN(source_id) AS sample_emend_id
FROM mart_legal_edges
WHERE relation = 'emendamento'
  AND target_id IS NOT NULL
GROUP BY 1, 2
