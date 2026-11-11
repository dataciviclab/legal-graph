# Legal Knowledge Graph — il diritto italiano come sistema

**857.938 nodi e 1.369.447 relazioni tra leggi, decreti, disegni di legge, sentenze e Costituzione (build 2026-11-11).**

Una legge non nasce da sola. Un disegno di legge diventa legge, un decreto legislativo attua una delega, una sentenza della Corte costituzionale impugna una norma, un articolo della Costituzione viene citato migliaia di volte. Il Legal Graph mette in un unico grafo interrogabile queste relazioni, raccolte da cinque fonti del Lab: italia-corpus, costituzione-italiana, senato-akn, open-politica e gu-monitor.

Non è un'enciclopedia del diritto né un motore di ricerca normativo. È una **mappa delle relazioni**: serve a ricostruire percorsi e a vedere chi cita cosa, con i limiti dichiarati in basso.

## Cosa contiene

| | |
|---|---|
| **Nodi** | 857.938 — leggi e decreti, DDL Camera e Senato, decreti-legge e conversioni, emendamenti, dibattiti, sentenze, articoli della Costituzione, persone |
| **Relazioni** | 1.369.447 — riferimenti tra atti, citazioni costituzionali, impugnazioni, emendamenti, deleghe, abrogazioni e sostituzioni, passaggio DDL→legge, firmatari, relatori |
| **Bersagli fuori corpus** | 10.852 atti citati che non sono nel corpus testuale, ricostruiti dal catalogo Normattiva (10.629 collegati al grafo) |
| **Tabelle pubblicate** | 13 mart: nodi, archi, edge in entrata, copertura, metriche, chiavi di ricerca, **indice full-text (BM25)**, relazioni pre-aggregate, emendamenti, testi, massime |
| **Fonti** | 5 repo del Lab, lette da rete |

## Domande a cui risponde

- *Da dove viene il Codice del Terzo settore?* — la catena delega → decreto legislativo.
- *Questa legge è stata impugnata dalla Corte costituzionale?* — sentenze e parametri invocati.
- *Chi cita l'art. 3 della Costituzione nelle leggi ordinarie?* — citazioni costituzionali in ingresso.
- *Questa proposta di legge è diventata legge?* — il ponte tra atti parlamentari e norme.
- *Quali norme sono più citate, critiche o vicine all'obsolescenza?* — metriche e report.

## Come accedervi

### MCP del Lab

Per agenti e per chi lavora in chat:

```text
legal_search("responsabilita amministrativa")
  → legal_node(id)                  # contesto del nodo
  → legal_node(id, view="chain")    # catena legislativa
  → legal_neighbors(id)             # vicini (in/out, relazione o canonica)
  → legal_text(id)                  # testo, quando disponibile
```

| Tool | Cosa fa |
|---|---|
| `legal_search` | Trova atti per parole, tipo, anno, fonte, stato, materia, collezione |
| `legal_node` | Contesto: `overview` · `chain` · `jurisprudence` · `parliament` |
| `legal_text` | Testo di un atto; `articolo=N` estrae l'articolo; `doc=<doc_id>` serve un allegato (codici: il testo è negli `<attachments>`, elencati in `attachments`) |
| `legal_neighbors` | Vicini per direzione, relazione esatta o canonica (25→15) |
| `legal_coverage` | Copertura per relazione e livelli L1-L2 |
| `legal_query` | SQL in sola lettura su nodi e archi |
| `legal_insights` | Report su atti critici, obsoleti e complessi |

### SQL diretto

I mart sono parquet pubblici su Google Cloud Storage: si interrogano con DuckDB senza scaricare altro.

```sql
SELECT e.source_id, n.title
FROM read_parquet('https://storage.googleapis.com/dataciviclab-mart/legal-graph/legal_graph/2026/mart_legal_edges.parquet') e
JOIN read_parquet('https://storage.googleapis.com/dataciviclab-mart/legal-graph/legal_graph/2026/mart_legal_nodes.parquet') n
  ON n.id = e.source_id
WHERE e.relation = 'cita_costituzione'
  AND e.target_id = 'costituzione:art:3'
LIMIT 10;
```

