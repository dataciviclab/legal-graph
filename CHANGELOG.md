# Changelog — Legal Graph

Storia delle modifiche del grafo. I numeri sono quelli del build indicato.

## 2026-11-10

- **P4 — firmatari Senato e `abroga` onesto** — `senato_firmatari` ha due namespace di identità: oltre a `senatore_id` (11.855 edge) ci sono **20.470 edge `deputato:*`** ricavati da `deputato_url` (25.630 righe prima scartate). Effetto: **−1.409 atti Senato isolati** (22.340 → 20.931). `abroga`: 3.733/3.733 `assertion_type='extracted'` (prima 3.364 `inferred` pur avendo la quote); la mappatura ora guarda l'evidenza, non il prefisso `akn:`. Il buco dei 7.668 abrogati senza arco resta di fonte (AKN non emette lifecycle).
- **P2 — catene `attua_delega` oneste** — l'evidenza dell'edge non è più il titolo della legge bersaglio ma lo **snippet citante**; `method='title'` (471 edge: 290 dal corpus normativa + 181 da `atti_api`) è `assertion_type='extracted'` con `prov_evidence_kind='locator'`, `method='regex'` (2.221) resta `inferred`. Il metodo arriva dal flag `from_title` dei due CTE, non dal peso (il peso regex è il numero di citazioni e può essere ≥5). La vista `chain` espone `method`/`assertion_type`/`prov_evidence_kind`/`evidence`, mette davanti le deleghe lette dal titolo e chiude con le dedotte da citazione. Nessun edge rimosso: 2.692 → 2.692.
- **P1 — ricerca per tema con indice full-text** — nuovo mart **`mart_legal_search_terms`** (13°): una riga per (atto, stem) con peso BM25 precalcolato (titolo 3x) su `italia-corpus/testi` e sui titoli di tutti gli atti navigabili. `legal_search` per tema legge l'indice invece di fare `LIKE` sul titolo; il ranking somma il punteggio testuale a `2·ln(1+citazioni in ingresso)`, mantenendo tipo e vigenza come primi criteri. Copertura minima: 1 termine per query di una parola, 2 per query più lunghe (le parole generiche non fanno tornare mezzo corpus). Senza l'indice (mart legacy) la search ricade sul titolo.
  **Misurato sull'eval temi 2026-11** (24 casi, 4 known-gap esclusi dal rate): 16/24 → **20/24** (MRR 0,708 → 0,875). Restano scoperti L. 241/1990 (senza testo nel corpus), DPR 380/2001 ("permesso di costruire"), e i temi dove più atti sono ugualmente canonici.

## 2026-11-09

- **M0 — contratto e provenance** — `legal_query` read-only (una istruzione, no scritture/file); colonne `assertion_type`, `method`, `prov_evidence_kind`, 4 confidenze, `review_status`, `snapshot_id` su `nodes`/`edges`; view `act`, `relation_type`, `entity_alias`, `coverage_level` L1-L2. `source_snapshot` sospeso (toolkit `metadata.inputs=[]`): nessun hash/URI inventato.
- **M1 — identità Camera** — −**2.590** nodi duplicati `camera:atto:{leg}_{id}` (stesso numero di `camera:{leg}:{num}` al 100%); 4.028 edge `relatore` riagganciati, 0 dangling; **2.593** alias in `mart_legal_entity_alias` (2.590 Camera + 3 `norma:*`; `dl:*` = 0 finché manca l'URN). I vecchi id risolvono con `matched_by="alias"`.
- **M1 — vocabolario** — `mart_legal_relation_type` (25 relazioni, mappatura canonica 25→15, flag `in_chain`/`in_impact`/`in_chain_view`); `mart_legal_node_metrics` e `view=chain` leggono la tabella. Parità metriche: 0 differenze su 857.879 nodi. Nuova view `conversione` (933 `dl_to_law` + 3.574 `ddl_to_law`).
- **M1 — tool MCP** — `legal_neighbors` (out/in/both, relazione o canonica) e `legal_coverage` (funnel per relazione + livelli L1-L2). Il compose passa a **12 tabelle**.
- **persone da `open-politica/persone`** — identità unica Camera+Senato; −8.445 nodi (deputati del Regno e della Costituente); restano i 100 che il ponte `collega` usa come bersaglio.
- **`proposta_cost` +1.276** — l'iter Costituzionale Camera puntava a `camera:{num}`, chiave senza legislatura: 0 edge Camera su 1.276 atti.
- **624 nodi catalogo fantoccio rimossi** — le citazioni erano lette dal clean senza edge nel mart; ora i citati vengono dal mart URN.
- **nodi Camera +26.701** — la chiave dell'atto Camera ora include la legislatura: `id_ddl` riparte ogni legislatura (l'atto n. 1 esiste in tutte e 7) e i 34.315 atti Camera collassavano su 7.614 nodi; 0 dangling, 316.406 archi `firmatario` coerenti con la legislatura del firmatario.
- **citation fuori corpus recuperate** — le citazioni ad atti senza testo hanno un nodo `source='catalogo'` dal catalogo Normattiva: **10.852 nodi** (10.629 con almeno un edge; 223 orfani, 2,1%). **10.724** bersagli agganciati (10.594 catalogo + 130 `atti_api`). La Costituzione come atto intero ha il nodo `costituzione:atto` (**4.966** citazioni); L. 241/1990 passa da 0 a **1.156** citazioni in ingresso.
- **canonicalizzazione URN nel build** (#121) — gli URN con chiave concatenata vengono risolti sul clean pubblicato prima di creare i nodi: target concatenati 5.879 → **1.771**, nodi catalogo orfani 763 → **223**. Restano fuori 5.144 direttive UE (issue #124).
- **emendamenti collegati all'atto +54%** — il target è il nodo atto, non solo `senato_ddl`: 103.829 orfani → 22.377, +238.363 archi.
- **`legal_search` ordina per vigenza prima dell'impatto** — la norma in vigore batte l'abrogata più citata.
- **`invoca_parametro` solo su parametri costituzionali** (`parametro_codice=25`) — −13.649 edge che mappavano statuti regionali/codici/CEDU sulla Costituzione.
- **0 target dangling**, un solo nodo per atto Senato (i 2.990 documenti AKN senza omologo open-politica diventano il nodo canonico).

## 2026-10

- evidence sugli edge `riferimento`, +3.666 `impugna` collegati a URN reali, +396 bridge senatore↔deputato, +35.911 `riferimento` dagli AKN, −1.013 autoanelli, nuovo mart `coverage`.
