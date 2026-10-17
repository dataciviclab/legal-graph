# Cross-Repo Key Schema — Legal Graph

Data: 2026-09-07  
Aggiornato: 2026-10-17 (atto_num bridge, ponte_persona, atti_api, senato_anagrafica, attua_delega peso>=1)  
Stato: ATTUALE — contratto chiavi per il compose e per i bridge cross-repo

## Architettura (oggi)

```
upstream (GCS / GitHub raw)
  → compose toolkit (dataset.yml + sql/mart_legal_*.sql)
  → out/data/mart/legal_graph/2026/
      mart_legal_nodes · edges · node_metrics · search_keys · node_rel · emend_leg
  → MCP legal-graph (search thin / node views / legal_text / query / insights)
```

`attua_delega`: edge tipizzato nel mart (D.Lgs → legge-base), non solo hint runtime.

## Le Chiavi

### id_ddl / atto_num (chiave parlamentare — Senato)

```
58074
```

- **open-politica** (`senato_ddl`): `id_ddl` — 38,372 DDL (Leg13-19)
- **senato-akn** (`senato_corpus`): `atto_num` — 7,659 atti (derivato da `atto_dir`)
- **Stesso numero, nomi diversi.** `atto_dir = "Atto58074"` → `atto_num = 58074` = `id_ddl = 58074`
- **Overlap**: 1,944 atti presenti in entrambi i repo

### fase (chiave iter parlamentare)

```
S.1670
```

