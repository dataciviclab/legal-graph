# Analisi con legal-graph — bank di domande

Strumento di **analisi/confronto**: ogni domanda è rispondibile (o quasi) con MCP
`legal_search` → `legal_node` → `legal_text` / `legal_query`.

**Disclaimer**: tutto dal mart legal-graph, non da Normattiva live.
Se il grafo non copre, dichiararlo e passare a un dataset Lab o a una fonte esterna.

---

## Come si usa

1. Apri una **Discussion** su questo repo (categoria *Analisi*) con una domanda della bank.
2. Un agente/umano risponde solo con tool legal-graph + citazioni id/nodi.
3. Se la domanda è “fuori grafo”, la risposta è: *gap + dove cercare altrove*.

---

## A. Struttura e catene (forte)

| # | Domanda | Tool / hint |
|---|---|---|
| A1 | Da dove viene il D.Lgs 231/2001? | search `231/2001` → `legal_node` view=`chain` (attua_delega → L.300/2000) |
| A2 | Quale legge-base attua il Codice Terzo settore? | search `117/2017` → chain → L.106/2016 |
| A3 | Quali D.Lgs recepiscono direttive UE nel mart? | `legal_query` title LIKE `%direttiva%` + `source=normativa` |
| A4 | Qual è la catena privacy pre/post GDPR? | search `196/2003`, `101/2018` → overview + riferimenti |
| A5 | Quante relazioni `attua_delega` ci sono nel grafo? | `legal_query` GROUP BY relation |
| A6 | Questo atto cosa abroga o sostituisce? | search atto → `legal_node` view=`chain` (modifiche AKN: abroga/sostituisce/split/join) |

## B. Costituzione e giurisprudenza (forte)

| # | Domanda | Tool / hint |
|---|---|---|
| B1 | Quali atti citano di più l’art. 3 Cost.? | search `art. 3 della Costituzione` → overview top incoming / `cita_costituzione` |
| B2 | Cosa dice l’art. 13 Cost.? | `legal_text(costituzione:art:13)` da mart texts |
| B3 | Chi ha impugnato la L. 40/2004? | search `legge 40 2004` → jurisprudence / `impugna` |
| B4 | Quali parametri Cost. invoca la sentenza 2009/151? | `legal_node(sentenza:2009-0151, view=jurisprudence)` |
| B5 | Quali massime toccano la L. 190/2012? | `legal_query` su view `massime` WHERE norma_numero |
| B6 | Chi è il relatore della sentenza X / quali sentenze ha redatto il giudice Y? | `legal_node` view=`jurisprudence` → `relatore` / `sentenze_relatore` |

## C. Tema Lab (parziale — dichiarare i limiti)

| # | Domanda | Tool / hint | Limite atteso |
|---|---|---|---|
| C1 | Il whistleblowing UE è nel grafo? | search `whistleblowing` / `2019/1937` → D.Lgs 24/2023 | Ranking tema debole |
| C2 | La legge anticorruzione 190/2012 è cercabile? | search `190/2012` → `norma:legge:190:2012` | Testo integrale non in mart texts |
| C3 | FOIA / accesso civico? | search `FOIA`, `trasparenza` | **33/2011 non è hub** — gap documentato |
| C4 | L. 241/1990 accesso documenti? | search `241/1990` | Solo `norma:*`, non URN hub |
| C5 | PNRR nel grafo? | search `PNRR` | ~20 nodi, non dataset milestones |
| C6 | Appalti: D.Lgs 50/2016 e successori? | search `50/2016`, `36/2023` → chain | |
| C7 | Atti fisco di alta qualità? | search tema + filtri `materia='fisco'`, `min_score=70` | Qualità = marker IC, non validazione giuridica |

## D. Cosa NON chiedere al grafo (fuori scope)

| Domanda | Dove andare |
|---|---|
| Testo consolidato art. 2043 c.c. | Normattiva / italia-corpus |
| Sentenze Cassazione/TAR/CdS | Dataset giurisprudenza Lab (non nel grafo) |
| Vigenza live di una norma | Normattiva |
| Milestone spesa PNRR | Dataset PNMR/MRN del Lab |
| EUR-Lex completo | Fonte UE dedicata |

---

## Template risposta (Discussion)

```text
Domanda: …
Esito: ✅ parziale ❌ fuori grafo
Nodi/relazioni: id, relation, weight
Testo: legal_text? via?
Limiti del mart: …
Prossimo passo: dataset Lab / Normattiva / gap hub
Disclaimer: dal mart legal-graph, non da Normattiva live.
```
