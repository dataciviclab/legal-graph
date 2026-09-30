# Legal Graph — Compose toolkit

Stato: **MVP solido in locale** (2026-09-30). Non è ancora un repo di pubblicazione.

## Cosa è

Compose **mart-only toolkit** (modello `pil-intelligence`).
Nodi e archi da rete (GitHub raw / GCS HTTPS), senza clonare i repo dati.

| Pezzo | Dove |
|---|---|
| Config | `dataset.yml` |
| SQL | `sql/mart_legal_nodes.sql`, `sql/mart_legal_edges.sql` |
| Output | `out/data/mart/legal_graph/2026/` |
| Package | `legal_graph/` — MCP 5 tool + paths + legal_text |
| CLI | `scripts/` — intelligence, sync offline, eu/temporal |
| Test | `tests/` |

## Come si usa

```bash
make run              # compose da rete (GitHub raw / GCS)
make intelligence     # metriche → legal_insights
make test
```

Le fonti si leggono **HTTPS dirette** — nessun clone locale, nessuna cache obbligatoria.

## Pulito per pubblicazione

- Dashboard rimossa
- Script sperimentali in `scripts/` (fuori dal package)
- LICENSE MIT
- MCP: 5 tool (search → node(view) → text; query/insights)

## Cosa è pronto e cosa no

| Area | Stato |
|---|---|
| Compose + MCP + test | ✅ |
| LICENSE | ✅ MIT |
| CI GitHub Actions | ✅ `ci.yml` + `pipeline.yml` |
| CI / remote org / path repo | ✅ Actions + `dataciviclab/legal-graph` (private) |
| Mart committati da pipeline | ✅ `out/data/mart/` (come pil-intelligence) |

## CI

| Workflow | Trigger | Cosa fa |
|---|---|---|
| `.github/workflows/ci.yml` | PR + push main | preflight → `make run` → ruff + pytest |
| `.github/workflows/pipeline.yml` | lunedì 06:00 UTC, push sql/dataset.yml | compose + intelligence + test + artifact |

Locale == CI: stessi target Makefile (`make check`, `make run`, `make test`, `make lint`).

## Prossimi passi

1. Spostamento path (org o `diritto-legge/legal-graph`) + remote
2. Abilitare Actions sul repo org
3. MCP nel template org + scheda projects
4. Dopo: explorer `normativa`, eventuali fonti con bridge
