-- mart_legal_node_rel.sql — pre-aggregato relazioni per vista MCP
--
-- (id, relation) con conteggi e pesi: usato da legal_node views
-- senza ricalcolare COUNT(*) su edges a ogni chiamata.
-- Dipende da mart_legal_edges (ordine dataset.yml).

SELECT
    id,
    relation,
    SUM(n_in) AS n_in,
    SUM(n_out) AS n_out,
    SUM(weight_in) AS weight_in,
    SUM(weight_out) AS weight_out
FROM (
    SELECT
        target_id AS id,
        relation,
        COUNT(*) AS n_in,
        0 AS n_out,
        SUM(weight) AS weight_in,
        0 AS weight_out
    FROM mart_legal_edges
    GROUP BY 1, 2
    UNION ALL
    SELECT
        source_id AS id,
        relation,
        0 AS n_in,
        COUNT(*) AS n_out,
        0 AS weight_in,
        SUM(weight) AS weight_out
    FROM mart_legal_edges
    GROUP BY 1, 2
)
GROUP BY id, relation
-- Ordinamento fisico: DuckDB scrive i row group in ordine di tabella e ne
-- registra min/max. Le query MCP filtrano per id (nodi) e source_id (archi):
-- con i parquet ordinati il row-group pruning salta la maggior parte del file
-- (misurato: lookup nodo 70ms → 12ms, scan archi 52ms → 14ms) e il file è
-- anche più piccolo perché ZSTD comprime meglio dati ordinati.
ORDER BY id, relation
