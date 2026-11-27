# Legal Graph — Compose toolkit

Stato: **compose + MCP thin in CI** (2026-10-08). Repo `dataciviclab/legal-graph`.

## Cosa è

Compose **mart-only toolkit** (modello `pil-intelligence`).
Nodi, archi e tabelle derivate da rete (GitHub raw / GCS HTTPS), senza clonare i repo dati.

| Pezzo | Dove |
|---|---|
| Config | `compose/legal-graph/dataset.yml` (ordine tabelle obbligatorio) |
| Config indice | `compose/legal-index/dataset.yml` (solo `mart_legal_search_terms`) |
| SQL base | `compose/legal-graph/sql/mart_legal_nodes.sql`, `.../mart_legal_edges.sql` |
| SQL derivate | alias · relation_type · coverage · metrics · search_keys · node_rel · emend_leg · **texts** · **massime** |
| Indice BM25 | `compose/legal-index/sql/mart_legal_search_terms.sql` — dataset separato per memoria |
| Output | `out/data/mart/legal_graph/2026/` + `out/data/mart/legal_texts/2026/` + `out/data/mart/legal_index/2026/` (gitignored) |
| Pubblico | GCS `dataciviclab-mart/legal-graph/` — `legal_graph/2026/` (11 tabelle, con freshness) + `legal_texts/2026/` (2) + `legal_index/2026/` (1) |
| Package | `legal_graph/` — MCP 7 tool + paths + legal_text |
| CLI | — (il layer legacy `data/*.parquet` è stato rimosso) |
| Test | `tests/` — integrità + golden search |

### Perché tre dataset

**`legal_texts` (testi e massime).** `mart_legal_texts` + `mart_legal_massime`
sono 88 MB su 198 MB del compose e non leggono nodi o archi: dipendono solo
dai clean di costituzione-italiana. Da soli valgono 5,8 s e **1,64 GB** di
picco (massime: 2,0 s e 0,38 GB), il maggiore contributo singolo al picco di
`run-graph` (2,37 GB). Isolarli rende il grafo costruibile su macchine da
4 GB e non ripubblica 88 MB a ogni fix di SQL del grafo. `make run` li
costruisce comunque, ma il dataset è indipendente e parallelizzabile.

**`legal_index` (indice BM25).**

`mart_legal_search_terms` aggrega ~62M token prima di ridurli a 7,2M coppie
(atto, stem): è il picco di memoria di tutto il compose e, insieme a nodi e
archi, non entra in una macchina da 8GB. Isolato in `legal_index`:

- il build del grafo scende a un budget suo (`GRAPH_MEMORY`, default 2GB);
- l'indice si ricostruisce solo quando cambia il corpus, non a ogni fix di SQL;
- `legal_index` legge `search_keys` dal grafo **in locale** (support
  `type: dataset`), non dalla copia pubblicata: niente staleness.

Ordine obbligato: `make run` = `run-graph` → `run-index`.

Misure reali (macchina 4,9GB, `preserve_insertion_order=false`; struttura
attuale 2026-11-27, `DUCKDB_MEMORY_LIMIT=1,5GB`, 2 thread):

| Target | Esito | Picco RSS | Tempo |
|---|---|---|---|
| `run-graph` + 2 staging (2026-11-27) | ✅ 7.076.079 righe | **1,77 GB** | 2 m 23 s |
| `run-graph` prima dello split (2026-11-23, 12 tabelle) | ✅ 6.532.318 righe | 2,37 GB | 80 s |
| `run-texts` (2 tabelle, isolato) | ✅ 69.266 righe | **1,59 GB** | 10 s |
| `run-index` originale, 2GB | ❌ ucciso | **3,13 GB** | 17 s CPU |
| `run-index` ottimizzata, 2GB | ❌ OOM pulito di DuckDB | 2,44 GB | — |
| `run-index` ottimizzata, 3GB + 3 thread | ✅ **7.918.259 righe** | **3,20 GB** | 59 s |

