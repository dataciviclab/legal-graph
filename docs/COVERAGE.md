# Copertura legal-graph — mappa onesta

Data: 2026-10-08 · Fonte: mart `out/data/mart/legal_graph/2026/` (non Normattiva live)

## Cosa è (in sintesi)

**Motore di contesto relazionale** su:

- **processo legislativo Senato** (emendamenti, dibattimenti, DDL)
- **Costituzione** (articoli + Corte Cost. + massime)
- **normativa “di sistema” recente** (~21k atti: D.Lgs UE, appalti, 231, privacy, Terzo settore)

Non è un’enciclopedia del diritto italiano né un database giurisprudenza ordinaria.

## Composizione nodi (~508k)

| Blocco | Quota | Stato |
|---|---:|---|
| Processo Senato (emend + dibattiti) | ~73% | Sovrabbondante per “legge su X”, ottimo per process mining |
| Votazioni Senato | ~6% | Coperto per DDL in senato_ddl |
| Attributi Senato (DDL/atti) | ~8% | Buono |
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
| `stato` | `vigente` \| `abrogato` \| `decaduto` — **marker tombstone VIGENZA**, non vigenza live di tutto il corpus |
| `materia` | 25 categorie tematiche (fisco, ambientale, lavoro…) |
| `qualita_score` | 0–100 (IC: penalizza duplicati/orfani/stato non vigente) |
| `sunsetting_score` | 0–100 (IC PR #54: propensione a decadere) |
| `eiv` | Entrata in vigore effettiva da AKN (`akn_act_meta`) — ~48% dei nodi normativa; spesso ≠ `data` (emanazione) |

**Limiti**: atti solo ORIGINALE restano `vigente` per costruzione; hub ordinarie (190/2012, 241/1990…) fuori collection IC possono non comparire. MCP: filtro `legal_search(..., stato='vigente')`.

## Ponti open-politica

| Ponte | Nel grafo |
|---|---|
| Relatore → ddl | ✅ edge `relatore` (`senatore:*` → `senato:*`) |
| Firmatario → atto Camera | ✅ edge `firmatario` (`deputato:*` → `camera:*`) |
| Firmatario → ddl Senato | ✅ edge `firmatario` (`senatore:*` → `senato:*`, weight 2 se primo) |
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
Tributario · penale (modifiche, non TU navigabile) · privacy come corpus articoli · **241/1990 solo nodo `norma:*` (massime, niente testo)** · PNRR (20 nodi, non dataset) · Terzo settore (rumore DDL) · regioni · codici · Camera asimmetrica

> Nota 2026-10-08: il **D.Lgs 33/2013 (FOIA)** è un hub critical del grafo (115 incoming) — prima documentato come gap sull'errato "33/2011".

### Scoperti
Giurisprudenza ordinaria/amm. · testo consolidato articoli codici · EUR-Lex · GU come fonte · **vigenza live (stato IC = tombstone)** · FTS · ordine giudiziario · INPS/sicurezza sociale · enti locali come nodi

## Cosa significa per il Lab

1. **Valore**: “come nascono e si collegano le leggi” (catene, Cost., processo) — non “tutto il diritto”.
2. **Se chiedi l’art. 2043 c.c. consolidato**: il grafo non basta — usa Normattiva/IC.
3. **Diritto applicato (PA, enti, gare, giurisprudenza ord.)**: integrare dataset Lab, non solo grafo.
4. **~73% è processo Senato** (emend+dibattiti): power/process mining sì; ricerca “umana” solo con search tema + hub.

## Priorità di copertura

| Pri | Azione | Sforzo |
|---|---|---|
| P0 | Disclaimer + 1 analisi pubblica su ciò che funziona | Basso |
| P0 | ~~Hub: 33/2013~~ ✅ già hub critical · 241/1990 e 190/2012: servono nodi URN (gap IC) | Basso |
| P1 | Search tema umano stabile (glossario Lab) | Basso (MCP) |
| P1 | Hub codici (c.c./c.p.) se servono domande da non-giurista | Medio |
| P2 | Camera + poche regioni; PNRR come dataset dedicato | Medio–alto |
| P3 | Giurisprudenza ord./amm.; corpus full | Alto |
| No | Ricostruire Normattiva live / EUR-Lex | — |

## Domande analisi (bank)

Vedi [ANALISI.md](ANALISI.md) — query pronte per MCP + Discussion sul repo.
