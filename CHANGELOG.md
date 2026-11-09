# Changelog — Legal Graph

Storia delle modifiche del grafo. I numeri sono quelli del build indicato.

## 2026-11-09

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