Il picco del grafo scende perché testi+massime (1,6+0,4 GB da soli) non sono
più nel suo run. L'indice resta il picco assoluto: su una macchina <8GB si usa
`make run-local` (grafo+testi in locale, indice dall'ultimo publish).

### Costi di lettura (misurati 2026-11-27)

Metodo: `EXPLAIN ANALYZE` + `HTTPFS HTTP Stats` con le colonne realmente
usate, una connessione per lettura (la full-scan del file è un limite
superiore: DuckDB legge solo le colonne proiettate).

| Support | Prima | Dopo | Δ |
|---|---|---|---|
| `riferimenti_urn` (8 letture) | 52,9 MiB | **13,7** (stg) | −39,2 |
| `senato_emendamenti` (3 letture) | 78,8 MiB | **28,2** (mart + stg) | −50,6 |
| `normativa` per l'identità | 2 letture | **1** (nodi) | −1,6 |
| `pronunce` (testo) | 76,8 nel grafo | 76,8 in `legal_texts` | fuori dal grafo |

Totale: **~−91 MiB/run** sui 521 MiB misurati dall'audit compose (−17%).
L'indice (`testi`, 82,9 MiB) è invariato; `mart_riferimenti_urn` (13,7 MiB)
resta il file più riletto, ora una volta sola.

L'indice non stava in 4GB con la query originale: `regexp_extract_all` +
`UNNEST` materializzano le liste fuori dal buffer manager, quindi
`memory_limit` non le contiene e non c'è spill. Tre modifiche (equivalenti,
verificate riga per riga su campioni da 300/400/1000 atti, Δweight = 0):

1. **`doc_id` intero** al posto dell'URN in tutte le fasi di tokenizzazione:
   il GROUP BY da ~8M coppie teneva la stringa (~40 byte) ripetuta.
2. **`doc_len` da `tf`** (`SUM(tf)` per atto = `SUM(w)` per atto): il codice
   rieseguiva l'intera catena `UNNEST`, perché i CTE DuckDB sono inlined.
3. **Una `regexp_replace` con alternanza** invece di tre in cascata.

Il passo decisivo però è stato il budget: `INDEX_MEMORY=3GB` e
`INDEX_THREADS=3` (4 thread alzano il picco oltre il tetto).

Su una macchina che non regge nemmeno 3,2 GB:

```bash
make run-local     # = run-graph + download-index (indice dall'ultimo publish)
```

L'indice cambia solo quando cambia il corpus, il grafo a ogni fix di SQL:
è il pezzo giusto da prendere da GCS invece di ricostruirlo.

## Mart (ordine compose)

```text
0  stg_riferimenti          ← una lettura di mart_riferimenti_urn (13,7 MiB)
                              usata da nodi, archi e copertura: prima 8
                              scansioni = 52,9 MiB/run. `internal: true`
0b stg_emendamenti          ← mart `senato-akn.mart_emendamenti` (grano
                              emendamento, 28 MB) letto una volta sola:
                              prima 3 scansioni del clean = 78,8 MiB/run
1 mart_legal_nodes          ← support esterni (+ nodi Camera/Senato da
                              open-politica `atti` e `persone`; eiv da akn_act_meta,
                              nodi dl:* da atti.mart_dl,
                              **nodi catalogo** da atti_index per gli atti citati
                              fuori corpus: solo identità, nessun testo;
                              **nodi UE** `celex:*` da `direttive_ue`: una riga per
                              CELEX, le due grafie URN collassano sullo stesso nodo)
2 mart_legal_edges          ← support esterni + stg + identità da nodes
                              (tipo/oggetto/numero/anno degli atti da
                              mart_legal_nodes, non dal clean;
                              attua_delega: `method='title'` letta dal titolo
                              dell'atto, `regex` dedotta da una citazione;
                              modifiche AKN, relatore_sentenza, relatore Camera,
                              firmatario, converte_decreto_legge)
                              + abroga dal mart URN di italia-corpus
                              + `riferimento` verso le direttive UE (alias URN→CELEX)
2b mart_legal_edges_in      ← FROM mart_legal_edges, stesse righe ordinate per
                              target_id (query in entrata con row-group pruning)
3 mart_legal_coverage       ← FROM mart_legal_edges + support: righe upstream
                              → coppie distinte → edge (funnel per relazione)
4 mart_legal_node_metrics   ← FROM mart_legal_nodes + edges
5 mart_legal_search_keys    ← FROM mart_legal_nodes
5b mart_legal_search_terms   ← FROM search_keys + testi IC (indice BM25)
6 mart_legal_node_rel       ← FROM mart_legal_edges
7 mart_legal_emend_leg      ← FROM mart_legal_edges
```

