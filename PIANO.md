# Legal Knowledge Graph — Piano di Sviluppo

**DataCivicLab — Esperimento locale, poi repo**

> Ogni legal claim → source → provision → version → evidence.
> Non un chatbot che "sa le leggi", ma l'infrastruttura dati che permette di ricostruire il sistema giuridico.

## Stato attuale

- **472.610 nodi** unici (11 fonti)
- **707.857 archi statici** (13 tipi di relazione)
- **61.096 archi temporali**
- **768.953 archi totali**
- Grafo interrogabile via DuckDB
- MCP server con **8 tool** (search, node_details, query, stats, intelligence, chain, jurisprudence, parliament)
- Dashboard Streamlit (**3 tab**: Catena del Diritto, Giurisprudenza, Parlamento)
- Bridge emendamento→DDL, Camera DDL→Legge, attua_delega funzionanti

---

## 1. Inventario dati esistente

### Asset già nel Lab

| Repo | Dataset | Rows | Join key |
|---|---|---|---|
| `italia-corpus` | `normativa.parquet` | 20.716 | **URN:NIR** (100%) |
| `italia-corpus` | `riferimenti.parquet` | 108.828 | fonte_filename → bersaglio_filename |
| `italia-corpus` | `citazioni-costituzionali.parquet` | 15.969 | fonte_filename + articolo |
| `costituzione-italiana` | `massime.parquet` | 266.805 | norma_numero + norma_data |
| `costituzione-italiana` | `atti-promovimento.parquet` | 1.007 | norma_numero |
| `costituzione-italiana` | `articoli.parquet` | 157 | articolo |
| `costituzione-italiana` | `revisioni.parquet` | 50 | urn |
| `gu-monitor` | `gu_acts.parquet` | 2.784 | id (codice redazionale) |
| `senato-akn` | `senato_corpus` | 1.891 | atto_num |
| `open-politica` | `senato_ddl` | ~5.000 | numero_legge + data_legge |

### Infrastruttura riusabile

- `lab-connectors/http/sparql.py` — executor SPARQL (POST/GET, retry, CSV fetch)
- `lab-connectors/http/client.py` — HTTP client con circuit breaker
- `lab-connectors/duckdb/` — query engine
- `toolkit/` — pipeline RAW → CLEAN → MART
- `toolkit/registry/builders.py` — `build_entity_graph()`
- `toolkit/mcp/server.py` — MCP server

---

## 2. Ponti mancanti

### Ponte 1: `gu_acts` → `normativa` (URGENTE, facile)
- **Problema**: `urn_normattiva` nello schema ma non popolato nel parquet
- **Fix**: Modificare `to_parquet.py` per LEFT JOIN con `gu_links.json` su `id`
- **Risultato**: Ogni atto GU collegato a Normattiva via URN

### Ponte 2: `senato_ddl` → `normativa` (media)
- **Problema**: `numero_legge` + `data_legge` (14% coverage) ma nessuna URN:NIR
- **Fix**: Script `build_urn.py` → `urn:nir:stato:legge:{data};{numero}`
- **Risultato**: Iter parlamentare collegato alla legge vigente (bridge F5)

### Ponte 3: `massime` → `normativa` (media)
- **Problema**: `norma_numero` + `norma_data` + `norma_descrizione` non collegati
- **Fix**: Costruire URN:NIR dalla descrizione + numero + data, o fuzzy match
- **Risultato**: Sentenze Corte Costituzionale → norma impugnata

### Ponte 4: Grafo unificato
- **Problema**: Grafi separati in ogni repo, nessun layer collegato
- **Fix**: Dataset `legal-nodes` + `legal-edges` che unificano tutto

---

## 3. Schema proposto

### `legal_nodes`

