# SPEC — `legal_catena` (MCP legal-graph)

**Status:** implementato v1 (2026-11-23) — `legal_graph/mcp/chain.py`, 10 test in
`tests/test_legal_catena.py` · **Data:** 2026-11-23
**Scope:** un tool verticale che risponde a *una* domanda — *da dove viene questa
norma, e cosa ha prodotto* — con percorso ordinato e copertura dichiarata.

> Regola del modello (`docs/MODEL_CORE.md`): il sistema deve sapere cosa sa,
> cosa non sa, e perché. Un percorso senza copertura viola la regola.

---

## 1. Il problema che risolve

Oggi `legal_node(view="chain")` fa un **BFS fino a 5 hop** e restituisce una
**lista piatta** di nodi raggiunti con la relazione usata (`_view_chain`,
`node_views.py`). Non è un percorso: l'ordine e la direzione li ricostruisce il
modello a parole. Costo misurato su una domanda tipica ("da dove viene questa
legge?"): `legal_search` → `legal_node(chain)` → `legal_node` su 2-3 hop →
forse `legal_neighbors`. **4+ chiamate, esito non deterministico.**

Secondo problema, più grave: **le relazioni non hanno direzione semantica
dichiarata**. `attua_delega` punta dall'attuativo alla delega (verso l'alto),
tutte le altre puntano in avanti. Un LLM che legge l'edge conclude che *la
delega attua il decreto*.

---

## 2. Cosa è una catena (e cosa non lo è)

| Famiglia | Relazioni | Edge | Destinazione |
|---|---|---|---|
| **Lineage** | `diventa_legge` · `converte_decreto_legge` · `attua_delega` | 3.662 · 933 · 2.250 | **questo tool** |
| **Vita** (ciclo di vita) | `abroga` · `modifica` · `sostituisce` · `split` · `join` · `renumbering` | 67.895 · 23.351 · 407 · 37 | `legal_vita` |
| **Citazione** | `riferimento` · `cita_costituzione` | 203.143 · 6.030 | `legal_neighbors` / `legal_query` |
| **Contenzioso** | `impugna` · `invoca_parametro` · `evoca_parametro` | 11.202 · 54.415 · 865 | `legal_contenzioso` |

**Il vocabolario attuale è tagliato male per questo tool**: `relation_type.in_chain_view`
include `abroga`, `cita_costituzione`, `sostituisce`, `split`, `join`,
`renumbering` e **esclude `modifica`** — cioè mette nel percorso cose che non
sono provenienza e lascia fuori la relazione che più conta per la vita della
norma. Non va riusato così.

v1: le 3 relazioni di lineage sono una **costante unica** nel modulo.
v1.1: colonna `lineage_role` in `mart_legal_relation_type` (valori
`src_monte` · `tgt_monte` · `null`), così il taglio vive nel dato e non nel codice.

---

## 3. Direzioni semantiche (il cuore della spec)

| Relazione | Sorgente | Target | Lato `monte` |
|---|---|---|---|
| `diventa_legge` | DDL | legge | **sorgente** |
| `converte_decreto_legge` | decreto-legge | DDL di conversione | **sorgente** |
| `attua_delega` | decreto attuativo | legge delega | **target** ← invertita |

Esempio di catena reale a due hop, verificata sui mart:

```
urn:nir:stato:decreto.legge:1996-05-10;257      (decreto-legge)
  --converte_decreto_legge-->  senato:771       (DDL di conversione)
  --diventa_legge-->  urn:nir:stato:legge:1996-07-08;368
```

---

## 4. Firma

```python
legal_catena(
    node_id: str,
    direzione: str = "monte",      # monte | valle | entrambe
    profondita: int = 3,           # 1..4 (oltre, il grafo è troppo denso)
    max_passi: int = 30,           # cap sul payload; il summary resta completo
) -> dict
```

`node_id`: URN completo o id parziale. Risoluzione con `_find_node` esistente
(exact → alias → parziale **solo se unico**; ambiguo → candidati, **nessuna
scelta euristica**).

---

## 5. Payload

