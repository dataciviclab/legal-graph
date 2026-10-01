# Legal Graph — Compose toolkit

Stato: **compose + MCP thin in CI** (2026-09-30). Repo `dataciviclab/legal-graph`.

## Cosa è

Compose **mart-only toolkit** (modello `pil-intelligence`).
Nodi, archi e tabelle derivate da rete (GitHub raw / GCS HTTPS), senza clonare i repo dati.

| Pezzo | Dove |
|---|---|
| Config | `dataset.yml` (ordine tabelle obbligatorio) |
| SQL base | `sql/mart_legal_nodes.sql`, `sql/mart_legal_edges.sql` |
| SQL derivate | metrics · search_keys · node_rel · emend_leg · **texts** · **massime** |
| Output | `out/data/mart/legal_graph/2026/` (committato) |
| Package | `legal_graph/` — MCP 5 tool + paths + legal_text |
| CLI | `scripts/` — intelligence fallback, eu/temporal |
| Test | `tests/` — integrità + golden search |

## Mart (ordine compose)

```text
1 mart_legal_nodes          ← support esterni
2 mart_legal_edges          ← support esterni + attua_delega tipizzato
3 mart_legal_node_metrics   ← FROM mart_legal_nodes + edges
4 mart_legal_search_keys    ← FROM mart_legal_nodes
5 mart_legal_node_rel       ← FROM mart_legal_edges
6 mart_legal_emend_leg      ← FROM mart_legal_edges
7 mart_legal_texts           ← articoli Cost. + pronunce (testo/dispositivo)
8 mart_legal_massime         ← massime (testo troncato 600)
```

Le tabelle derivate leggono le precedenti **nella stessa sessione DuckDB** del run MART.

## Come si usa

```bash
make run              # compose da rete → 6 mart
make test             # integrità + golden (~50)
make lint
make intelligence     # fallback solo se metrics mancanti dal mart
```

Le fonti si leggono **HTTPS dirette** — nessun clone locale.

## MCP (thin)

| Tool | Cosa fa |
|---|---|
| `legal_search` | intent (numero/anno, ECLI, Cost., data, frase) → 1 SQL su search_keys+metrics |
| `legal_node` | overview · chain · jurisprudence · parliament (usano node_rel/emend_leg) |
| `legal_text` | testo normativa (fetch rete) |
| `legal_query` | SELECT/PRAGMA/DESCRIBE escape hatch |
| `legal_insights` | report da metrics mart |

Se manca `search_keys`/`metrics`: errore + hint `make run`.

## CI

| Workflow | Trigger | Cosa fa |
|---|---|---|
| `ci.yml` | PR + push main | preflight → `make run` → ruff + pytest |
| `pipeline.yml` | lun 06:00 UTC, push sql/dataset.yml | `make run` + intelligence + test + commit mart `[skip ci]` |

Locale == CI: stessi target Makefile.

## Cosa è pronto / cosa no

| Area | Stato |
|---|---|
| Compose mart + MCP thin | ✅ 8 tabelle (nodes, edges, metrics, search_keys, rel, emend_leg, texts, massime) |
| Golden test ranking | ✅ |
| LICENSE MIT | ✅ |
| CI org | ✅ Actions + pipeline commit mart |
| Copertura onesta | ✅ [docs/COVERAGE.md](docs/COVERAGE.md) |
| Bank analisi / Discussion | ✅ [docs/ANALISI.md](docs/ANALISI.md) |
| Repo pubblico / MCP template org | ⏳ non ancora |
| Hub FOIA 33/2011, 241/1990 | ❌ gap (solo `norma:*`) |
| Codici c.c./c.p. navigabili | ❌ gap |
| FTS / giurisprudenza ordinaria / PNRR dataset | ❌ fuori grafo |

## Prossimi passi

1. Discussion attive su questo repo come strumento di analisi (bank ANALISI.md)
2. Repo pubblico + scheda projects + MCP nel template org
3. Hub Lab: 33/2013, 241/1990, 190/2012 cercabili
4. Solo nodes+edges in git; derivate via `make derived` (storia git leggera)
5. Dopo: explorer `normativa`, dataset PNRR dedicato, non “Normattiva in grafo”
