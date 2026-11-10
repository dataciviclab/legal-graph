# Copertura legal-graph — mappa onesta

Data: 2026-10-08 · aggiornata 2026-11-09 · Fonte: build locale 2026-11-09 (il mart pubblico si aggiorna al prossimo run)

> 2026-11-09 — build con **860.469 nodi / 1.346.607 archi**, direttive UE incluse. Le correzioni di semantica del 2026-11-01 restano valide: `invoca_parametro` tiene solo i parametri costituzionali (`parametro_codice=25`) e solo articoli 1..139 · i documenti AKN Senato senza omologo open-politica diventano il nodo canonico dell'atto (un oggetto, un nodo) · 0 target dangling.

## Cosa è (in sintesi)

**Motore di contesto relazionale** su:

- **processo legislativo Senato** (emendamenti, dibattimenti, DDL)
- **Costituzione** (articoli + Corte Cost. + massime)
- **normativa “di sistema” recente** (~21k atti: D.Lgs UE, appalti, 231, privacy, Terzo settore)

> **Ricerca (2026-11-10)** — `legal_search` per tema usa un indice BM25
> (`mart_legal_search_terms`) costruito su `italia-corpus/testi`: copre il
> testo dei **20.790 atti** del corpus e il titolo di tutti gli atti
> navigabili. Gli atti senza testo nel corpus (es. L. 241/1990) restano
> cercabili per titolo, ma non per contenuto. Il ranking somma il punteggio
> testuale al log delle citazioni in ingresso: un codice molto citato batte
> un atto che nomina il tema una volta.

Non è un’enciclopedia del diritto italiano né un database giurisprudenza ordinaria.

## Bilancio di copertura del compose (2026-11-01)

Il compose scarta le righe che non riesce a collegare. Prima questo era
invisibile: il test di integrità vedeva **0 dangling**, perché gli edge non
risolti non vengono proprio emessi. Ora il funnel è un mart:

```sql
SELECT relation, upstream_rows, natural_pairs, edges, edge_pct, note
FROM read_parquet('out/data/mart/legal_graph/2026/mart_legal_coverage.parquet')
WHERE upstream IS NOT NULL;
```

| Relazione | Righe upstream | Coppie distinte | Edge | Copertura | Cosa si perde |
|---|---:|---:|---:|---:|---|
| `riferimento` | 267.636 | 267.636 | 221.345 | 82,7% | bersagli senza nodo (URN ambigui/malformati), guardia temporale; direttive UE incluse |
| `emendamento` | 763.690 | 561.270 | 494.259 | 88,1% | fasi senza DDL e directory replicata (ambiguo), atti fuori corpus |
| `invoca_parametro` | 256.934 | 52.738 | 52.738 | 100% | righe con parametro di un altro atto o fuori 1..139, più duplicazione snapshot (256.934 righe → 52.738 coppie) |
| `diventa_legge` | 7.520 | 4.496 | 3.574 | 79,5% | URN di legge assenti dal corpus (DDL Senato 63% in corpus, leggi Camera 33%) |
| `abroga` | 4.199 | 4.199 | 3.733 | 88,9% | target senza nodo, guardia temporale |

Le altre 20 relazioni sono 1:1 (una riga upstream → un edge): il bilancio le
elenca con `upstream` NULL invece di inventare un denominatore.

**Nodi catalogo (2026-11).** I bersagli fuori corpus che esistono nel catalogo
Normattiva hanno un nodo `source='catalogo'` (**10.852 nodi** da `atti_index`, solo
identità: nessun testo, ma con `materia` dal classificatore IC). **10.629** hanno
almeno un edge; i 223 restanti (2,1%) sono citazioni ad atti che il catalogo non
contiene nella stessa forma o che cadono nella guardia temporale. I `riferimento`
puntano a **10.594** nodi catalogo e **130** nodi `atti_api`.
Le citazioni all'atto-Costituzione (`...;const`, **4.966 edge**) hanno un nodo
proprio `costituzione:atto`, non un articolo: sono citazioni all'atto intero.
Le direttive UE non sono nel catalogo italiano: hanno un nodo dedicato
`celex:*` dal dataset `direttive_ue` (identità CELEX, non testo), con **2.343
direttive risolte su 2.672**. Restano fuori **329 direttive non in Cellar**,
**1.373 URN malformati** (`urn:nir::…`, senza emittente) e circa **19.900**
bersagli non risolti al file: di questi ~11.400 esistono comunque in
`atti_index` e diventano nodi catalogo, gli altri non identificano un atto del
catalogo. Gli URN malformati restano **esclusi per scelta**.

