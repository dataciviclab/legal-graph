# Copertura legal-graph — mappa onesta

Data: 2026-10-08 · aggiornata 2026-11-01 · Fonte: mart `out/data/mart/legal_graph/2026/` (non Normattiva live)

> 2026-11-01 — tre correzioni di semantica, tutte misurabili nel mart:
> `invoca_parametro` tiene solo i parametri costituzionali (`parametro_codice=25`)
> e solo articoli 1..139 · i documenti AKN Senato senza omologo open-politica
> diventano il nodo canonico dell'atto (un oggetto, un nodo) · 0 target dangling.
> Numeri sotto: **831.474 nodi / 1.201.349 archi**, 25 relazioni.

## Cosa è (in sintesi)

**Motore di contesto relazionale** su:

- **processo legislativo Senato** (emendamenti, dibattimenti, DDL)
- **Costituzione** (articoli + Corte Cost. + massime)
- **normativa “di sistema” recente** (~21k atti: D.Lgs UE, appalti, 231, privacy, Terzo settore)

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
| `riferimento` | 200.080 | 126.593 | 101.583 | 80,2% | snapshot dello stesso atto (righe→coppie), mappatura URN, guardia temporale |
| `emendamento` | 763.690 | 568.252 | 487.538 | 85,8% | fasi senza DDL e directory replicata (ambiguo), atti fuori corpus |
| `invoca_parametro` | 256.827 | 52.719 | 52.719 | 100% | 65.611 righe con parametro di un altro atto + 10 fuori 1..139; il resto è duplicazione snapshot (191.206 righe → 52.719 coppie) |
| `diventa_legge` | 7.478 | 4.479 | 2.960 | 66,1% | URN di legge assenti dal corpus (DDL Senato 63% in corpus, leggi Camera 33%) |
| `abroga` | 8.374 | 8.374 | 3.633 | 43,4% | atto abrogante/abrogato fuori corpus, contesto regex ambiguo |

