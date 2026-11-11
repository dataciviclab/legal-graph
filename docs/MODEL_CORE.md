# MODEL CORE — legal-graph

**Status:** frozen after validation (2026-11-09) · **Verdict:** READY WITH CONDITIONS
**Scope:** core model for M0-M2; M3 temporal columns; M0-M1 can start now, M2-M3 gated on the conditions below.
**Rule:** the system must know what it knows, what it does not know, and why.

---

## 1. Decision

- **Foundation:** DuckDB + Parquet + SQL compose. No graph DB, no event store, no new service.
- **Core:** additive tables/views over the existing `nodes`/`edges`; process entities remain views.
- **`provision` is lazy:** only when it has text/status/version or is a target that needs state. A cited article is a locator (`dst_article`, `dst_comma`), not a node.
- **Versions are records, not nodes.** An event is a relation with `event_date`/`effect_from`, not a node.
- **Provenance is a record/columns, not a node.**
- **No stub entities** for data we do not have (PNRR, GU, procedures).

---

## 2. Core schema (8 tables + 2 views)

| Object | Type | Phase | Purpose |
|---|---|---|---|
| `source_snapshot` | table | M0 (deferred) | dataset/URI/hash/fetched_at; sospesa finché il toolkit non espone `inputs` |
| `entity_alias` | table | M0 | old/external ids → canonical |
| `relation_type` | table | M0 | vocabulary: family, domain/range, granularity, flags |
| `act` | view | M0 | typed act view over `nodes` |
| `relation` | view → table | M0-M1 / M2 | edges + locator/date/assertion; event-level at M2 |
| `document_expression` | table | M2 | filename/snapshot/content_hash/text_status |
| `provision` | table | M2 | act + article + comma + heading |
| `provision_version` | table | M2 | text/status/validity/effect |
| `version_expression` | table | M2 | one version ↔ many expressions (same text) |
| `coverage_level` | view | M1 L1-L2 / M3 L3-L6 | coverage per source/period/type/granularity |

`nodes`/`edges` remain the public compatibility views. `edges` is act-level in M0-M1; from M2 it is an aggregate view over `relation`.

---

## 3. Identity rules

- **Act:** `act_id` = URN for enacted acts; namespaces for others (`senato:{id_ddl}`, `camera:{leg}:{num}`, `celex:{CELEX}`, `sentenza:{year}-{num:04}`). The URL number (e.g. Senato `/ddl/11018`) is an alias, not the id.
- **Provision:** `provision_id = act_id:art:{label}[:com:{label}][:let:{label}]`; `article` is a string (`82-bis` ≠ `83`).
- **Version:** `version_id` uses the stable expression id / snapshot, **not** `valid_from`; a corrected date must not change the id.
- **Relation:** `relation_id` hash of semantic key + locator/source_locator + occurrence; **not** `event_date`/evidence. A corrected date or evidence must not change the id.
- **Aliases:** `camera:atto:*` → `camera:{leg}:{num}`, `dl:*` → URN, `norma:*` → URN, `senato:atto:*` → document. Old ids keep working.
- **No merge** of distinct acts: three RD 208/1893 are three acts; reissued DLs are distinct and linked by `reitera`; Camera/Senate are distinct until a verified relation.
- **Continuity vs structure:** `provision_id` is structural; renumbering/move/reproduction are relations (`renumbering`, `same_as`, `riproduce`), not new “legal disposition” nodes.

---

## 4. Version vs expression

- `document_expression` = a file/snapshot (`filename`, `versione`, `snapshot_data`, `content_hash`, `text_status`).
- `provision_version` = a legally valid text/status/validity.
- Same `content_hash` → **same version**, multiple expressions. Different text → new version.
- Re-acquisition of the same expression → **no new version**.
- `snapshot_data` is `observed_at`, **not** `valid_from`.
- Evidence: L.149/2016 has 4 expressions, 2 identical texts, 1 different, 1 missing; 36/2023 has 3 filenames across mart/clean/testi. The model must not create a version per filename.

---

## 5. Temporal rules

| Field | Meaning | Not |
|---|---|---|
| `published_at` | GU publication | not entry into force |
| `enacted_at` | act date | not publication |
| `in_force_from` | EIV | not effect date |
| `effect_from` | start of legal effect | not necessarily EIV |
| `valid_from/to` | version validity | not `snapshot_data` |
| `observed_at` | snapshot observation | not a legal date |

