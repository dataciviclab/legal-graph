# Legal Graph — Baseline Verificato

**Data:** 2026-09-22  
**Stato:** Post-M1 (fix applicati, rebuild completato, 28/28 test passano)

---

## Numeri Verificati

### Nodi

| Metrica | Valore | Nota |
|---------|--------|------|
| **Nodi totali unici** | **471.837** | Prima: 472.611 righe (788 duplicati promovimento) |
| Nodi normativa | 20.768 | URN:NIR 100% coverage |
| Nodi Costituzione (articoli) | 139 | Da wikisource |
| Nodi revisioni costituzionali | 50 | Da italia-corpus |
| Nodi GU (con URN) | 13 | Da gu-monitor (26 con URN, 14 deduplicati via normativa) |
| Nodi DDL Senato | 34.303 | 7 legislature (Leg13-19) |
| Nodi DDL Camera | 7.614 | 7 legislature |
| Nodi corpus Senato | 7.676 | AKN XML |
| Nodi emendamenti | 247.961 | Deduplicated |
| Nodi dibattito | 120.926 | Deduplicated |
| Nodi senatori | 590 | Da dibattito |
| Nodi EUR-Lex | 632 | EU legislation |
| Nodi sentenze C.C. | 22.389 | Da Corte Costituzionale |
| Nodi giudici C.C. | 133 | Da Corte Costituzionale |
| Nodi norme (da massime) | 8.423 | Deduplicated |
| Nodi promovimento | 220 | **Fix M1: prima 1.008 (788 duplicati)** |

### Archi Statici

| Relazione | Count | Source → Target |
|-----------|------:|----------------|
| `emendamento` | 428.835 | emendamento → DDL |
| `intervento` | 120.926 | intervento → senatore |
| `riferimento` | 68.767 | atto → atto |
| `invoca_parametro` | 66.239 | sentenza → articolo Cost. |
| `impugna` | 10.890 | sentenza → norma |
| `testo_atto` | 7.676 | corpus → DDL |
| `cita_costituzione` | 6.895 | atto → articolo Cost. |
| `diventa_legge` | 3.451 | DDL → legge |
| `recepisce_direttiva` | 1.236 | atto → direttiva UE |
| `evoca_parametro` | 874 | promovimento → articolo Cost. |
| `attua_delega` | 464 | D.Lgs → legge di delega |
| `attua_regolamento` | 129 | atto → regolamento UE |
| `collega_ue` | 12 | atto → atto UE |
| **Totale statici** | **716.394** | |

### Archi Temporali

| Relazione | Count | Nota |
|-----------|------:|------|
| `modifica` | 51.520 | Da riferimenti incrociati + ordering temporale |
| `entra_in_vigore` | 22.061 | Self-loop con data pubblicazione |
| `pubblicato_in_gu` | 37 | **Fix M1: prima 26** |
| **Totale temporali** | **73.618** | |

### Totale Grafo

| Metrica | Valore |
|---------|--------|
| **Nodi unici** | **471.837** |
| **Archi totali** | **790.012** (716.394 statici + 73.618 temporali) |
| **Tipi di relazione** | **16** (13 statici + 3 temporali) |

### Intelligence Metrics

| Metrica | Valore |
|---------|--------|
| Nodi metricati | 471.837 |
| Critical (≥100 refs) | 190 |
| Important (≥50 refs) | 163 |
| Very complex (≥200 deps) | 16 |
| Obsolete candidates (≥50y + ≥10 refs) | 163 |
| Dormant (last cited < 2010) | 5.621 |
| Eta media | 13.0 anni |

---

## Fix Applicati in M1

| # | Fix | Effetto | File |
|---|-----|---------|------|
| 1 | promovimento dedup | -788 nodi duplicati | `build_legal_nodes.py:553-574` |
| 2 | GU COALESCE bug | +11 URN recuperate | `gu-monitor/scripts/to_parquet.py:167` |
| 3 | GU scope (tipo_atto filter) | Crossref su tutti atti normativi SG | `gu-monitor/scripts/crossref.py:80-86` |
| 4 | Frontmatter encoding | +12 atti recuperati (22.049→22.061) | `italia-corpus/lab_tools/_frontmatter.py:37-43` |
| 5 | Anno dinamico | Metriche non più hardcoded al 2026 | `graph_intelligence.py:95,112,113` |
| 6 | attua_delega documentato | Sovvrapposizione con riferimento documentata | `build_legal_edges.py:321-328` |
| 7 | vigente column removed | Rimosso riferimento a colonna inesistente | `graph_intelligence.py:82` |
| 8 | senatore prefix | Aggiunto ai namespace validi | `tests/test_graph_integrity.py:66` |

---

## Test

28/28 test passano:

- 5 test frontmatter (encoding fix)
- 5 test nodi (integrità, dedup, namespace)
- 6 test archi (integrità, relazioni, pesi)
- 3 test temporali (modifica, vigore, self-loop)
- 4 test metrics (coverage, livelli, età)
- 5 test cardinalità (range attesi)

---

## Known Issues (pre-esistenti, non bloccanti)

| Issue | Impatto | Stato |
|-------|---------|-------|
| 5.222 archi `testo_atto` con target `senato:X` non in nodes | Dangling edges | Documentato nei test (≤6.000) |
| 1 nodo `norma:decreto-legge:78:2105` con anno futuro | Age negativo | Dati massime corrotti |
| `attua_delega` sovrappone `riferimento` (464 archi) | Doppio conteggio potenziale | Escluso da ANALYSIS_RELATIONS |

---

## Stack

```
italia-corpus     → normativa.parquet (22.061 atti)
costituzione      → 7 clean parquet datasets
gu-monitor        → gu_acts.parquet (4.682 atti, 37 con URN)
senato-akn        → senato_corpus, senato_emendamenti, senato_dibattito
open-politica     → 25 dataset (Camera/Senato/elezioni)
EUR-Lex           → legal_edges_eu, legal_nodes_eu

legal-graph       → legal_nodes.parquet (471.837)
                → legal_edges.parquet (716.394)
                → legal_edges_temporal.parquet (73.618)
                → graph_metrics.parquet (471.837)
```

---

*Baseline generato da rebuild completo post-M1. Tutti i numeri sono verificabili eseguendo `pytest tests/ -v` e `python -m legal_graph.graph_intelligence`.*