Le altre 20 relazioni sono 1:1 (una riga upstream → un edge): il bilancio le
elenca con `upstream` NULL invece di inventare un denominatore.

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
- **Emendamenti**: l'edge `emendamento` si risolve sulla **`fase`** (S.NNN) → `senato_ddl.id_ddl` → nodo `senato:{id_ddl}`; solo in fallback, per le fasi fuori da open-politica con directory unica (`n_atti_fase = 1`), usa `atto_num`. `atto_num` è la directory di archiviazione e può essere replicata (fino a 57 copie per fase; dedup upstream 2026-11): usarla come chiave attribuiva lo stesso emendamento a più atti. Ora **487.538 edge su 568.252 coppie distinte (85,8%)**, e il nodo emendamento è `senato:emend:{leg}:{fase}:{numero}`: l'atto entra nell'id, quindi non esistono più nodi che aggregano emendamenti di atti diversi (erano 439 con >100 atti, max 427). I nodi emendamento passano da 248.184 a **561.270**. Copertura per legislatura: 19 al 100%, 18 al 98,1%, 17 all'88,9%, 16 all'82,7%, 15 al 98,5%, 14 al 66,7%; i mancanti sono fasi senza DDL e directory replicata (ambigue, non si indovina) e atti fuori corpus.
  **Attenzione al denominatore**: i nodi `senato:emend:{leg}:{emend_id}` sono 248.127 perché `emend_id` è il numero *dentro* l'atto — lo stesso "1.1" esiste in centinaia di atti. Contare i nodi dà 22.377 orfani (9%), ma gli emendamenti orfani sono 124.083 (15,7%): la quota vera sta nel funnel `coverage`, non nel conteggio dei nodi. L'identità del nodo va corretta (`atto_num` nell'id): issue da aprire.
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
più di 50 atti distinti: **3.140 edge** (3,1% dei `riferimento`), di cui 2.727
al D.P.R. 1092/1985 (la clausola di pubblicazione che ogni preambolo ripete).
Il match è **esatto**: normalizzare lo snippet (lower, primi N caratteri)
porterebbe a 14.178 edge su 1.790 target e catturerebbe citazioni AKN legittime
di conversione.

Secondo segnale, **semantico e della fonte**: `edges.is_reading_clause` viene da
`riferimenti.clausola_lettura` (le citazioni del tipo "…si legge…"). Sono **4.221
edge**, quasi tutti verso il D.P.R. 1092; 2.710 hanno anche `is_formulaic`, 1.511
solo la clausola. La soglia su stringhe non li avrebbe visti, perché il testo
varia ("art. 10, comma 2", "comma 3", …).

`mart_legal_node_metrics` esclude da `referenced_by`/`impact_score` sia i
formulaici sia le clausole di lettura, ed espone `formulaic_by` (la somma dei
due segnali): il lordo è `referenced_by + formulaic_by`. Effetto: il D.P.R.
1092 passa da 4.831 a **597** riferimenti e la classifica "critical" torna
dominata dalla Costituzione (art. 3, 24, 117, 76, 97).

`mart_legal_coverage` dichiara `formulaic_edges`/`formulaic_pct` e
`reading_clause_edges` per relazione:
il funnel conta il **lordo**, così non sembra migliorato solo perché etichettato.

**Limite noto, ora misurabile**: `edges.evidence_kind` classifica l'evidenza dei
`riferimento` in tre stati — `missing` (segnaposto `akn:ref`), `locator`
(frammento URL/URN) e `quote` (testo). Oggi `missing` è **57.002 edge (56,1%)**:
la regola >50 giudica solo i 44.577 con testo. `coverage` espone
`missing_evidence_edges`/`missing_evidence_pct`, così il target (0) è misurato:
dipende da **italia-corpus#109**, non da una soglia.

## Composizione nodi (~522k)

| Blocco | Quota | Stato |
|---|---:|---|
| Processo Senato (emend + dibattiti) | ~82% | Sovrabbondante per “legge su X”, ottimo per process mining |
| Votazioni Senato | ~5,6% | Coperto per DDL in senato_ddl |
| Attributi Senato (DDL/atti) | ~6,6% | Buono |
| Costituzione (art. + pronunce + correlati) | ~6% | Buono |
| Normativa statale (`source=normativa`) | ~4% | Sottocoperta vs corpus pieno (~288k file IC) |
| Camera DDL | ~1,5% | Sottocoperto vs Senato |
| Iter cost. + revisioni | ~0,5% | Ponte OP |
| Deputati (firmatari Camera) | ~0,5% | ✅ Ponte OP `camera_firmatari` |
| PNRR / GU | ~0% | Quasi assente |

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

**Limiti**: `stato` è lo status **formale** atto-level di Normattiva (classe 3 ∪ nota whole-act): non cattura abrogazioni implicite/parziali (article-level). Sui nodi `normativa`: `esclusione` **~10.800** (~52%) · `normattiva` **~9.900** (~47%) · `non_coperto` **~35** · `atti_api` **~171**. `esclusione` = `vigente` **per esclusione** (assente dalla lista ufficiale abrogati) → trattare come "non risulta abrogato", non come certezza. Hub ordinarie (190/2012, 241/1990…) fuori collezione IC compaiono come nodi `atti_api` (`stato` dall'API `dettaglio-atto-urn`, non dal clean). MCP: filtro `legal_search(..., stato='vigente')`.

## Riferimenti (`riferimento`) — limite di fonte

Gli archi `riferimento` (citazioni tra atti) **non sono rinvii verificati**: derivano dai `<ref>`/`<destination>` dell'AKN di **Normattiva**, convertiti in link markdown da italia-corpus (`akn_parser.resolve_ref`). La fonte ha cross-reference **ambigui sui titoli generici**: es. la frase-titolo *"Codice dei contratti pubblici"* è linkata al **163/2006** anche in una norma del **2026** che cita il **36/2023** → l'arco punta a un atto abrogato mentre il testo cita quello vigente (delle 48 citazioni del 163/2006 nel 2024-26, solo 2 hanno link coerente).

Conseguenza: analisi basate sulle citazioni (es. "norme vigenti che citano norme abrogate") sono **sovrastimate** e vanno qualificate. Le relazioni **tipizzate** (`abroga`, `modifica`, `attua_delega` — da AKN `lifecycle`) sono più affidabili del `riferimento` generico. Verificato 2026-10-26.

## Ponti open-politica

| Ponte | Nel grafo |
|---|---|
| Relatore → ddl | ✅ edge `relatore` (`senatore:*` → `senato:*`) |
| Relatore → atto Camera | ✅ edge `relatore` (`deputato:*` → `camera:atto:{leg}_{id}`) — ~19% incarichi (atto LOD parziale per design) |
| Firmatario → atto Camera | ✅ edge `firmatario` (`deputato:*` → `camera:*`) |
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
| **C. Corpus normativo completo 1861–oggi** | ❌ Scoperto (~21k vs ~288k) |
| **D. Codici navigabili art. per art.** (c.c., c.p., c.p.c.) | ❌ Scoperto (Cost. sì) |
| **E. Giurisprudenza** | ⚠️ Solo Corte Cost. |
| **F. Camera / regioni / GU / EUR-Lex** | ❌ Scoperto o sottile |
| **G. Dati applicativi** (PNRR milestones, gare, bilanci enti) | ❌ Fuori grafo (servono dataset Lab) |

## Temi

### Forti
Processo Senato · Costituzione + Corte Cost. · responsabilità enti/anticorruzione (parziale) · appalti · lavoro · istruzione/sanità/difesa · recepimento UE via D.Lgs

### Deboli
Tributario · penale (modifiche, non TU navigabile) · privacy come corpus articoli · **241/1990 in grafo via Normattiva API (vigenza 2020)** · PNRR (20 nodi, non dataset) · Terzo settore (rumore DDL) · regioni · codici · Camera asimmetrica

> Nota 2026-10-08: il **D.Lgs 33/2013 (FOIA)** è un hub critical del grafo (115 incoming) — prima documentato come gap sull'errato "33/2011".

### Scoperti
Giurisprudenza ordinaria/amm. · testo consolidato articoli codici · EUR-Lex · GU come fonte · **vigenza live (stato IC = tombstone)** · FTS · ordine giudiziario · INPS/sicurezza sociale · enti locali come nodi

## Cosa significa per il Lab

1. **Valore**: “come nascono e si collegano le leggi” (catene, Cost., processo) — non “tutto il diritto”.
2. **Se chiedi l’art. 2043 c.c. consolidato**: il grafo non basta — usa Normattiva/IC.
3. **Diritto applicato (PA, enti, gare, giurisprudenza ord.)**: integrare dataset Lab, non solo grafo.
4. **~71% è processo Senato** (emend+dibattiti): power/process mining sì; ricerca “umana” solo con search tema + hub.

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
