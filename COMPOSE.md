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
| Pubblico | GCS `dataciviclab-mart/legal-graph/` (7 tabelle grafo: 6 grafo + coverage) |
| Package | `legal_graph/` — MCP 5 tool + paths + legal_text |
| CLI | — (il layer legacy `data/*.parquet` è stato rimosso) |
| Test | `tests/` — integrità + golden search |

## Mart (ordine compose)

```text
1 mart_legal_nodes          ← support esterni (+ eiv da akn_act_meta,
                              nodi dl:* da decreti_legge, camera:atto:* da camera_relatori)
2 mart_legal_edges          ← support esterni + relazioni tipizzate
                              (attua_delega, modifiche AKN, relatore_sentenza,
                              relatore Camera, firmatario, converte_decreto_legge)
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
make run              # compose da rete → 9 mart
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

## CI

| Workflow | Trigger | Cosa fa |
|---|---|---|
| `ci.yml` | PR + push main | preflight + lint (no build mart — non sono più in git) |
| `pipeline.yml` | lun 06:00 UTC, push sql/dataset.yml | `make run` + intelligence + **test** + sync GCS + registry PR |

I mart **non** vengono più committati in git. I test sul grafo girano in pipeline (o localmente). texts/massime restano fuori dal GCS nel primo rilascio.

Locale == CI: stessi target Makefile.

## Cosa è pronto / cosa no

| Area | Stato |
|---|---|
| Compose mart + MCP thin | ✅ 9 tabelle (nodes, edges, coverage, metrics, search_keys, rel, emend_leg, texts, massime) |
| Golden test ranking | ✅ |
| LICENSE MIT | ✅ |
| CI org | ✅ Actions + pipeline commit mart |
| Copertura onesta | ✅ [docs/COVERAGE.md](docs/COVERAGE.md) |
| Bank analisi / Discussion | ✅ [docs/ANALISI.md](docs/ANALISI.md) |
| Repo pubblico / MCP template org | ⏳ non ancora |
| Hub FOIA 33/2011, 241/1990 | ⚠️ parziale — L. 241/1990 in grafo via Normattiva API (2026-10); L. 33/2011 ancora assente |
| Codici c.c./c.p. navigabili | ❌ gap |
| FTS / giurisprudenza ordinaria / PNRR dataset | ❌ fuori grafo |

## Prossimi passi

1. Discussion attive su questo repo come strumento di analisi (bank ANALISI.md)
2. Repo pubblico + scheda projects + MCP nel template org
3. Hub Lab: 33/2013, 241/1990, 190/2012 cercabili
4. Dopo: explorer `normativa`, dataset PNRR dedicato, non “Normattiva in grafo”