```json
{
  "nodo": {"id": "urn:nir:stato:legge:1997-03-15;59", "tipo": "LEGGE",
           "titolo": "Delega al Governo per il conferimento di funzioni ...", "anno": 1997},
  "direzione": "monte",
  "catena": [
    {"hop": 1, "relation": "diventa_legge", "verso": "monte",
     "id": "senato:1234", "tipo": "DDL", "titolo": "Delega al Governo ...", "anno": 1996,
     "evidence": {"method": "join", "kind": "quote", "conf": null, "locator": null}},
    {"hop": 2, "relation": "attua_delega", "verso": "monte",
     "id": "urn:nir:stato:legge:1992-...", "tipo": "LEGGE", "anno": 1992,
     "evidence": {"method": "regex", "kind": "quote", "conf": "strong", "locator": "art_1"}}
  ],
  "summary": [
    {"relation": "diventa_legge", "verso": "monte", "n": 2, "mostrati": 2},
    {"relation": "attua_delega", "verso": "valle", "n": 375, "mostrati": 0}
  ],
  "copertura": [
    {"relation": "diventa_legge", "pct": 81.5, "upstream_rows": 7520,
     "natural_pairs": 4496, "edges": 3662},
    {"relation": "attua_delega", "pct": null, "edges": 2250,
     "nota": "1:1: una riga upstream produce un edge, nessun denominatore"}
  ],
  "troncato": {"si": false, "motivo": null},
  "build_at": null,
  "note": []
}
```

- `summary` è **sempre completo**: 375 attuativi si dichiarano anche se il
  payload ne mostra 0 in direzione `monte`.
- `copertura` solo per le relazioni effettivamente attraversate. Solo
  `diventa_legge` ha un funnel upstream (7.520 righe → 4.496 coppie → 3.662
  edge, 81,5%); `attua_delega` e `converte_decreto_legge` sono 1:1 nel mart di
  coverage, quindi `pct: null` e la nota che lo dice.
- `evidence.conf` esiste solo per `attua_delega` (`strong` 1.339 · `medium` 857
  · `review` 54). Per `diventa_legge`/`converte_decreto_legge` è `null`: il
  metodo è `join` su URN, non un'estrazione.
- **Hop senza evidenza**: 1.705 edge `diventa_legge` su 3.662 hanno
  `prov_evidence_kind = none`. Vanno restituiti, ma con `affidabile: false` sul
  passo e una nota: *"questo passaggio non ha una citazione a supporto"*.
- **`attua_delega` è un'ipotesi, non un fatto** (italia-corpus#170): nasce dal
  titolo o da una citazione, il 97,6% degli archi sono già `riferimento`, e il
  ramo `regex` copre 116 delle 198 leggi con "delega al Governo" nel titolo. Se
  compare nel percorso, la risposta aggiunge una nota che lo dichiara.
- `build_at`: **data del build**, non vigenza dell'atto. Rinominato da `as_of`
  perché legal-graph#68 userà `as_of` per la validità temporale dell'atto: due
  significati sullo stesso nome sarebbero una rottura annunciata. Da
  `metadata.json` del publish se leggibile, altrimenti `null` finché non esiste
  `mart_legal_freshness` (P0 aperto).
- `note`: frasi brevi e verificabili, es. `"39,5% dei DDL non ha urn_normattiva:
  la catena può fermarsi prima della legge"`.

---

## 6. Algoritmo

1. Risolvi il nodo (`_find_node`). Ambiguità → stop con candidati.
2. BFS per hop, `profondita` livelli:
   - `monte`: segui le relazioni dove il nodo corrente è sul lato `valle`
     (`attua_delega` entrante, `diventa_legge` entrante,
     `converte_decreto_legge` entrante);
   - `valle`: il contrario.
3. `visited` per id; nessun nodo ripetuto, nessun ciclo.
4. Ordinamento **deterministico**: `hop`, poi priorità relazione
   (`diventa_legge` > `converte_decreto_legge` > `attua_delega`), poi
   `weight DESC`, poi `id`.
5. Cap: `max_passi` totale, max 10 per relazione per hop → `troncato.si`.
6. Target: < 400 ms su mart locale (3 query: nodo, archi per hop, copertura).
   Non misurato: è un'aspettativa da verificare in implementazione.