> Direttive UE incluse dal 2026-11-09: **2.343 nodi `celex:*`** (su 2.672) e
> **28.128 edge** `riferimento`. Il mart pubblico si aggiorna al prossimo run
> della pipeline.

Limiti dichiarati:

- **`invoca_parametro` non è "la Corte cita la Costituzione e basta"**: la
  massima ha un parametro per ogni atto richiamato (statuti regionali, codici,
  CEDU, TFUE). Il grafo tiene solo `parametro_codice=25` (Costituzione): prima
  del 2026-11 ogni parametro diventava `costituzione:art:N` (13.649 edge su
  66.368, 20,6%). Esempio: l'art. 145 del codice civile (sent. 6/1971) finiva
  su `costituzione:art:145`, che non esiste.
- **`edge_pct` misura il compose, non la fonte.** I riferimenti che
  italia-corpus non risolve non arrivano nemmeno in `riferimenti`: quel buco
  sta a monte (IC #86, copertura citazioni AKN ~39%).
- **`abroga` resta un buco di fonte**: 7.668 nodi `stato=abrogato` su 8.051 non
  hanno un arco in ingresso. `akn_n_repeal_events` è 0 su tutti gli atti IC:
  il lifecycle AKN non viene estratto, quindi non c'è nulla da comporre.
- **Fallback rimossi (2026-11)**: i nodi `norma:*` (atti citati dalla Corte assenti dal corpus) ora esistono **solo** se sono target di un edge `impugna`: prima ogni norma citata da una massima creava un nodo, e 5.000 su 8.439 restavano senza relazioni. I 213 DL presenti anche come URN non creano più un nodo `dl:*` (edge `converte_decreto_legge` spostato sull'URN). Effetto: −5.213 nodi, 0 orfani tra i `norma:*`, 0 duplicati `dl:`.
- **Emendamenti**: l'edge `emendamento` si risolve sulla **`fase`** (S.NNN) → `senato_ddl.id_ddl` → nodo `senato:{id_ddl}`; solo in fallback, per le fasi fuori da open-politica con directory unica (`n_atti_fase = 1`), usa `atto_num`. `atto_num` è la directory di archiviazione e può essere replicata (fino a 57 copie per fase; dedup upstream 2026-11): usarla come chiave attribuiva lo stesso emendamento a più atti. Ora **494.259 edge su 561.270 coppie distinte (88,1%)**: il denominatore del funnel è `(legislatura, fase, numero)`, la stessa chiave dell'edge — con `atto_num` erano 568.252 e la copertura veniva sottostimata (85,8%). Il nodo emendamento è `senato:emend:{leg}:{fase}:{numero}`: l'atto entra nell'id, quindi non esistono più nodi che aggregano emendamenti di atti diversi (erano 439 con >100 atti, max 427); i nodi passano da 248.184 a **561.270**.

Copertura per legislatura (snapshot 2026-11-01, quando il totale era 487.538/561.270): **19 100%**, 18 98,1%, 17 89,0%, 16 77,1%, 15 98,5%, 14 59,4%. I mancanti sono fasi senza DDL con directory replicata (ambigue, non si indovina) e atti fuori corpus.
- **Identità Senato**: un atto del corpus con nodo open-politica esiste come
  documento (`senato:atto:X`) **e** come atto (`senato:X`), collegati da
  `testo_atto`. Gli atti senza nodo open-politica (2.990) non creano più un
  secondo nodo: il documento è l'atto. Effetto: −2.990 nodi, −2.990
  `testo_atto`, e ~2.900 atti che risultano isolati perché l'unico arco che
  avevano li collegava a sé stessi via un id duplicato.
- **Autoanelli rimossi** (916 `riferimento`, 97 `abroga`): un atto non è in
  relazione con sé stesso. Prima il self-loop del D.Lgs 58/1998 (weight 436)
  era l'edge più pesante del grafo e entrava in `impact_score`.
  **Effetto**: `referenced_by`/`impact_score` cambiano per i **916 atti** che
  avevano l'autoanello; i numeri pubblicati prima del 2026-10-30 li includono.

## Evidenza formulaica (`is_formulaic`)

`edges.is_formulaic` marca le citazioni in cui lo **stesso** snippet è usato da
più di 50 atti distinti: **4.311 edge** (2,0% dei `riferimento`), di cui 2.729
al D.P.R. 1092/1985 (la clausola di pubblicazione che ogni preambolo ripete).
Il match è **esatto**: normalizzare lo snippet (lower, primi N caratteri)
porterebbe a 14.178 edge su 1.790 target e catturerebbe citazioni AKN legittime
di conversione.

Secondo segnale, **semantico e della fonte**: `edges.is_reading_clause` viene da
`riferimenti.clausola_lettura` (le citazioni del tipo "…si legge…"). Sono **4.238
edge**, quasi tutti verso il D.P.R. 1092; 2.712 hanno anche `is_formulaic`, 1.526
solo la clausola. La soglia su stringhe non li avrebbe visti, perché il testo
varia ("art. 10, comma 2", "comma 3", …).

`mart_legal_node_metrics` esclude da `referenced_by`/`impact_score` sia i
formulaici sia le clausole di lettura, ed espone `formulaic_by` (la somma dei
due segnali): il lordo è `referenced_by + formulaic_by`. Effetto: il D.P.R.
1092 passa da 4.835 a **597** riferimenti e la classifica "critical" torna
dominata dalla Costituzione (art. 3, 24, 117, 76, 97).

`mart_legal_coverage` dichiara `formulaic_edges`/`formulaic_pct` e
`reading_clause_edges` per relazione:
il funnel conta il **lordo**, così non sembra migliorato solo perché etichettato.

**Limite noto, ora misurabile**: `edges.evidence_kind` classifica l'evidenza dei
`riferimento` in tre stati — `missing` (segnaposto `akn:ref`), `locator`
(frammento URL/URN) e `quote` (testo). Oggi `missing` è **139.943 edge (63,2%)**:
la regola >50 giudica solo gli 81.402 con testo. `coverage` espone
`missing_evidence_edges`/`missing_evidence_pct`, così il target (0) è misurato:
dipende da **italia-corpus#109**, non da una soglia.

## Composizione nodi (~860k)

| Blocco | Quota | Stato |
|---|---:|---|
| Processo Senato (emend + dibattiti) | 79,7% | Sovrabbondante per “legge su X”, ottimo per process mining |
| Votazioni Senato | 3,4% | Coperto per DDL in senato_ddl |
| Attributi Senato (DDL/atti) | 5,0% | Buono |
| Costituzione (art. + pronunce + correlati) | 2,9% | Buono |
| Normativa statale (`source=normativa`) | 2,4% | Sottocoperta vs catalogo (~21k atti su 205k) |
| Camera DDL | 4,0% | Sottocoperto vs Senato |
| Iter cost. + revisioni | 0,3% | Ponte OP |
| Deputati e relatori Camera | 0,6% | ✅ Ponte OP |
| Catalogo Normattiva (bersagli fuori corpus) | 1,3% | Solo identità, nessun testo |
| Direttive UE (identità) | 0,3% | Solo identità, testo su EUR-Lex |
| PNRR / GU / DL | ~0% | Quasi assente |

## Qualità IC (stato / materia / score)

Su nodi `source=normativa` il mart espone da **italia-corpus**:

| Colonna | Significato |
|---|---|
| `stato` | `vigente` \| `abrogato` \| `decaduto` — **status autorevole** Normattiva, atto-level: `abrogato` = classe 3 (lista ufficiale "Atti normativi abrogati") **o** nota whole-act `PROVVEDIMENTO ABROGATO`; `decaduto` = collezione "DL decaduti". Propagato a **tutte** le snapshot dell'atto |
| `vigenza_fonte` | Origine di `stato`: `normattiva` (presente nella lista ufficiale abrogati, classe 3) \| `non_coperto` (assente dalla lista ma con nota whole-act) \| `esclusione` (assente → `vigente` per esclusione) \| `atti_api` (atto fuori collezione IC, `stato` dall'API `dettaglio-atto-urn`) |
| `materia` | 25 categorie tematiche (fisco, ambientale, lavoro…) |
| `qualita_score` | 0–100 (IC: penalizza duplicati/orfani/stato non vigente) |
| `sunsetting_score` | 0–100 (IC PR #54: propensione a decadere) |
| `eiv` | Entrata in vigore effettiva da AKN (`akn_act_meta`) — 48% nodi normativa, **per natura**: copertura 100% ≤1944 e ≥2011, ~0% sul 1946-2010 (Normattiva non espone authorialNote EIV in quella finestra — verificato 2026-10-08, non è extraction miss). Spesso ≠ `data` (emanazione) |

**Limiti**: `stato` è lo status **formale** atto-level di Normattiva (classe 3 ∪ nota whole-act): non cattura abrogazioni implicite/parziali (article-level). Sui nodi `normativa`: `esclusione` **11.041** (52,7%) · `normattiva` **9.711** (46,3%) · `non_coperto` **38** · `atti_api` **171**. `esclusione` = `vigente` **per esclusione** (assente dalla lista ufficiale abrogati) → trattare come "non risulta abrogato", non come certezza. Hub ordinarie (190/2012, 241/1990…) fuori collezione IC compaiono come nodi `atti_api` (`stato` dall'API `dettaglio-atto-urn`, non dal clean). MCP: filtro `legal_search(..., stato='vigente')`.

## Riferimenti (`riferimento`) — limite di fonte

Gli archi `riferimento` (citazioni tra atti) **non sono rinvii verificati**: derivano dai `<ref>`/`<destination>` dell'AKN di **Normattiva**, convertiti in link markdown da italia-corpus (`akn_parser.resolve_ref`). La fonte ha cross-reference **ambigui sui titoli generici**: es. la frase-titolo *"Codice dei contratti pubblici"* è linkata al **163/2006** anche in una norma del **2026** che cita il **36/2023** → l'arco punta a un atto abrogato mentre il testo cita quello vigente (delle 48 citazioni del 163/2006 nel 2024-26, solo 2 hanno link coerente).

Conseguenza: analisi basate sulle citazioni (es. "norme vigenti che citano norme abrogate") sono **sovrastimate** e vanno qualificate. Le relazioni **tipizzate** (`abroga`, `modifica` — da AKN `lifecycle`) sono più affidabili del `riferimento` generico. Verificato 2026-10-26.

**`attua_delega` (2026-11-10)** — Due livelli, esposti su ogni edge: `method='title'` (471 edge: 290 dal corpus normativa + 181 da `atti_api`) = il titolo del D.Lgs nomina la legge delegante («in attuazione dell'articolo 1 della legge …»), `assertion_type='extracted'`, `prov_evidence_kind='locator'`; `method='regex'` (2.221) = la legge bersaglio ha «delega al Governo» nel titolo ed è citata dall'atto, `assertion_type='inferred'`, evidenza = snippet citante (o `none` se AKN non dà contesto). Il metodo è un flag esplicito dei due CTE, non il peso: il peso regex è il conteggio delle citazioni e può essere ≥5. La vista `chain` mette davanti le deleghe `title` e chiude con le `regex`: non spariscono, ma non si spacciano per catena verificata. Solo 471/2.692 (17,5%) hanno la delega nominata nell'atto.

## Ponti open-politica

| Ponte | Nel grafo |
|---|---|
| Relatore → ddl | ✅ edge `relatore` (`senatore:*` → `senato:*`) |
| Relatore → atto Camera | ✅ edge `relatore` (`deputato:*` → `camera:{legislatura}:{num}`) — ~19% incarichi (atto LOD parziale per design); l'id storico `camera:atto:{leg}_{id}` resta alias |
| Firmatario → atto Camera | ✅ edge `firmatario` (`deputato:*` → `camera:{legislatura}:{num}`) |
| Firmatario → ddl Senato | ✅ edge `firmatario` (`senatore:*` → `senato:*`, weight 2 se primo) |
| DL conversione (Leg13-19) | ✅ nodi `dl:{anno}-{numero}` + edge `converte_decreto_legge` → `senato:{ddl_id}` (esito+giorni in evidence; ddl_id representative) |
| Votazione → DDL | ✅ edge `vota` (`votazione:*` → `senato:*`, evidence = esito) |
| Iter revisioni Cost. | ✅ nodi `itercost:*` + edge `proposta_cost` / `diventa_revisione` |
| Voti individuali / profilo | ❌ compose OP, non grafo |

Nota `vota`: copre DDL presenti in `senato_ddl` clean (~80% dei ddl_id votati).  
Nota `itercost`: nodi deduplicati (`camera_o_senato`+`atto_num`); `proposta_cost` solo su target **davvero Cost.** (filtro titolo/tipo — `atto_num` Camera collide coi DDL ordinari); `diventa_revisione` solo `ha_legge=1` con `rev_urn` nel mart (~29 revisioni).  
`diventa_legge` Camera/Senato: solo archi con source **e** target nel mart (niente dangling).

## Livelli di copertura

| Livello | Verdetto |
|---|---|
| **A. Struttura relazionale** (deleghe, DDL→legge, impugnazioni Cost., emendamenti) | ✅ Coperto |
| **B. Normativa di sistema recente** (231, 24/2023, 101/2018, 50/2016, 117/2017…) | ✅ Coperto |
| **C. Corpus normativo completo 1861–oggi** | ❌ Scoperto (~21k vs 205k del catalogo) |
| **D. Codici navigabili art. per art.** (c.c., c.p., c.p.c.) | ❌ Scoperto (Cost. sì) |
| **E. Giurisprudenza** | ⚠️ Solo Corte Cost. |
| **F. Camera / regioni / GU / EUR-Lex** | ❌ Scoperto o sottile |
| **G. Dati applicativi** (PNRR milestones, gare, bilanci enti) | ❌ Fuori grafo (servono dataset Lab) |

## Temi

### Forti
Processo Senato · Costituzione + Corte Cost. · responsabilità enti/anticorruzione (parziale) · appalti · lavoro · istruzione/sanità/difesa · recepimento UE via D.Lgs

### Deboli
Tributario · penale (modifiche, non TU navigabile) · privacy come corpus articoli · **241/1990 in grafo via Normattiva API (vigenza 2020)** · PNRR (16 nodi, non dataset) · Terzo settore (rumore DDL) · regioni · codici · Camera asimmetrica

> Nota 2026-10-08: il **D.Lgs 33/2013 (FOIA)** è un hub critical del grafo (115 incoming) — prima documentato come gap sull'errato "33/2011".

### Scoperti
Giurisprudenza ordinaria/amm. · testo consolidato articoli codici · **EUR-Lex (solo identità delle direttive citate)** · GU come fonte · **vigenza live (stato IC = tombstone)** · ordine giudiziario · INPS/sicurezza sociale · enti locali come nodi

## Cosa significa per il Lab

1. **Valore**: “come nascono e si collegano le leggi” (catene, Cost., processo) — non “tutto il diritto”.
2. **Se chiedi l’art. 2043 c.c. consolidato**: il grafo non basta — usa Normattiva/IC.
3. **Diritto applicato (PA, enti, gare, giurisprudenza ord.)**: integrare dataset Lab, non solo grafo.
4. **~80% è processo Senato** (emend+dibattiti): power/process mining sì; ricerca “umana” solo con search tema + hub.

## Priorità di copertura

| Pri | Azione | Sforzo |
|---|---|---|
| P0 | Disclaimer + 1 analisi pubblica su ciò che funziona | Basso |
| P0 | ~~Hub: 33/2013~~ ✅ · ~~241/1990 e 190/2012~~ ⚠️ L. 241/1990 in grafo via API; L. 33/2011 ancora assente; nodi `norma:*` residui per atti non in corpus | Basso |
| P1 | Search tema umano stabile (glossario Lab) | Basso (MCP) |
| P1 | Hub codici (c.c./c.p.) se servono domande da non-giurista | Medio |
| P2 | Camera + poche regioni; PNRR come dataset dedicato | Medio–alto |
| P3 | Giurisprudenza ord./amm.; corpus full | Alto |
| No | Ricostruire Normattiva live / EUR-Lex | — |

## Domande analisi (bank)

Vedi [ANALISI.md](ANALISI.md) — query pronte per MCP + Discussion sul repo.
