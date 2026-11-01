# Legal Knowledge Graph — il diritto italiano come sistema interrogabile

**518.388 nodi, 1.201.188 relazioni tra leggi, decreti, DDL, sentenze e Costituzione. Un grafo per capire come funziona il diritto — non solo leggerlo.**

> Aggiornato 2026-11: **emendamenti collegati all'atto +54%** (il target è il nodo atto, non solo `senato_ddl`: 103.829 orfani → 22.377, +238.363 archi), **`legal_search` ordina per vigenza prima dell'impatto** (la norma in vigore batte l'abrogata più citata), **`invoca_parametro` solo su parametri costituzionali** (`parametro_codice=25`, −13.649 edge che mappavano statuti regionali/codici/CEDU sulla Costituzione), 0 target dangling, un solo nodo per atto Senato (i 2.990 documenti AKN senza omologo open-politica diventano il nodo canonico). Precedente 2026-10: evidence sugli edge `riferimento`, +3.666 `impugna` collegati a URN reali, +396 bridge senatore↔deputato, +35.911 `riferimento` dagli AKN, −1.013 autoanelli, nuovo mart **`coverage`**.

Le leggi non vivono isolate: una DDL diventa legge, un D.Lgs attua una delega, una sentenza della Corte Costituzionale impugna una norma, un articolo della Costituzione viene citato migliaia di volte. Qui quelle relazioni sono **dati**, non opinioni.

Legal Graph unifica **5 repo del Lab** (normativa, Costituzione, Senato, Camera, GU) in un unico grafo interrogabile, per rispondere a domande come:

- *Da dove viene il Codice del Terzo Settore?*
- *Questa legge è stata impugnata dalla Corte?*
- *Chi cita l’art. 3 Cost. nelle leggi ordinarie?*
- *Questa DDL è diventata legge?*

## Cosa contiene

| | |
|---|---|
| **Nodi** | 518.388 — leggi, decreti, DDL Camera/Senato, DL conversione, emendamenti, dibattiti, sentenze, articoli Cost., norme |
| **Archi** | 1.201.188 — riferimenti, citazioni costituzionali, impugnazioni, emendamenti, deleghe, modifiche AKN (abroga/sostituisce), bridge DDL→legge, firmatari, relatore sentenza |
| **Mart** | nodes · edges · **coverage** · metrics · search_keys · node_rel · emend_leg · **texts** · **massime** |
| **Fonti** | italia-corpus, costituzione-italiana, gu-monitor, senato-akn, open-politica |
| **Relazioni tipiche** | `diventa_legge`, `impugna`, `cita_costituzione`, `attua_delega`, `abroga`, `sostituisce`, `firmatario`, `relatore`, `relatore_sentenza`, `converte_decreto_legge`, `emendamento`, `riferimento`, `collega` (senatore↔deputato) |

## Esempi di domande

| Domanda | Tool / query |
|---|---|
| Trova un atto per nome o tema | `legal_search` |
| Da dove viene questa legge? (delega, DDL, recepimento UE) | `legal_node(view="chain")` |
| La Corte ha impugnato questa norma? | `legal_node(view="jurisprudence")` |
| Emendamenti e iter di una DDL | `legal_node(view="parliament")` |
| Quali atti citano un articolo della Costituzione? | SQL su `cita_costituzione` |
| Quali norme sono più critiche o obsolette? | `legal_insights` |
| Testo integrale di un atto | `legal_text(URN)` |
| Un articolo specifico (anche dentro un codice lungo) | `legal_text(URN, articolo=45)` |

## Come accedere

### 1. MCP del Lab (per agenti e chi lavora in chat)

```text
legal_search("responsabilita amministrativa")
  → legal_node(id)                  # overview del nodo
  → legal_node(id, view="chain")    # se serve la catena legislativa
  → legal_text(id)                  # testo (atti normativi)
```

| Tool | Cosa fa |
|---|---|
| `legal_search` | Trova atti (parole, tipo, anno, fonte, stato, vigenza_fonte, materia, min_score, collezione) |
| `legal_node` | Contesto: `overview` · `chain` · `jurisprudence` · `parliament` |
| `legal_text` | Testo di un atto da URN; `articolo=N` estrae il singolo articolo dagli heading AKN |
| `legal_query` | SQL (SELECT) su nodi e archi |
| `legal_insights` | Report: atti critici, obsoleti, complessi |

### 2. Build locale (compose toolkit)

```bash
make run          # compose: mart da rete → out/data/mart/ (gitignored)
make download-gcs # senza build: scarica le 7 tabelle grafo da GCS
make test         # integrità + golden search + bilancio copertura
make lint
```

Output locale: `out/data/mart/legal_graph/2026/` (**non committato**).

**Pubblicazione** (source of truth per i consumatori):

| Tabella | GCS |
|---|---|
| `nodes` · `edges` · `coverage` · `metrics` · `search_keys` · `node_rel` · `emend_leg` | `gs://dataciviclab-mart/legal-graph/legal_graph/2026/` |
| `texts` · `massime` | locale / MCP-only (primo rilascio) |

MCP e test risolvono: **locale `out/` → env `LEGAL_GRAPH_MART_DIR` → GCS HTTPS**.

| Tabella | Ruolo |
|---|---|
| `mart_legal_nodes` / `mart_legal_edges` | grafo canonicо |
| `mart_legal_coverage` | bilancio righe upstream → coppie → edge, per relazione |
| `mart_legal_node_metrics` | intelligence (referenced_by, impact, età) |
| `mart_legal_search_keys` | ranking search (id_num, title_folded) |
| `mart_legal_node_rel` | pre-aggregato relazioni per view MCP |
| `mart_legal_emend_leg` | emendamenti per DDL/legislatura |

Ordine obbligato nel compose: nodes → edges → coverage → derivate (stessa sessione DuckDB).
`coverage` legge `mart_legal_edges`, quindi non può divergere dal grafo pubblicato.  
I mart **non** sono più committati in git: CI → GCS + registry. Le fonti upstream si leggono da rete.

MCP senza search mart → errore con hint `make run` o `make download-gcs`.

### 3. SQL diretto (DuckDB)

```sql
-- Atti che citano l'articolo 3 della Costituzione
SELECT e.source_id, n.title
FROM read_parquet('out/data/mart/legal_graph/2026/mart_legal_edges.parquet') e
JOIN read_parquet('out/data/mart/legal_graph/2026/mart_legal_nodes.parquet') n
  ON n.id = e.source_id
WHERE e.relation = 'cita_costituzione'
  AND e.target_id = 'costituzione:art:3'
LIMIT 10;
```

## Architettura (in breve)

Compose **mart-only toolkit**: legge i clean/derived di altri repo Lab da rete, produce nodi/archi + tabelle derivate (metrics, search keys, pre-aggregati), li espone via MCP. Non clona i repo dati e non duplica i testi — per il testo si usa `legal_text`.

```text
upstream (GitHub raw / GCS)
  → make run (dataset.yml + sql/mart_legal_*.sql)
  → out/data/mart/legal_graph/2026/  (gitignored)
  → MCP thin: legal_search (intent → SQL) · legal_node(view) · legal_text · query · insights
```

`legal_search` è **thin**: parse intent (numero/anno, ECLI, Cost., data, frase) → **1 query** su `search_keys` + `metrics`.  
`attua_delega` è un **edge tipizzato** nel mart (delega→attuazione), non solo un euristica runtime.

Documenti di dettaglio:

- [COMPOSE.md](COMPOSE.md) — come è costruito, mart, CI, limiti
- [KEYS.md](KEYS.md) — chiavi cross-repo (URN, id_ddl, atto_num)
- [docs/COVERAGE.md](docs/COVERAGE.md) — mappa onesta di cosa copre / non copre
- [docs/ANALISI.md](docs/ANALISI.md) — bank di domande per Discussion e agenti

## Limiti (sintesi)

Legal Graph è un **motore di contesto relazionale** (processo Senato + Costituzione +
normativa di sistema recente), **non** un’enciclopedia del diritto italiano.

- Coperto bene: catene legislative, `attua_delega`, modifiche AKN (`abroga`/`sostituisce`), Corte Cost. (+ relatore), firmatari Camera/Senato, D.Lgs "di sistema"
- Scoperto: corpus normativo full (~21k vs ~288k IC), codici art. per art., giurisprudenza ordinaria, GU/EUR-Lex, PNRR come dataset
- `legal_text`: normativa (IC) + articoli Cost. + pronunce Corte (mart texts); senza `articolo` restituisce la testa del documento (max 50k caratteri), non l'atto intero
- id parziali: `legal_node`/`legal_text` risolvono un id parziale **solo se unico**, altrimenti rispondono `id ambiguo` con i candidati (mai un atto scelto a caso)
- Ranking: vigenza → impatto → anno per i temi; per atti precisi usare **numero/URN/direttiva**
- `evidence`: contesto upstream quando la fonte lo espone, altrimenti tag di provenienza (`akn:ref` sul 78% dei `riferimento`) — non è una citazione verificata
- citazioni formulaiche o non informative (`is_formulaic` 3.140 + `is_reading_clause` 4.221): escluse da `impact_score` (lordi in `formulaic_by`/`formulaic_out`), vedi [COVERAGE.md](docs/COVERAGE.md)
- `evidence_kind` sui `riferimento`: `missing` 56,1% (segnaposto `akn:ref`), `locator` 5,1%, `quote` 38,8% — vedi italia-corpus#109
- Sempre: *dal mart legal-graph, non da Normattiva live*

## Partecipa

- **Analisi e confronto**: [Discussions di questo repo](../../discussions) — bank in [docs/ANALISI.md](docs/ANALISI.md)
- Intake pubblico Lab: [Discussions del Lab](https://github.com/orgs/dataciviclab/discussions)
- Issue cross-repo: [dataciviclab/dataciviclab](https://github.com/dataciviclab/dataciviclab/issues)
- Contributi codice: [CONTRIBUTING del Lab](https://github.com/dataciviclab/dataciviclab/blob/main/COME-CONTRIBUIRE.md)

## Licenza e CI

[![CI](https://github.com/dataciviclab/legal-graph/actions/workflows/ci.yml/badge.svg)](https://github.com/dataciviclab/legal-graph/actions/workflows/ci.yml)

Dati: pubblico dominio dalle fonti ufficiali (Normattiva, Corte Costituzionale, Parlamento).  
Codice: [MIT](LICENSE) — DataCivicLab.