---

## 7. Casi limite

| Caso | Comportamento |
|---|---|
| id ambiguo | elenco candidati, nessuna scelta |
| nodo senza lineage | `catena: []`, `note: ["nessuna relazione di provenienza"]` |
| 375 attuativi (Bassanini) | `summary` completo, payload capped, `troncato.si` |
| nodo solo-catalogo (67.282) | id esiste, zero archi, nota "identità senza testo" |
| due grafie dello stesso atto (`dl:1996-275` / `urn:...`) | alias di identità (`entity_alias`), un solo nodo |
| ciclo | impossibile su lineage, ma `visited` lo copre |

---

## 8. Test su dati reali (accettazione)

| # | Input | Atteso |
|---|---|---|
| 1 | `urn:nir:stato:legge:1997-03-15;59` (Bassanini), `monte` | catena corta (2 DDL); `summary` con `attua_delega valle = 375` |
| 2 | stesso, `valle` | `summary` 375 attuativi, payload ≤ 30, `troncato.si` |
| 3 | `urn:nir:stato:decreto.legge:1996-05-10;257`, `valle` | 2 hop: `senato:771` → `legge:1996-07-08;368` |
| 4 | `urn:nir:stato:decreto.legislativo:2009-12-01;179` | 1.804 `modifica`: **non devono comparire** (non è lineage) |
| 5 | nodo con zero archi di lineage | `catena: []` + nota |
| 6 | id parziale ambiguo (es. `231`) | candidati, nessuna scelta |

I casi 1-4 sono già verificati sui mart di oggi: i conteggi della tabella
vengono da lì.

---

## 9. Non-obiettivi v1

- **Nessun `riferimento` nel percorso**: 203k edge, il grafo diventa una rete.
- **Nessun ragionamento temporale**: la catena non dice "era vigente nel 2001".
- **Nessuna narrazione**: il tool restituisce fatti ordinati; la prosa è del modello.
- **Nessuna euristica di disambiguazione**: meglio due candidati che un atto sbagliato.

---

## 10. Perché vale

| | Oggi | Con `legal_catena` |
|---|---|---|
| Chiamate per rispondere | 4+ | 1 |
| Determinismo | no (lista piatta + prosa del modello) | sì (SQL + ordinamento fissato) |
| Copertura | assente o su iniziativa dell'LLM | campo obbligatorio |
| Direzione | ambigua sull'edge | dichiarata per relazione |

Criterio di adozione, onesto: se in due settimane di uso reale nessuno chiama
`legal_catena` per una domanda di provenienza, la tesi "le catene sono la
domanda" è falsa — e si risparmiano gli altri tre verticali.

---

## 11. Issue collegate (verificate 2026-11-23)

| Issue | Relazione con questo tool | Stato |
|---|---|---|
| **legal-graph#85** — `conf_evidence` nelle viste curate | `legal_catena` la espone già in `step.evidence.conf`; resta da fare per `_top_edges`/`_bfs` di `legal_node` | **parzialmente chiusa da questo lavoro** (solo il ramo nuovo) |
| **legal-graph#69** — monitor mensile, 5 domande fisse | è la stessa direzione "prodotto ricorrente": atti più modificati, nuove abrogazioni, dormienti, impugnazioni, direttive non recepite | da agganciare: 2 delle 5 domande sono già calcolabili e la 1ª (modifiche) è `legal_vita`, non `legal_catena` |
| **legal-graph#68** — validità temporale (`as_of`) | conflitto di nome risolto: qui il campo è `build_at` | aperta, indipendente |
| **italia-corpus#170** — delega dal preambolo | `attua_delega` è inferita dal titolo; il tool lo dichiara in `note` | aperta: alza la qualità di una relazione già usata |
| **italia-corpus#158** — 4.403 `riferimento` con anni impossibili | non tocca il lineage (nessun `riferimento` qui) | aperta, impatta `legal_neighbors` |
| **toolkit#426 / PR #515** — pulizia mart orfani | lo split lascia `mart_legal_search_terms` nel vecchio `out/data/mart/legal_graph/` | la pulizia arriva con la PR #515: non duplicarla qui |