| Colonna | Tipo | Descrizione |
|---|---|---|
| `id` | VARCHAR | URN:NIR o identificativo univoco |
| `tipo` | VARCHAR | LEGGE, DECRETO, DECRETO-LEGGE, SENTENZA, ... |
| `data` | DATE | Data di pubblicazione/emanazione |
| `numero` | VARCHAR | Numero dell'atto |
| `title` | VARCHAR | Titolo/oggetto |
| `collezione` | VARCHAR | Collezione di provenienza |
| `source` | VARCHAR | normativa, gu, senato, costituzione |
| `source_filename` | VARCHAR | Filename originale (per join) |
| `anno` | INTEGER | Anno |
| `length_chars` | BIGINT | Lunghezza testo (caratteri) |
| `length_words` | BIGINT | Lunghezza testo (parole) |
| `celex` | VARCHAR | Identificativo CELEX (se UE) |
| `vigente` | BOOLEAN | Se l'atto è ancora in vigore |

### `legal_edges`

| Colonna | Tipo | Descrizione |
|---|---|---|
| `source_id` | VARCHAR | Nodo sorgente |
| `relation` | VARCHAR | riferimento, cita_costituzione, impugna, invoca_parametro, modifica, abroga, pubblica_in_gu |
| `target_id` | VARCHAR | Nodo destinazione |
| `weight` | INTEGER | Peso dell'arco (occorrenze) |
| `source_year` | INTEGER | Anno del nodo sorgente |
| `target_year` | INTEGER | Anno del nodo destinazione |
| `evidence` | VARCHAR | Contesto testuale (opzionale) |

### Tipi di relazione

| relation | Fonte | Target | Descrizione |
|---|---|---|---|
| `riferimento` | atto | atto | Riferimento incrociato tra atti (italia-corpus) |
| `cita_costituzione` | atto | articolo_costituzione | Cita un articolo della Costituzione |
| `impugna` | sentenza | atto | Corte Costituzionale dichiara incostituzionale |
| `invoca_parametro` | sentenza | articolo_costituzione | Parametro costituzionale invocato |
| `evoca_parametro` | promovimento | articolo_costituzione | Parametro costituzionale evocato |
| `diventa_legge` | senato_ddl | atto | DDL diventa legge vigente |
| `recepisce_direttiva` | atto | direttiva_ue | Ricepimento direttiva UE |
| `attua_regolamento` | atto | regolamento_ue | Attuazione regolamento UE |
| `collega_ue` | atto | atto_ue | Altro collegamento UE |
| `emendamento` | senato_ddl | emendamento | Emendamento a un DDL |
| `intervento` | senato_ddl | intervento | Intervento in dibattito parlamentare |
| `testo_atto` | senato_ddl | corpus_akt | Testo dell'atto legislativo |

---

## 4. Piano di implementazione

### Fase 1 — Attivare i ponti esistenti (1-2 giorni)

- [x] **1a**: `gu-monitor` — popolare `urn_normattiva` in `gu_acts.parquet` (26 atti collegati)
- [x] **1b**: `open-politica` — costruire URN:NIR in `senato_ddl` (733 atti collegati)
- [ ] **1c**: Verificare EUR-Lex CELLAR (da rete EU)

### Fase 2 — Dataset `legal-nodes` (2-3 giorni)

- [x] Creare script `build_legal_nodes.py`
- [x] Unire `normativa` + `revisioni` + `gu_acts` + `senato_corpus`
- [x] Output: `data/legal_nodes.parquet` (20.931 nodi)

### Fase 3 — Dataset `legal-edges` (2-3 giorni)

- [x] Creare script `build_legal_edges.py`
- [x] Unire `riferimenti` + `citazioni-costituzionali` + `massime` + relazioni GU
- [x] Output: `data/legal_edges.parquet` (459.465 archi)

### Fase 4 — Temporal versioning (fase avanzata, 1-2 settimane)

- [x] Parser versioni Normattiva: API provide `articoloDataInizioVigenza` / `articoloDataFineVigenza` ma inconsistente per bulk
- [x] Estensione archi con `modifica` (32.315 archi da riferimenti incrociati + date)
- [x] Estensione con `entra_in_vigore` (20.716 atti con data di pubblicazione)
- [x] Estensione con `pubblicato_in_gu` (26 atti da gu-monitor)
- [x] Output: `data/legal_edges_temporal.parquet` (53.057 archi temporali)

### Fase 5 — EUR-Lex integration (fase avanzata, 1 settimana)

