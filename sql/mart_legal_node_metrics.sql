-- mart_legal_node_metrics.sql — intelligence layer nel compose
--
-- Legge le tabelle DuckDB create prima nel run MART
-- (mart_legal_nodes, mart_legal_edges) — ordine in dataset.yml obbligatorio.
-- Stesso output storico di scripts/graph_intelligence.py (backward-compat MCP).

WITH incoming AS (
    SELECT
        target_id,
        COUNT(*) AS referenced_by,
        SUM(weight) AS impact_score,
        MAX(source_year) AS last_referenced_year,
        MIN(source_year) AS first_referenced_year,
        COUNT(DISTINCT source_id) AS distinct_sources,
        COUNT(DISTINCT relation) AS relation_types
    FROM mart_legal_edges
    WHERE relation IN (
        'riferimento', 'cita_costituzione', 'impugna', 'invoca_parametro',
        'diventa_legge', 'recepisce_direttiva', 'attua_regolamento',
        'evoca_parametro', 'abroga', 'attua_delega'
    )
    GROUP BY target_id
),

outgoing AS (
    SELECT
        source_id,
        COUNT(*) AS references,
        SUM(weight) AS complexity_score,
        COUNT(DISTINCT target_id) AS distinct_targets,
        MAX(target_year) AS newest_reference,
        MIN(target_year) AS oldest_reference
    FROM mart_legal_edges
    WHERE relation IN (
        'riferimento', 'cita_costituzione', 'impugna', 'invoca_parametro',
        'diventa_legge', 'recepisce_direttiva', 'attua_regolamento',
        'evoca_parametro', 'abroga', 'attua_delega'
    )
    GROUP BY source_id
)

SELECT
    n.id,
    n.tipo,
    n.title,
    n.data,
    n.anno,
    n.source,
    COALESCE(i.referenced_by, 0) AS referenced_by,
    COALESCE(i.impact_score, 0) AS impact_score,
    i.last_referenced_year,
    i.first_referenced_year,
    COALESCE(i.distinct_sources, 0) AS distinct_sources,
    COALESCE(i.relation_types, 0) AS relation_types,
    COALESCE(o.references, 0) AS "references",
    COALESCE(o.complexity_score, 0) AS complexity_score,
    COALESCE(o.distinct_targets, 0) AS distinct_targets,
    o.newest_reference,
    o.oldest_reference,
    CASE WHEN n.anno IS NOT NULL THEN {year} - n.anno ELSE NULL END AS age_years,
    CASE
        WHEN COALESCE(i.referenced_by, 0) >= 100 THEN 'critical'
        WHEN COALESCE(i.referenced_by, 0) >= 50 THEN 'important'
        WHEN COALESCE(i.referenced_by, 0) >= 10 THEN 'moderate'
        ELSE 'minor'
    END AS impact_level,
    CASE
        WHEN COALESCE(o.references, 0) >= 200 THEN 'very_complex'
        WHEN COALESCE(o.references, 0) >= 100 THEN 'complex'
        WHEN COALESCE(o.references, 0) >= 50 THEN 'moderate_complexity'
        ELSE NULL
    END AS complexity_level,
    CASE
        WHEN ({year} - n.anno) >= 50 AND COALESCE(i.referenced_by, 0) >= 10 THEN 'obsolete_candidate'
        WHEN ({year} - n.anno) >= 30 AND COALESCE(i.referenced_by, 0) >= 50 THEN 'aging'
        ELSE NULL
    END AS age_risk,
    CASE
        WHEN i.last_referenced_year IS NOT NULL AND i.last_referenced_year < 2010 THEN 'dormant'
        WHEN i.last_referenced_year IS NOT NULL AND i.last_referenced_year < 2015 THEN 'declining'
        ELSE NULL
    END AS activity_level
FROM mart_legal_nodes n
LEFT JOIN incoming i ON n.id = i.target_id
LEFT JOIN outgoing o ON n.id = o.source_id
