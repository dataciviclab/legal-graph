# Cross-Repo Key Schema — Legal Graph

Data: 2026-09-07  
Aggiornato: 2026-09-30 (compose toolkit)  
Stato: ATTUALE — contratto chiavi per il compose e per i bridge cross-repo

## Architettura (oggi)

```
upstream (GCS / GitHub raw)
  → compose toolkit (dataset.yml + sql/mart_legal_*.sql)
  → out/data/mart/legal_graph/2026/
  → MCP legal-graph (search / chain / jurisprudence / legal_text)
```

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
- **Join corretto**: `fase + legislatura` → 1:1 con `id_ddl`
- **Coverage**: 64% delle fasi emendamento matchano nel DDL

⚠️ **fase numero ≠ id_ddl**: il numero nella fase (es. 1689) è diverso da id_ddl (es. 55177). Sono due sistemi di numerazione diversi.

### URN:NIR (chiave universale — enacted legislation)

```
urn:nir:stato:legge:2012-12-24;234
```

- **italia-corpus**: 100% coverage (22,449 atti)
- **open-politica**: 14% coverage (733/5,165 DDL hanno URN)
- **Copre solo**: leggi, decreti diventati legge

### filename (chiave Normattiva interna)

```
2012-12-24_023U0012_ORIGINALE_V0.md
```

- **italia-corpus**: PK del dataset normativa

## Le Mappe Cross-Repo

```
open-politica.id_ddl  ←→  senato-akn.atto_num        [1:1, esplicito]
open-politica.fase    ←→  senato-akn.emendamenti.fase  [1:1 dentro legislatura]
open-politica.urn_normattiva  ←→  italia-corpus.urn   [1:1, 14% coverage]
```

Catena completa: `emend.fase → DDL.fase → DDL.id_ddl → corpus.atto_num → DDL.urn_normattiva → normativa.urn`

## I Marts (ciò che la dashboard consuma)

| Dataset | Mart | Key | Righe | Uso |
|---------|------|-----|-------|-----|
| corpus | `mart_per_atto` | `atto_num` | ~2,000 | KPI per atto |
| corpus | `mart_per_legislatura` | `legislatura` | 4 | Panoramica |
| emendamenti | `mart_emendamenti_per_fase` | `fase` | ~1,600 | Intensità emendativa |
| emendamenti | `mart_per_legislatura` | `legislatura` | 6 | Panoramica |
| dibattito | `mart_dib_per_atto` | `atto_num` | ~1,800 | Dibattito per atto |
| dibattito | `mart_interventi_per_persona` | `senatore_id` | ~5,000 | Top oratori |
| dibattito | `mart_dibattito_per_seduta` | `data_seduta` | ~1,200 | Timeline |
| dibattito | `mart_per_legislatura` | `legislatura` | 3 | Panoramica |

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

- [ ] Estendere coverage di `urn_normattiva` in open-politica (da 14% a 100% sui DDL diventati legge)
- [ ] Materializzare nodi mancanti nel graph (senatore, norma)
- [ ] Aggiornare status.md in senato-akn
