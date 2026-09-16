# Legal Knowledge Graph

**472.610 nodi normativi, 768.953 relazioni. Il sistema giuridico italiano come grafo interrogabile.**

Non un chatbot che "sa le leggi", ma l'infrastruttura dati che permette di ricostruire il sistema giuridico: ogni claim → source → provision → version → evidence.

## Cosa contiene

| | |
|---|---|
| **Nodi** | 472.610 (leggi, decreti, DDL Camera/Senato, emendamenti, dibattiti, sentenze, norme, UE) |
| **Archi statici** | 707.857 (riferimenti, citazioni, impugnazioni, emendamenti, interventi, deleghe, bridge) |
| **Archi temporali** | 61.096 (modifiche, entrata in vigore) |
| **Fonti** | 11 repo del Lab collegati |
| **Relazioni** | 13 tipi (riferimento, cita_costituzione, impugna, diventa_legge, attua_delega, recepisce_direttiva, ...) |
| **Legislature** | 7 legislature Senato + Camera (XIII-XIX, 1996-2026) |

## Fonti collegate

```
italia-corpus ─────┐
costituzione ──────┤
open-politica ─────┤
gu-monitor ────────┼──→ LEGAL KNOWLEDGE GRAPH
senato-akn ────────┤       │
EUR-Lex ───────────┘       ├── 472.610 nodi
                           ├── 707.857 archi
                           ├── 11 fonti
                           └── MCP server (8 tool)
```

## Esempi di domande

- **Questa legge è stata dichiarata incostituzionale?** → 10.890 sentenze impugnano norme
- **Quali norme impugna più spesso la Corte?** → D.Lgs. 286/1998 (Testo Unico Immigrazione): 152 impugnazioni
- **Quali articoli Costituzionali sono più invocati?** → Art. 3 (uguaglianza), Art. 24 (difesa), Art. 117 (autonomia)
- **DDL Senato è diventata legge?** → 2.757 bridge DDL → legge vigente (7 legislature)
- **Quali legge è più citata nelle altre?** → Riferimenti incrociati tra 60.595 atti
- **Quali direttive UE ha recepito l'Italia?** → 1.241 recepimenti da 567 direttive
- **Chi ha parlato in Senato su un provvedimento?** → 120.926 interventi di dibattito
- **Timeline delle modifiche legislative?** → Picco 2017 (2.183 modifiche)

## Installazione

```bash
pip install -e .
```

## MCP Server

Il grafo è esposto come server MCP con 8 tool:

| Tool | Uso |
|---|---|
| `legal_search` | Cerca atti per testo, tipo, anno, sorgente |
| `legal_node_details` | Dettagli nodo + tutti i suoi archi |
| `legal_graph_query` | SQL arbitrario sul grafo (solo SELECT) |
| `legal_stats` | Statistiche generali |
| `legal_intelligence` | Analisi impatto, complessità, obsolescenza |
| **`legal_chain`** | Catena del diritto: DDL→Legge→D.Lgs→EU |
| **`legal_jurisprudence`** | Sentenze, impugnazioni, parametri Costituzionali |
| **`legal_parliament`** | Emendamenti, interventi, stato iter DDL |

```bash
legal-graph-mcp
```

### Esempio: catena del Codice Terzo Settore

```
legal_chain(node_id="urn:nir:stato:decreto.legislativo:2017-07-03;117")
→ D.Lgs 117/2017 (Codice Terzo Settore)
→ attua_delega → Legge 106/2016 (Delega Terzo Settore)
→ cita_costituzione → Art. 2, 3, 11, 18, 118
```

### Esempio: sentenza sulla fecondazione assistita

```
legal_jurisprudence(node_id="sentenza:2009-0151")
→ Impugna: Legge 40/2004
→ Parametri: Art. 2, 3, 13, 23, 32 Costituzione
```

## Dashboard

```bash
pip install -r dashboard/requirements.txt
streamlit run dashboard/app.py
```

3 tab: Catena del Diritto, Giurisprudenza, Parlamento.

## Build

Rigenera i parquet dai dati sorgente:

```bash
python -m legal_graph.build_legal_nodes
python -m legal_graph.build_legal_edges
python -m legal_graph.temporal_enrichment
python -m legal_graph.eu_enrichment
```

## Struttura

```
legal-graph/
├── legal_graph/
│   ├── __init__.py
│   ├── mcp_server.py              # MCP server (8 tool)
│   ├── build_legal_nodes.py       # Unifica nodi da 11 fonti
│   ├── build_legal_edges.py       # Unifica archi (13 tipi)
│   ├── temporal_enrichment.py     # Archi temporali
│   └── eu_enrichment.py           # Arricchimento EUR-Lex
├── dashboard/
│   ├── app.py                     # Dashboard Streamlit (3 tab)
│   └── requirements.txt
├── data/
│   ├── legal_nodes.parquet        # 472.610 nodi
│   ├── legal_edges.parquet        # 707.857 archi statici
│   ├── legal_edges_temporal.parquet # 61.096 archi temporali
│   ├── legal_nodes_eu.parquet     # 632 nodi UE
│   └── legal_edges_eu.parquet     # 1.745 archi EU
├── pyproject.toml
├── PIANO.md
├── notes.md
└── README.md
```

## Licenza

MIT — dati: pubblico dominio (Normattiva, Corte Costituzionale, EUR-Lex, Senato)

Progetto del [DataCivicLab](https://github.com/dataciviclab).