`mart_legal_texts` e `mart_legal_massime` vivono in `compose/legal-texts/`
(dataset `legal_texts`): payload di testo e picco di memoria isolati.

Le tabelle derivate leggono le precedenti **nella stessa sessione DuckDB** del run MART.
Le `stg_*` sono `internal: true`: il parquet resta in `out/` per debug e run
parziali, ma non entra in `mart_refs`/`marts` del registry e non va su GCS.

L'ordine `nodes → edges` è un **contratto**, non una comodità: `mart_legal_edges`
fa `EXISTS (... FROM mart_legal_nodes)` per emettere solo edge con endpoint
esistenti (`testo_atto`, `diventa_legge`, `emendamento`, `relatore`, `vota`,
`diventa_revisione`). Invertire le due tabelle in `dataset.yml` interrompe il
build — rumorosamente, non in silenzio. Lo stesso vale per `coverage`, `metrics`
e `node_rel`, che leggono gli edge già materializzati.

`mart_legal_coverage` è il bilancio del compose: per ogni relazione dichiara
quante righe upstream entrano, quante diventano coppie distinte, quanti edge
arrivano nel mart. Gli `edges` sono letti da `mart_legal_edges` (non
ricalcolati), quindi il bilancio non può divergere dal grafo pubblicato.

**Semantica `source='ue'`**: `stato` è `vigente`/`abrogato` da `in_vigore` di
Cellar e `vigenza_fonte='cellar'`; non è la vigenza italiana del corpus
(`source='normativa'`). `legal_text` restituisce `note` + `eurlex_url`/`eli_url`
(nessun `error`): il testo resta su EUR-Lex.

## Come si usa

```bash
make run              # compose da rete → 10 mart grafo + 2 testi + 1 indice
make test             # integrità + golden + E2E di contratto
make lint
```

> **Memoria**: `make run` usa `DUCKDB_MEMORY_LIMIT=4GB` (serve all'indice
> lessicale). Su una macchina con meno di ~8GB liberi il compose va in OOM e
> porta giù la sessione, non solo il processo. Lì la build va lasciata alla CI
> (`pipeline.yml`, ~3 minuti); in locale si eseguono solo `make test` sul mart
> già pubblicato (`make download-gcs`).

Le fonti si leggono **HTTPS dirette** — nessun clone locale.

> **Artefatti upstream nuovi**: se `italia-corpus` aggiunge una colonna a un
> support (es. `articolo` in `mart_riferimenti_urn`), il build locale usa il
> parquet in `out/` e fallisce con un **binder error** che sembra un bug del
> codice. Ordine giusto: `make download-gcs` **prima** di `make run`. Stessa
> cosa per l'MCP: le view sono create al bootstrap, dopo `make run` va
> riavviato.

## MCP (thin)

| Tool | Cosa fa |
|---|---|
| `legal_search` | intent (numero/anno, ECLI, Cost., data, frase) → 1 SQL su search_keys+metrics; per i temi join su `search_terms` (BM25 + hub del grafo); filtri: tipo, anno, source, stato, vigenza_fonte, materia, min_score, collezione |
| `legal_node` | overview · chain (con modifiche AKN) · jurisprudence (con relatore) · parliament (con firmatari) — usano node_rel/emend_leg |
| `legal_text` | testo normativa (fetch rete) |
| `legal_neighbors` | vicini in/out, filtro relazione o canonica |
| `legal_coverage` | funnel per relazione + livelli L1-L2 |
| `legal_query` | SELECT/PRAGMA/DESCRIBE escape hatch |
| `legal_insights` | report da metrics mart |

