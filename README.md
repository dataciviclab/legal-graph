# Legal Knowledge Graph — il diritto italiano come sistema interrogabile

**472.000 nodi, 710.000 relazioni tra leggi, decreti, DDL, sentenze e Costituzione. Un grafo per capire come funziona il diritto — non solo leggerlo.**

Le leggi non vivono isolate: una DDL diventa legge, un D.Lgs attua una delega, una sentenza della Corte Costituzionale impugna una norma, un articolo della Costituzione viene citato migliaia di volte. Qui quelle relazioni sono **dati**, non opinioni.

Legal Graph unifica **5 repo del Lab** (normativa, Costituzione, Senato, Camera, GU) in un unico grafo interrogabile, per rispondere a domande come:

- *Da dove viene il Codice del Terzo Settore?*
- *Questa legge è stata impugnata dalla Corte?*
- *Chi cita l’art. 3 Cost. nelle leggi ordinarie?*
- *Questa DDL è diventata legge?*

## Cosa contiene

| | |
|---|---|
| **Nodi** | ~472.000 — leggi, decreti, DDL Camera/Senato, emendamenti, dibattiti, sentenze, articoli Cost., norme |
| **Archi** | ~710.000 — riferimenti, citazioni costituzionali, impugnazioni, emendamenti, deleghe, bridge DDL→legge |
| **Fonti** | italia-corpus, costituzione-italiana, gu-monitor, senato-akn, open-politica |
| **Relazioni tipiche** | `diventa_legge`, `impugna`, `cita_costituzione`, `attua_delega`, `emendamento`, `riferimento` |

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
| `legal_search` | Trova atti (parole, tipo, anno, fonte) |
| `legal_node` | Contesto: `overview` · `chain` · `jurisprudence` · `parliament` |
| `legal_text` | Testo integrale di un atto da URN |
| `legal_query` | SQL (SELECT) su nodi e archi |
| `legal_insights` | Report: atti critici, obsoleti, complessi |

### 2. Build locale (compose toolkit)

```bash
make run          # costruisce nodi e archi leggendo le fonti da rete
make test         # 32 test di integrità
make intelligence # metriche per legal_insights
```

Output: `out/data/mart/legal_graph/2026/` (parquet nodi + archi).

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

Il grafo è un **compose mart-only toolkit**: legge i clean/derived di altri repo Lab da rete (GitHub raw e GCS), li unisce in nodi e archi, li espone via MCP. Non clona i repo dati e non duplica i testi — per il testo si usa `legal_text`.

Documenti di dettaglio:

- [COMPOSE.md](COMPOSE.md) — come è costruito, cosa è pronto, limiti
- [KEYS.md](KEYS.md) — chiavi cross-repo (URN, id_ddl, atto_num)

## Limiti

- `legal_text` copre gli atti di **normativa** (italia-corpus); per altri testi restano i repo dati
- Nessuna ricerca full-text dentro i corpus di testo (si cerca su titoli e relazioni del grafo)
- Relazioni UE e archi temporali sono opzionali e non nel compose principale

## Partecipa

- [Discussions del Lab](https://github.com/orgs/dataciviclab/discussions) — idee, limiti, nuove domande sul grafo
- [Issue del Lab](https://github.com/dataciviclab/dataciviclab/issues) — bug o proposte cross-repo
- Contributi al codice: vedi [CONTRIBUTING del Lab](https://github.com/dataciviclab/dataciviclab/blob/main/COME-CONTRIBUIRE.md)

## Licenza e CI

[![CI](https://github.com/dataciviclab/legal-graph/actions/workflows/ci.yml/badge.svg)](https://github.com/dataciviclab/legal-graph/actions/workflows/ci.yml)

Dati: pubblico dominio dalle fonti ufficiali (Normattiva, Corte Costituzionale, Parlamento).  
Codice: [MIT](LICENSE) — DataCivicLab.