- **open-politica**: dal SPARQL `osr:fase` — 9,861 valori unici (Leg14-19)
- **senato-akn** (`senato_emendamenti`): da work_uri regex — 1,614 fasi uniche
- **1:1 con id_ddl dentro una legislatura**, many-to-many cross-legge
- **Join corretto**: `atto_num == id_ddl` (bridge esatto, PR #23)
- ⚠️ `fase` NON è usata nel join emend→atto: emend.fase è formato Senato (S.NNNN), ddl.fase è formato Camera (C.NNNN)

⚠️ **fase numero ≠ id_ddl**: il numero nella fase (es. 1689) è diverso da id_ddl (es. 55177). Sono due sistemi di numerazione diversi.

### URN:NIR (chiave universale — enacted legislation)

```
urn:nir:stato:legge:2012-12-24;234
```

- **italia-corpus**: 100% coverage (22,449 atti)
- **open-politica**: ~100% sui DDL promulgati (4,326 con URN); 845 URN non in corpus (misurato 2026-10-17 su `senato_ddl`)
- **Copre solo**: leggi, decreti diventati legge
- **Fetch API**: leggi ordinarie mancanti via `dettaglio-atto-urn` (atti_api support)

### attua_delega (D.Lgs → legge delega)

```
urn:nir:stato:decreto.legislativo:2023-03-31;43 → urn:nir:stato:legge:2022-06-21;78
```

- **Da 2026-10-17**: soglia `peso >= 1` (prima `>= 5`, che azzerava i D.Lgs post-2015 perché le citazioni nei preamboli valgono 1) + guardia temporale.
- `weight` = peso reale della citazione: **`weight < 5` = edge debole/euristico** (cross-reference possibili), `>= 5` = forte.
- Copertura: **1.375/3.356 D.Lgs (41,0%)**; 2022–2026: 85–100% per anno. Prima del fix: 498 (14,8%).
- Due euristiche: citation (`delega al governo` nel titolo della legge) + title-regex ("…della legge N/YYYY").
- `normativa_types` unisce **`atti_api`** (leggi fetchate via API): senza, le leggi fuori collezione non sono target. 182 D.Lgs agganciati via leggi API (top: L.124/2015 → 23).
- Nota produzione: `atti_api` è external da GitHub raw; il fix è visibile dopo il commit del file in italia-corpus (CI giornaliera).

### senatore_id / persona_id (chiave persona — cross-chamber)

```
senatore:25407 ↔ deputato:302741
```

- **open-politica** `ponte_persona`: match cognome+nome (Leg 13-19, 505 righe)
- **open-politica** `senato_anagrafica`: senatore_id da SPARQL Senato (Leg 13-19)
- **senato-akn** `senato_dibattito`: senatore_id (590 unici)
- **Stesso namespace** senatore_id tra firmatari e dibattito (189 overlap)
- **Edge `collega`**: ponte_persona → `senatore:*` ↔ `deputato:*` (396 edge)

### filename (chiave Normattiva interna)

```
2012-12-24_023U0012_ORIGINALE_V0.md
```

- **italia-corpus**: PK del dataset normativa

## Le Mappe Cross-Repo

```
open-politica.id_ddl  ←→  senato-akn.atto_num        [1:1, esplicito]
open-politica.fase    ←→  senato-akn.emendamenti.fase  [NO — formati diversi]
open-politica.urn_normattiva  ←→  italia-corpus.urn   [1:1, ~100% sui promulgati]
open-politica.ponte_persona   ←→  senatore:* ↔ deputato:*  [collega edge]
```

Catena emend→atto: `emend.atto_num → DDL.id_ddl → DDL.urn_normattiva → normativa.urn`

## I Marts del compose legal-graph (2026)

| Mart | Key | Uso |
|------|-----|-----|
| `mart_legal_nodes` | `id` (URN / namespace) | grafo nodi (+ `eiv` da akn_act_meta) |
| `mart_legal_edges` | `(source_id, relation, target_id)` | grafo relazioni |
| `mart_legal_node_metrics` | `id` | intelligence MCP (insights, ranking) |
| `mart_legal_search_keys` | `id` + `id_num`/`id_year` | ranking `legal_search` |
| `mart_legal_node_rel` | `(id, relation)` | conteggi view overview/parliament |
| `mart_legal_emend_leg` | `(target_id, legislatura)` | emendamenti per DDL |
| `mart_legal_texts` | `id` | testi articoli Cost. + pronunce (locale/MCP) |
| `mart_legal_massime` | `sentenza_id` | massime (locale/MCP) |

> Storico dashboard (senato-akn `mart_per_*`) non fa più parte di questo repo.

## Bug Fixati

| # | Bug | Fix | Stato |
|---|-----|-----|-------|
| 1 | Dibattito clean droppa `atto_dir` | +`atto_num` da `path` | ✅ |
| 2 | Dashboard `atto_tag` usa 7 cifre | `Atto{N:08d}` | ✅ |
| 3 | Riferimenti source edges dangling | `regexp_extract(fonte_filename)` | ✅ |
| 4 | Emendamento edges 47% fantasma | JOIN con `legislatura` | ✅ |
| 5 | Fallback table schemas mismatch | 7-column schema | ✅ |
| 6 | `frbr_number` case mismatch | `FRBRnumber` | ✅ |

## Cosa è stato rimosso

- `senato-composed/` (dataset + marts) — il composed era un 3-way join che non funzionava (fase ≠ atto_num)
- `scripts/build_legislative_edges.py` — logica spostata in legal-graph
- `scripts/build_summaries.py` — le aggregazioni sono nei marts
- `dashboard/sources.py:load_edges()` — la dashboard non usa edges
- `data/derived/senato_edges.parquet` — non più necessario

## Prossimi passi

- [x] Estendere coverage di `urn_normattiva` — ~100% sui promulgati; 845 leggi ordinarie mancanti (IC #68, dato 2026-10-17)
- [x] Materializzare nodi senatore da anagrafica (PR #25, issue #24)
- [x] Bridge senatore↔deputato via ponte_persona (PR #23)
- [x] `attua_delega`: soglia `peso >= 1` + guardia temporale (2026-10-17, 14,8% → 41,0%)
- [ ] Fetch restanti 845 URN leggi ordinarie (IC #68)
- [x] Backfill leggi-delega mancanti (82 URN in `atti_singoli`, 162/174 fetchati) → title-regex
- [ ] Commit/publish `atti_api.parquet` in italia-corpus per rendere il fix visibile in produzione
- [ ] Sync vigenza da API (IC #67)
- [ ] Article-level edges Corte
