-- mart_legal_relation_type.sql — vocabolario relazioni (M1)
--
-- Una riga per relazione del grafo (25). Sostituisce le liste hardcoded di
-- `mart_legal_node_metrics` (impatto) e di `legal_node(view='chain')`
-- (traversal). `canonical_relation` è la riduzione 25→15 usata dai consumer
-- che non vogliono distinguere i sottotipi; `relation` resta la chiave
-- pubblica e non cambia.
--
-- Flag:
--   in_chain        relazione della catena normativa (modello)
--   in_chain_view   relazione attraversata da legal_node(view='chain')
--                   (esclude `riferimento`: 221k edge, resta nel fallback
--                    top_riferimenti; e `modifica`/`impugna`, mostrate
--                    nelle rispettive viste)
--   in_impact       entra in referenced_by/impact_score/references
--
-- Non è un contratto "vero per sempre": se il modello cambia, cambia qui e
-- i consumer leggono la tabella. Nessun consumer deve avere una seconda lista.

SELECT * FROM (VALUES
    -- relation, family, domain, range, granularity, directed, temporal,
    -- inferable, reliability, in_chain, in_impact, in_chain_view,
    -- canonical_relation, description
    ('riferimento', 'norm', 'act', 'act', 'article', false, false, true, 'weak', true, true, false, 'cita', 'citazione testuale'),
    ('cita_costituzione', 'norm', 'act', 'provision', 'article', false, false, true, 'strong', true, true, true, 'cita', 'citazione Costituzione'),
    ('modifica', 'norm', 'act', 'act', 'article', true, true, true, 'strong', true, false, false, 'modifica', 'modifica attiva'),
    ('abroga', 'norm', 'act', 'act', 'article', true, true, true, 'strong', true, true, true, 'abroga', 'abrogazione'),
    ('sostituisce', 'norm', 'act', 'act', 'article', true, true, true, 'strong', true, false, true, 'modifica', 'sostituzione'),
    ('split', 'norm', 'act', 'act', 'article', true, true, true, 'strong', true, false, true, 'riordino', 'split AKN'),
    ('join', 'norm', 'act', 'act', 'article', true, true, true, 'strong', true, false, true, 'riordino', 'join AKN'),
    ('renumbering', 'norm', 'act', 'act', 'article', true, true, true, 'strong', true, false, true, 'riordino', 'rinumerazione AKN'),
    ('attua_delega', 'norm', 'act', 'act', 'article', true, false, true, 'weak', true, true, true, 'attua', 'attuazione di delega (euristica)'),
    ('converte_decreto_legge', 'proc', 'act', 'act', 'act', false, false, false, 'strong', true, false, true, 'conversione', 'conversione tentata'),
    ('diventa_legge', 'proc', 'act', 'act', 'act', false, false, false, 'strong', true, true, true, 'conversione', 'DDL → legge'),
    ('impugna', 'juris', 'act', 'act', 'article', false, false, false, 'strong', true, true, false, 'impugna', 'impugnazione'),
    ('invoca_parametro', 'juris', 'act', 'provision', 'article', false, false, false, 'strong', true, true, false, 'parametro', 'parametro costituzionale'),
    ('evoca_parametro', 'juris', 'act', 'provision', 'article', false, false, false, 'strong', true, true, false, 'parametro', 'parametro da promovimento'),
    ('relatore_sentenza', 'juris', 'act', 'person', 'act', false, false, false, 'strong', false, false, false, 'relatore', 'relatore sentenza'),
    ('emendamento', 'proc', 'document', 'act', 'act', false, false, false, 'strong', false, false, false, 'emendamento', 'emendamento'),
    ('firmatario', 'proc', 'person', 'act', 'act', false, false, false, 'strong', false, false, false, 'firmatario', 'firma'),
    ('relatore', 'proc', 'person', 'act', 'act', false, false, false, 'strong', false, false, false, 'relatore', 'relatore'),
    ('vota', 'proc', 'vote', 'act', 'act', false, false, false, 'strong', false, false, false, 'voto', 'voto'),
    ('intervento', 'proc', 'document', 'person', 'act', false, false, false, 'strong', false, false, false, 'documento', 'intervento'),
    ('testo_atto', 'doc', 'document', 'act', 'act', false, false, false, 'strong', false, false, false, 'documento', 'documento → atto'),
    ('proposta_cost', 'proc', 'act', 'act', 'act', false, false, false, 'strong', false, false, false, 'iter_costituzionale', 'proposta costituzionale'),
    ('diventa_revisione', 'proc', 'act', 'act', 'act', false, false, false, 'strong', false, false, false, 'iter_costituzionale', 'proposta → revisione'),
    ('collega', 'identity', 'person', 'person', 'act', false, false, false, 'strong', false, false, false, 'identita', 'ponte persona'),
    ('referenzia_pnrr', 'program', 'act', 'program', 'act', false, false, false, 'weak', false, false, false, 'cita', 'riferimento PNRR')
) AS t(relation, family, domain, range, granularity, directed, temporal,
       inferable, reliability, in_chain, in_impact, in_chain_view,
       canonical_relation, description)