### Build locale

```bash
make run           # compone i mart da rete in out/ (non versionato)
make download-gcs  # oppure scarica i mart pubblicati
make test          # integrità del grafo, ricerca, bilancio di copertura
```

Dettagli su compose, variabili d'ambiente e CI in [COMPOSE.md](COMPOSE.md).

## Come è fatto

```text
fonti del Lab (GitHub / GCS)
  → compose toolkit (dataset.yml + SQL)
  → 10 mart su GCS
  → MCP thin: search · node · text · query · insights
```

Il grafo è **ricomposto ogni settimana** dalle fonti e pubblicato su GCS. Non vengono clonati i repo dati né duplicati i testi: il testo si legge da `legal_text`, che dichiara sempre la provenienza.

## Limiti

Legal Graph è un **motore di contesto relazionale** sul processo legislativo, sulla Costituzione e sulla normativa di sistema recente. Non è "tutto il diritto italiano".

- **Coperto bene**: catene legislative, attuazione delle deleghe, modifiche e abrogazioni, Corte costituzionale, firmatari e relatori, decreti legislativi di sistema.
- **Scoperto**: corpus normativo completo (20.790 atti contro i 205.117 del catalogo), codici articolo per articolo, giurisprudenza ordinaria, EUR-Lex, PNRR come dataset.
- **Citazioni**: derivano dai riferimenti Akoma Ntoso di Normattiva, non sono rinvii verificati. Sui titoli generici la fonte può sbagliare bersaglio. Le relazioni tipizzate (`abroga`, `sostituisce`) sono più precise del `riferimento` generico; per `attua_delega` la forza dipende da `method`: `title` (la delega è nominata nel titolo dell'atto, estratta con locator) batte `regex` (dedotta da una citazione, inferita).
- **Copertura dichiarata**: 193.217 riferimenti su 267.636 coppie (72,2%) e 3.733 abrogazioni su 4.199 (88,9%). Le direttive UE citate restano fuori perché non sono nel catalogo italiano ([issue #124](https://github.com/dataciviclab/italia-corpus/issues/124)).
- **Vigenza**: `stato` è lo status formale di Normattiva, a livello di atto; non cattura abrogazioni implicite o parziali.
- **Testo**: `legal_text` restituisce il documento o il singolo articolo, non l'atto consolidato. Per gli atti fuori dal corpus risponde con il link a Normattiva; per le **direttive UE** con il link EUR-Lex (`note`, non `error`).
- **Sempre**: i dati vengono dal mart legal-graph, non da Normattiva in tempo reale.

La mappa completa di cosa è coperto e cosa no è in [docs/COVERAGE.md](docs/COVERAGE.md).

## Partecipa

- **Analisi e confronto**: [Discussions di questo repo](../../discussions), con il bank di domande in [docs/ANALISI.md](docs/ANALISI.md).
- **Intake pubblico del Lab**: [Discussions DataCivicLab](https://github.com/orgs/dataciviclab/discussions).
- **Issue cross-repo**: [dataciviclab/dataciviclab](https://github.com/dataciviclab/dataciviclab/issues).
- **Contributi**: [come contribuire al Lab](https://github.com/dataciviclab/dataciviclab/blob/main/COME-CONTRIBUIRE.md).

## Licenza e stato

[![CI](https://github.com/dataciviclab/legal-graph/actions/workflows/ci.yml/badge.svg)](https://github.com/dataciviclab/legal-graph/actions/workflows/ci.yml)

Dati: pubblico dominio dalle fonti ufficiali (Normattiva, Corte costituzionale, Parlamento).
Codice: [MIT](LICENSE) — DataCivicLab.

Storia delle modifiche e numeri dei build precedenti: [CHANGELOG.md](CHANGELOG.md).
Chiavi condivise tra i repo: [KEYS.md](KEYS.md).