- [x] Verificare accesso CELLAR: SPARQL non accessibile da questa rete (endpoint deprecato)
- [x] `data.europa.eu/sparql` ha 1.26B triples ma solo metadata catalogo, non CELLAR
- [x] Usare i 757 CELEX già in italia-corpus per collegare atti italiani a direttive/regolamenti UE
- [x] EUR-Lex REST API: parsing HTML ELI metadata (tipo, date, EuroVoc)
- [x] Output: `data/legal_nodes_eu.parquet` (632 nodi UE), `data/legal_edges_eu.parquet` (752 archi EU)

---

## 5. Ordine di priorità

| # | Fase | Effort | Valore | Blocca autre? | Stato |
|---|---|---|---|---|---|
| 1 | **1a**: gu-monitor URN | 0.5 giorno | Alto | No | ✅ |
| 2 | **1b**: senato_ddl URN | 1 giorno | Alto | Sì (F5) | ✅ |
| 3 | **2**: legal-nodes | 2-3 giorni | Alto | Sì (grafo) | ✅ |
| 4 | **3**: legal-edges | 2-3 giorni | Alto | No | ✅ |
| 5 | **1c**: EUR-Lex verify | 0.5 giorno | Medio | No | ✅ |
| 6 | **4**: Temporal | 1-2 settimane | Altissimo | No | ✅ |
| 7 | **5**: EUR-Lex enrich | 1 giorno | Medio | No | ✅ |

**Totale MVP (fasi 1-3)**: ~5-7 giorni ✅ COMPLETATO
**Totale completo (fasi 1-5)**: ~3-4 settimane ✅ COMPLETATO

---

## 6. Domande aperte

1. **EUR-Lex CELLAR**: accessibile solo da rete EU? Verificare da ambiente diverso.
2. **Scope MVP**: partire solo con `normativa` + `riferimenti` + `citazioni` (già tutti i dati pronti) oppure subito unire anche GU e Senato?
3. **Repo finale**: estendere `italia-corpus` o creare repo separato `legal-graph`?
4. **Versioning temporale**: quanto investirci subito vs. posticipare?

---

## 7. MCP Server

Il grafo è esposto come server MCP con 4 tool:

### Tool disponibili

| Tool | Uso |
|---|---|
| `legal_search` | Cerca atti per testo, tipo, anno, sorgente |
| `legal_node_details` | Dettagli nodo + tutti i suoi archi (in/out/temporali) |
| `legal_graph_query` | SQL arbitrario sul grafo (solo SELECT) |
| `legal_stats` | Statistiche generali |

### Avvio

```bash
cd esperimenti-locali/legal-graph
python scripts/mcp_server.py
```

### Esempio di utilizzo da agente AI

```
"Cerca tutte le leggi che modificano il Codice Penale"
→ legal_search(query="Codice Penale", tipo="LEGGE")

"Quali articoli Costituzionali sono più invocati in Corte?"
→ legal_graph_query(sql="SELECT ... FROM edges WHERE relation='invoca_parametro'")

"Dimmi tutto sulla legge 234/2012"
→ legal_node_details(node_id="urn:nir:stato:legge:2012-12-24;234")
```

---

## 8. Risorse

- [italia-corpus](https://github.com/dataciviclab/italia-corpus) — 20.716 atti, 108.490 riferimenti
- [costituzione-italiana](https://github.com/dataciviclab/costituzione-italiana) — 266.805 massime, 15.969 citazioni
- [gu-monitor](https://github.com/dataciviclab/gu-monitor) — Gazzetta Ufficiale
- [senato-akn](https://github.com/dataciviclab/senato-akn) — Parlamento Akoma Ntoso
- [open-politica](https://github.com/dataciviclab/open-politica) — Dati parlamentari strutturati
- [EUR-Lex CELLAR](https://publications.europa.eu/webapi/sparql) — SPARQL UE
- [Normattiva](https://www.normattiva.it) — Fonte primaria legislazione italiana
- [Corte Costituzionale Open Data](https://dati.cortecostituzionale.it) — Sentenze e massime
