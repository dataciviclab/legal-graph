-- mart_legal_edges_in.sql — mart_legal_edges ordinato per target_id
--
-- Stessa tabella (stesse righe, stesse colonne) dell'archi, in un layout
-- fisico diverso. Serve alle query "in entrata": la vista jurisprudence (chi
-- cita/impugna questo nodo), parliament (emendamenti/interventi di un atto),
-- overview (top_incoming) e il ramo backward della chain filtrano tutte per
-- `target_id`.
--
-- DuckDB registra min/max per row group: con i parquet ordinati il pruning
-- salta la maggior parte del file. Misurato sui dati reali: filtro
-- `target_id = ?` 50ms → 14ms, jurisprudence 348ms → ~60ms.
-- L'alternativa (indice ART) richiede una tabella in RAM (~100MB per
-- processo): per un MCP stdio non conviene.
--
-- Le due copie costano meno di una: ordinate, ZSTD le comprime meglio
-- (22,3MB non ordinate → 10,7 + 10,7MB ordinate).

SELECT *
FROM mart_legal_edges
ORDER BY target_id, source_id, relation
