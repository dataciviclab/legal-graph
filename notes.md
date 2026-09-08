# Notes — Legal Knowledge Graph

## 2026-09-06 — Sessione completa: fix upstream + espansione grafo

### Modifiche upstream (PR aperte)

| Repo | PR | Modifica |
|---|---|---|
| italia-corpus | #37 | Campo `vigente` da frontmatter in `normativa.parquet` |
| gu-monitor | #3 | Pipeline crossref integrata, script legacy droppati |
| open-politica | #26 | Legislature 13-19 per senato-ddl |

### Stato grafo aggiornato

| Metrica | Prima | Dopo |
|---|---|---|
| Nodi | 26.326 | 729.177 |
| Archi statici | 460.950 | 1.134.516 |
| Archi temporali | 53.057 | 53.057 |
| `vigente` popolato | 0 | 20.716 |
| GU → Normattiva | 26 (rotto) | 26 (funzionante) |
| Senato DDL | 4.763 (1 legislature) | 41.307 (7 legislature) |
| Bridge `diventa_legge` | 733 | 5.057 |
| Fonti | 6 | 8 |
| Relazioni | 9 | 12 |

### Root cause分析

**78% di mortalità iniziale** (26K nodi, 78% senza archi) aveva 5 cause:
1. Leggi pre-costituzionali zombie (31%)
2. Disegni di legge Senato non diventati legge (21%)
3. Atti esecutivi naturalmente isolati (24%)
4. D.Lgs. Luogotenenziali (5%)
5. Grafo incompleto / dati mancanti (19%)

**Dopo i fix**: mortalità ridotta a ~30% (miglioramento grazie a senato-akn e legislature storiche).

### Fix toolkit (pulizia)

- `clean.sql`: rimosso `= 19` hardcoded, aggiunto filtro `HAVING id_ddl IS NOT NULL AND fase IS NOT NULL`
- `dataset.yml`: `years: [13,14,15,16,17,18,19]`, soglie validazione abbassate
- `build_legal_nodes.py`: glob pattern per leggere da `senato_ddl/*/`
- `build_legal_edges.py`: glob pattern per leggere da `senato_ddl/*/`

### Note su `vigente`

- Tutti i 20.716 atti hanno `vigente = True`
- Il corpus upstream (`ahmeabd/italia-corpus`) include solo atti vigenti da Normattiva
- Il campo diventa utile quando il corpus si estende ad atti abrogati

### Note su riferimenti non risolti

- 39.997 riferimenti (36.8%) puntano a leggi non nel corpus
- Causa: 20 collezioni tracciate su ~30 esistenti su Normattiva
- Collezione principale mancante: `DPR` (decreti del Presidente della Repubblica)