- `NULL` = unknown; use `validity_source`/`validity_confidence`; never invent dates.
- `effect_from` comes from the text/act producing the effect, not automatically from source EIV.
- As-of query returns `validity_known=false` when a date is missing; `NULL valid_to` means unknown/open, not “certified in force”.
- Retroactivity: `effect_from < published_at` + `retroactive=true`; `EIV < data` is a data anomaly, not retroactivity.

---

## 6. Relation taxonomy

Families: `norm` (legal effects), `juris`, `proc`, `admin`, `identity`.

Canonical relations (minimum): `cita`, `rinvia`, `modifica`, `abroga`, `attua`, `reitera`, `conversione`, `conversione_tentata`, `pubblica`; `impugna`, `invoca_parametro`, `evoca_parametro`, `relatore_sentenza`; process relations `firma`, `relatore`, `emenda`, `vota`, `interviene`, `diventa_legge`, `proposta_cost`, `diventa_revisione`, `testo_atto`, `collega`.

Rules:
- `modification_type` (`insertion|substitution|renumbering|split|join|unknown`) and `scope` (`act|article|comma|unknown`) are mandatory for `modifica`; `scope` is mandatory for `abroga`.
- `sostituisce`, `split`, `join`, `renumbering` are **views/subtypes** of `modifica`, not double-stored relations.
- `attua` is one direction (implementing → delegating); `delega` is the derived inverse.
- `conversione_tentata` (DL→DDL, with `esito`) ≠ `conversione` (DL→law).
- `impugna` stores **all** outcomes (`esito`), not only `illegittimo|misto`; `dichiara_illegittimo` is a view.
- `rinvia` only with explicit text; `dipende` is a derived view, never a stored edge.
- The 25 current relation strings remain aliases in `edges`.

---

## 7. Provenance

For every important node/edge:
`assertion_type` (`extracted|resolved|inferred|verified|unverifiable`), `method` (`akn_ref|akn_lifecycle|regex|title|join|api|manual`), `evidence_kind` (`quote|locator|label|none`), `evidence_locator`, four confidences (`identity`, `extraction`, `semantic`, `temporal`), `review_status`.

- No single confidence float.
- `weight` is ranking, not confidence.
- `verified` requires `reviewed_by`/`reviewed_at`; an inference cannot become a fact without a documented review.
- `inferred`/`weak`/`unverifiable` are visible in responses.

---

## 8. Abrogation and status

- `act.status` and `provision.status` are independent. A partial abrogation does **not** change `act.status`.
- `abroga` needs `scope`, `dst_article/comma` (if known), `effect_from`, evidence.
- A total abrogation can change `act.status` only with `scope='act'`, explicit evidence and date.
- 998 `vigente` + `abroga` are **not auto-corrected**: mark `disputed/unknown`; 97,3% of abrogated acts have no edge, so status is not proof.
- `status_authority`/`status_confidence` distinguish Normattiva, Cellar, catalogo, and “vigente per esclusione”.

---

## 9. Coverage levels L1-L6

| Level | Capability |
|---|---|
| L1 | act identification |
| L2 | links/citations |
| L3 | provisions/locators |
| L4 | verifiable temporal evolution |
| L5 | verified legal relations |
| L6 | implementation/administration links |

`coverage_level(level, source, period, act_type, granularity, n_units, n_with_data, pct)` is a **view**. Publish the highest level with data; do not wait for L6.

---

## 10. Gate conditions (short)

| ID | Condition | Acceptance |
|---|---|---|
| C1 | Identity vs continuity | 8 identity cases; alias/renumbering; no merge |
| C2 | Expression vs version | L.149/2016 → 2 versions/4 expressions; reacquisition no new version |
| C2b | Expression alignment | 0 unmatched clean/testi for pinned snapshot (or explicit list) |
| C3 | Relation semantics | every `modifica` has `modification_type`+`scope`; `conversione`/`impugna` not compressed |
| C4 | Abrogation scope/status | D.P.R. 631/1949 vigente + artt. 96–98 abrogati; D.Lgs 50/2016 only with `scope='act'`; 998 unchanged |
| C5 | Coverage L1-L6 | view with counts/pct per source/period/type/granularity |
| C6 | Provenance | 4 dimensions; `verified` requires review; no `weight` as confidence |
| C7 | Minimal core | 8 tables + 2 views; no `assertion` table; `provision` lazy |
| C8 | Compatibility | `nodes`/`edges` same columns + additive; alias; parity; rollback |
| C9 | Publish levels | L1-L3 now; no L4-L6 claim without data |

