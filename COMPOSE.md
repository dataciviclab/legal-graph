# Legal Graph — Compose toolkit

Stato: **compose + MCP thin in CI** (2026-10-08). Repo `dataciviclab/legal-graph`.

## Cosa è

Compose **mart-only toolkit** (modello `pil-intelligence`).
Nodi, archi e tabelle derivate da rete (GitHub raw / GCS HTTPS), senza clonare i repo dati.

| Pezzo | Dove |
|---|---|
| Config | `dataset.yml` (ordine tabelle obbligatorio) |
| SQL base | `sql/mart_legal_nodes.sql`, `sql/mart_legal_edges.sql` |
| SQL derivate | coverage · metrics · search_keys · node_rel · emend_leg · **texts** · **massime** |
| Output | `out/data/mart/legal_graph/2026/` (gitignored) |
| Pubblico | GCS `dataciviclab-mart/legal-graph/legal_graph/2026/` — 10 tabelle online (build 2026-11-09) |
| Package | `legal_graph/` — MCP 5 tool + paths + legal_text |
| CLI | — (il layer legacy `data/*.parquet` è stato rimosso) |
| Test | `tests/` — integrità + golden search |

## Mart (ordine compose)

```text
1 mart_legal_nodes          ← support esterni (+ nodi Camera/Senato da
                              open-politica `atti` e `persone`; eiv da akn_act_meta,
                              nodi dl:* da atti.mart_dl, camera:atto:* da camera_relatori,
                              **nodi catalogo** da atti_index per gli atti citati
                              fuori corpus: solo identità, nessun testo;
                              **nodi UE** `celex:*` da `direttive_ue`: una riga per
                              CELEX, le due grafie URN collassano sullo stesso nodo)
2 mart_legal_edges          ← support esterni + relazioni tipizzate
                              (attua_delega, modifiche AKN, relatore_sentenza,
                              relatore Camera, firmatario, converte_decreto_legge)
                              + abroga dal mart URN di italia-corpus
                              + `riferimento` verso le direttive UE (alias URN→CELEX)
2b mart_legal_edges_in      ← FROM mart_legal_edges, stesse righe ordinate per
                              target_id (query in entrata con row-group pruning)
3 mart_legal_coverage       ← FROM mart_legal_edges + support: righe upstream
                              → coppie distinte → edge (funnel per relazione)
4 mart_legal_node_metrics   ← FROM mart_legal_nodes + edges
5 mart_legal_search_keys    ← FROM mart_legal_nodes
6 mart_legal_node_rel       ← FROM mart_legal_edges
7 mart_legal_emend_leg      ← FROM mart_legal_edges
8 mart_legal_texts           ← articoli Cost. + pronunce (testo/dispositivo)
9 mart_legal_massime         ← massime (testo troncato 600)
```

Le tabelle derivate leggono le precedenti **nella stessa sessione DuckDB** del run MART.

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

## Come si usa

```bash
make run              # compose da rete → 10 mart
make test             # integrità + golden + E2E di contratto
make lint
```

Le fonti si leggono **HTTPS dirette** — nessun clone locale.

## MCP (thin)

| Tool | Cosa fa |
|---|---|
| `legal_search` | intent (numero/anno, ECLI, Cost., data, frase) → 1 SQL su search_keys+metrics; filtri: tipo, anno, source, stato, vigenza_fonte, materia, min_score, collezione |
| `legal_node` | overview · chain (con modifiche AKN) · jurisprudence (con relatore) · parliament (con firmatari) — usano node_rel/emend_leg |
| `legal_text` | testo normativa (fetch rete) |
| `legal_query` | SELECT/PRAGMA/DESCRIBE escape hatch |
| `legal_insights` | report da metrics mart |

Se manca `search_keys`/`metrics`: errore + hint `make run`.

## Sorgente dei dati e runtime

MCP e test risolvono i mart in quest'ordine: **locale `out/` → env `LEGAL_GRAPH_MART_DIR` → GCS HTTPS**.

| Variabile | Default | Effetto |
|---|---|---|
| `LEGAL_GRAPH_SOURCE` | `auto` | `auto`: locale se esiste, altrimenti GCS · `local`: solo build locale, nessun fallback · `gcs`: solo GCS, anche con build locale (evita di servire dati vecchi) |
| `LEGAL_GRAPH_MART_DIR` | — | path esplicito dei mart (sostituisce l'autodetect di `out/`) |
| `LEGAL_GRAPH_THREADS` | `min(4, cpu)` | thread per query; `legal_search` è uno scan completo di `search_keys` (~2,4s a 1 thread, ~0,9s a 4) |
| `LEGAL_GRAPH_MEMORY_LIMIT` | `1GB` | tetto di memoria per connessione (non prenotata: RSS reale ~350MB); da alzare se materializzi i mart nella stessa connessione |
| `LEGAL_GRAPH_REQUIRE_MART` | assente | `1` → mart mancante è un errore, non uno skip (CI) |

Con i mart come VIEW sui parquet, 1GB è un tetto comodo, non una prenotazione. Con 256MB la memoria si esaurisce dopo qualche query pesante: in OOM la search risponde con un errore esplicito, non con una lista vuota.

Le view sui mart sono create al bootstrap della connessione: dopo un `make run` che aggiunge tabelle nuove serve **riavviare** l'MCP (il refresh per chiamata è stato tolto: costava ~50ms di query di catalogo su ogni tool).

## CI

| Workflow | Trigger | Cosa fa |
|---|---|---|
| `ci.yml` | PR + push main | preflight + lint (no build mart — non sono più in git) |
| `pipeline.yml` | lun 06:00 UTC, push sql/dataset.yml | `make run` + intelligence + **test** + sync GCS + registry PR |

I mart **non** vengono più committati in git. I test sul grafo girano in pipeline (o localmente). Il publish GCS include tutte e 10 le tabelle, texts e massime comprese (dal build 2026-11-09): `LEGAL_GRAPH_SOURCE=auto|local|gcs` sceglie la sorgente (default `auto`: locale → GCS).

Locale == CI: stessi target Makefile.

## Cosa è pronto / cosa no

| Area | Stato |
|---|---|
| Compose mart + MCP thin | ✅ 10 tabelle (nodes, edges, edges_in, coverage, metrics, search_keys, rel, emend_leg, texts, massime) |
| Golden test ranking | ✅ |
| LICENSE MIT | ✅ |
| CI org | ✅ Actions + pipeline → GCS + registry PR |
| Copertura onesta | ✅ [docs/COVERAGE.md](docs/COVERAGE.md) |
| Bank analisi / Discussion | ✅ [docs/ANALISI.md](docs/ANALISI.md) |
| Repo pubblico | ✅ · MCP nel template org ⏳ |
| Hub FOIA 33/2013 e 241/1990 | ✅ nodi nel grafo, con citazioni in ingresso (bersagli fuori corpus agganciati per URN); 33/2013: 157, 241/1990: 1.156 |
| Codici c.c./c.p. navigabili | ❌ gap |
| FTS / giurisprudenza ordinaria / PNRR dataset | ❌ fuori grafo |

## Prossimi passi

1. Discussion attive su questo repo come strumento di analisi (bank ANALISI.md)
2. MCP nel template org
3. Hub Lab: 33/2013, 241/1990, 190/2012 cercabili
4. Dopo: explorer `normativa`, dataset PNRR dedicato, non “Normattiva in grafo”
