-- mart_legal_entity_alias.sql — alias di identità (M1)
--
-- Contratto (view `entity_alias`, stesse colonne):
--   alias_id · entity_id · alias_type · valid_from · valid_to
--
-- Perché una tabella e non una view: dopo M1 il nodo `camera:atto:{leg}_{id}`
-- non esiste più (l'atto è `camera:{leg}:{id}`). L'alias deve sopravvivere
-- alla rimozione del nodo, quindi si deriva dalla fonte (`camera_relatori`)
-- e non da `nodes`.
--
-- `dl:*` / `norma:decreto legge` → URN restano derivati da `mart_legal_nodes`:
-- sono alias storici delle massime, non entità del processo Camera.
--
-- Regola: un alias esiste solo se l'`entity_id` è un nodo reale. Non si
-- inventano destinazioni.

WITH camera AS (
    SELECT DISTINCT
        'camera:atto:' || r.atto_id_leg AS alias_id,
        'camera:' || split_part(r.atto_id_leg, '_', 1)
            || ':' || split_part(r.atto_id_leg, '_', 2) AS entity_id
    FROM read_parquet({support.camera_relatori.outputs}, union_by_name = true) r
    WHERE regexp_matches(r.atto_id_leg, '^[0-9]+_[0-9]+$')
),

dl_alias AS (
    SELECT DISTINCT
        d.id AS alias_id,
        u.id AS entity_id
    FROM mart_legal_nodes d
    JOIN mart_legal_nodes u
      ON u.id LIKE 'urn:nir:stato:decreto.legge:%'
     AND regexp_extract(d.id, 'dl:(\d{4})-(\d+)$', 1)
         = regexp_extract(
             u.id,
             'urn:nir:stato:decreto.legge:(\d{4})-\d{2}-\d{2};(\d+)$', 1
         )
     AND regexp_extract(d.id, 'dl:(\d{4})-(\d+)$', 2)
         = regexp_extract(
             u.id,
             'urn:nir:stato:decreto.legge:(\d{4})-\d{2}-\d{2};(\d+)$', 2
         )
    WHERE d.id LIKE 'dl:%'
),

norma_alias AS (
    SELECT DISTINCT
        n.id AS alias_id,
        u.id AS entity_id
    FROM mart_legal_nodes n
    JOIN mart_legal_nodes u
      ON u.id LIKE 'urn:nir:stato:decreto.legge:%'
     AND regexp_extract(n.id, 'norma:decreto legge:(\d+):(\d{4})$', 1)
         = regexp_extract(
             u.id,
             'urn:nir:stato:decreto.legge:(\d{4})-\d{2}-\d{2};(\d+)$', 2
         )
     AND regexp_extract(n.id, 'norma:decreto legge:(\d+):(\d{4})$', 2)
         = regexp_extract(
             u.id,
             'urn:nir:stato:decreto.legge:(\d{4})-\d{2}-\d{2};(\d+)$', 1
         )
    WHERE n.id LIKE 'norma:decreto legge:%'
)

SELECT alias_id, entity_id, 'old_id' AS alias_type,
       NULL::VARCHAR AS valid_from, NULL::VARCHAR AS valid_to
FROM camera
WHERE entity_id IN (SELECT id FROM mart_legal_nodes)
UNION
SELECT alias_id, entity_id, 'old_id', NULL::VARCHAR, NULL::VARCHAR
FROM dl_alias
UNION
SELECT alias_id, entity_id, 'old_id', NULL::VARCHAR, NULL::VARCHAR
FROM norma_alias
ORDER BY alias_id