**M0-M1:** GO (C1, C3 vocabulary, C6, C7, C8, C9 L1-L2).
**M2-M3:** GO after C2, C2b, C3 data, C4, C5/C6.
**M4:** NO-GO without IPA/CIG/CF sources.

---

## 11. Real evidence used

- 2.422 filename `normativa` assenti in `testi`; 2.812 testi assenti dal clean.
- 36/2023: mart `VIGENZA_2026-09-24`, clean `ORIGINALE`, testi `VIGENZA_2026-09-23`.
- L.149/2016: 4 expressions, 2 identical texts, 1 different, 1 missing.
- D.P.R. 631/1949: artt. 96–98 abrogati, act `vigente`.
- D.Lgs 50/2016: total abrogation; 998 `vigente`+`abroga`; 14.532/14.933 abrogati without edge.
- 302.621 edge `citation` nel clean (109.339 con `articolo`, 157.310 con `contesto`); `insertion`/`repeal`/`substitution` hanno il locator dove la collezione è riprocessata (956 righe restano act-level per il pin `V`).
- 2.590 Camera duplicates; 3 `norma:*` duplicates; 4.322 impossible citations.

---

## 12. Non-goals

- No graph DB, event store, ontology, microservices.
- No node for every cited article/comma; no `event`/`source`/`evidence`/`version` nodes.
- No admin entities without IPA/CIG/CF keys.
- No auto-correction of `stato`; no merge of distinct provisions/acts.
- No version per snapshot; no article-level claim without `scope`/locator.

---

## 13. Next

1. M0: `legal_query` read-only; `source_snapshot`/`entity_alias`/`relation_type`; provenance columns; `coverage_level` L1-L2.
2. M1: Camera dedup, aliases, `act` view; taxonomy mapping; `legal_neighbors`/`legal_coverage`; freeze. ✅ (vedi §15)
3. M2-M3 only after C2b/C3/C4.

---

## 14. M0.2 implementation note (2026-11-09)

**Fatto**
- `legal_query` read-only (M0.1): una istruzione, tipi SELECT/EXPLAIN, no scritture, no funzioni file, no `FROM 'path'`; 271 test verdi.
- View di contratto nel bootstrap MCP: `act`, `relation_type` (25 relazioni), `entity_alias` (camera:atto, dl, norma DL), `coverage_level` L1-L2.
- Colonne provenance additive su `nodes`/`edges`: `assertion_type`, `method`, `prov_evidence_kind` (nuovo), `evidence_locator`, 4 confidenze, `review_status`, `snapshot_id`.
- `evidence_kind` legacy resta invariato (`riferimento`); `prov_evidence_kind` è il campo per tutte le relazioni.
- Mart ricostruito: 10 tabelle, 6.386.899 righe; 271 test, ruff pulito.

**Sospeso (non finto)**
- `source_snapshot`: il toolkit produce `metadata.inputs=[]` per il compose mart-only. Non è stata creata una tabella con URI/hash inventati. Serve una modifica al toolkit (input manifest) o un manifest esplicito.
- `snapshot_id`: NULL su nodi/edge finché non esiste un `source_snapshot` reale.
- `assertion` table: rinviata; le colonne su `nodes`/`edges` coprono M0-M3.
- `provision`/`provision_version`/`document_expression`: M2.

**Limiti noti**
- `entity_alias` copre `camera:atto`, `dl:*`, `norma:decreto legge`; le varianti URN UE e gli alias regionali richiedono il code table (M1/M2).
- `coverage_level` è una view L1-L2; L3-L6 arrivano con `provision`/temporalità/amministrazione.
- `conf_*` sono conservative e per producer, non una verifica legale; `review_status='unreviewed'` su tutto.

---

## 15. M1 implementation note (2026-11-09)