Se manca `search_keys`/`metrics`: errore + hint `make run`.

## Sorgente dei dati e runtime

MCP e test risolvono i mart in quest'ordine: **locale `out/` → env `LEGAL_GRAPH_MART_DIR` → GCS HTTPS**.

| Variabile | Default | Effetto |
|---|---|---|
| `LEGAL_GRAPH_SOURCE` | `auto` | `auto`: locale se esiste, altrimenti GCS · `local`: solo build locale, nessun fallback · `gcs`: solo GCS, anche con build locale (evita di servire dati vecchi) |
| `LEGAL_GRAPH_MART_DIR` | — | path esplicito dei mart (sostituisce l'autodetect di `out/`) |
| `LEGAL_GRAPH_THREADS` | `min(4, cpu)` | thread per query; `legal_search` è uno scan di `search_keys` (numeri/titoli) o un lookup su `search_terms` (temi, ~30-80ms) |
| `LEGAL_GRAPH_MEMORY_LIMIT` | `1GB` | tetto di memoria per connessione (non prenotata: RSS reale ~350MB); da alzare se materializzi i mart nella stessa connessione |
| `LEGAL_GRAPH_REQUIRE_MART` | assente | `1` → mart mancante è un errore, non uno skip (CI) |

Con i mart come VIEW sui parquet, 1GB è un tetto comodo, non una prenotazione. Con 256MB la memoria si esaurisce dopo qualche query pesante: in OOM la search risponde con un errore esplicito, non con una lista vuota.

Le view sui mart sono create al bootstrap della connessione: dopo un `make run` che aggiunge tabelle nuove serve **riavviare** l'MCP (il refresh per chiamata è stato tolto: costava ~50ms di query di catalogo su ogni tool).

## CI

| Workflow | Trigger | Cosa fa |
|---|---|---|
| `ci.yml` | PR + push main | preflight + lint (no build mart — non sono più in git) |
| `pipeline.yml` | lun 06:00 UTC, push compose/** | `make run` (graph → index) + intelligence + **test** + sync GCS + registry PR |

I mart **non** vengono più committati in git. I test sul grafo girano in pipeline (o localmente). Il publish GCS include tutte e 10 le tabelle, texts e massime comprese (dal build 2026-11-09): `LEGAL_GRAPH_SOURCE=auto|local|gcs` sceglie la sorgente (default `auto`: locale → GCS).

Locale == CI: stessi target Makefile.

## Cosa è pronto / cosa no

| Area | Stato |
|---|---|
| Compose mart + MCP thin | ✅ 13 tabelle su 3 dataset — grafo (nodes, edges, entity_alias, relation_type, edges_in, coverage, metrics, search_keys, rel, emend_leg) · testi (texts, massime) · indice (search_terms) |
| Golden test ranking | ✅ |
| LICENSE MIT | ✅ |
| CI org | ✅ Actions + pipeline → GCS + registry PR |
| Copertura onesta | ✅ [docs/COVERAGE.md](docs/COVERAGE.md) |
| Bank analisi / Discussion | ✅ [docs/ANALISI.md](docs/ANALISI.md) |
| Direttive UE | ✅ 2.343 nodi `celex:*`, 28.128 edge `riferimento`; testo su EUR-Lex |
| Repo pubblico | ✅ · MCP nel template org ⏳ |
| Hub FOIA 33/2013 e 241/1990 | ✅ nodi nel grafo, con citazioni in ingresso (bersagli fuori corpus agganciati per URN); 33/2013: 157, 241/1990: 1.156 |
| Codici c.c./c.p. navigabili | ❌ gap |
| Ricerca full-text (BM25 su `testi`) / giurisprudenza ordinaria / PNRR dataset | ✅ indice · ❌ giurisprudenza ordinaria e PNRR fuori grafo |

## Prossimi passi

1. Discussion attive su questo repo come strumento di analisi (bank ANALISI.md)
2. MCP nel template org
3. Hub Lab: 33/2013, 241/1990, 190/2012 cercabili
4. Dopo: explorer `normativa`, dataset PNRR dedicato, non “Normattiva in grafo”