**Fatto**
- **Camera dedup.** Rimossi 2.590 nodi `camera:atto:{leg}_{id}`: erano lo stesso atto di `camera:{leg}:{num}` (numero identico al 100%; 72 date diverse su 2.590). Gli edge `relatore` Camera ora puntano a `camera:{leg}:{num}` (4.028 edge, 0 dangling).
- **Alias come mart.** `mart_legal_entity_alias` (2.593 righe) deriva gli alias Camera da `camera_relatori` — non dai nodi — quindi sopravvivono alla rimozione. `dl:*`/`norma:*` restano derivati da `mart_legal_nodes`. La view `entity_alias` legge il mart; il fallback legacy resta per i mart vecchi.
- **Risoluzione alias.** `legal_node`/`legal_neighbors` risolvono `camera:atto:17_100` → `camera:17:100` con `matched_by="alias"`.
- **Vocabolario come mart.** `mart_legal_relation_type` (25 righe) con `canonical_relation` (15 gruppi), `in_chain`, `in_impact`, `in_chain_view`. `mart_legal_node_metrics` e il traversal di `view=chain` leggono la tabella; niente più liste hardcoded.
- **Parità metriche.** Su 857.879 nodi condivisi col build precedente: 0 differenze in `referenced_by`, `impact_score`, `references`. `sostituisce` resta `in_impact=false` (era già escluso dalla lista storica).
- **Vista `conversione`.** Unione canonica di `converte_decreto_legge` (933, `dl_to_law`) e `diventa_legge` (3.574, `ddl_to_law`).
- **Tool MCP.** `legal_neighbors` (out/in/both, relazione esatta o canonica) e `legal_coverage` (funnel per relazione, livelli L1-L2, famiglie).
- **Mart ricostruito:** 12 tabelle, 6.381.747 righe; 302 test, ruff pulito.

**Canonical 25→15**
`cita` (riferimento, cita_costituzione) · `modifica` (modifica, sostituisce) · `abroga` · `riordino` (split, join, renumbering) · `attua` · `conversione` (converte_decreto_legge, diventa_legge) · `iter_costituzionale` (proposta_cost, diventa_revisione) · `impugna` · `parametro` (invoca/evoca) · `relatore` (relatore, relatore_sentenza) · `firmatario` · `emendamento` · `voto` · `documento` (testo_atto, intervento) · `identita` (collega).

**Compatibilità (M1)**
- Gli id storici `camera:atto:*` risolvono via `entity_alias`, ma la riga risolta è il nodo canonico: `tipo='DDL'`, `source='camera_ddl'`, `anno` valorizzato. Chi filtra `tipo='ATTO CAMERA'` non trova più quei 2.590 atti: il filtro corretto è sull'id canonico o via alias.
- `evidence_locator` è popolato sui **nodi** (da `source_filename`) e, dal 2026-11-11, anche sugli **edge** dove il dato esiste: 91.134 `riferimento`, 12.479 `modifica`, 151 `abroga`, 203 `sostituisce`. La forma resta descritta da `prov_evidence_kind`; il locator non sostituisce `evidence` ma lo qualifica (`art_N[-suffisso][-comM]`, lista se l'edge copre più articoli).

**Limiti noti (non finti)**
- `impugna` copre solo gli esiti `illegittimo|misto`: mancano `infondato`/`inammissibile` e `id_massima`. È lavoro dati/modello di M2 (C3.5), non di vocabolario.
- `conversione` è la conversione **nota**: `converte_decreto_legge` resta `conversione_tentata` (include i decaduti); il filtro per esito è M2.
- `source_snapshot`/`snapshot_id` restano sospesi come in M0.
- Il fallback legacy di `relation_type`/`entity_alias` è congelato a M0/M1: i mart nuovi usano le tabelle.
- `dl:*` → URN produce **0 alias** nel build corrente (i 12 nodi `dl:*` non hanno URN corrispondente); `norma:decreto legge` ne produce 3. L'alias DL si popolerà quando esisterà il nodo URN canonico.
- **C8.5 aperto:** il publish non ha un envelope di versione del modello (`schema_version` di `dataset.yml` è la versione config toolkit). Rollback = build precedente su GCS; da formalizzare in M2.
