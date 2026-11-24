"""`legal_catena` — percorso di provenienza con copertura dichiarata.

Una sola domanda: *da dove viene questa norma, e cosa ha prodotto*.

Perché esiste (docs/SPEC-legal-catena.md): `legal_node(view="chain")` fa un BFS
e restituisce una lista piatta di nodi, non un percorso; e le relazioni non
dichiarano da che parte sta l'origine, quindi `attua_delega` (che punta
dall'attuativo alla delega) viene letta al contrario da chi legge l'edge.

Confine: qui c'è **solo il lineage**. Ciclo di vita (`abroga`, `modifica`,
`sostituisce`, `split`, `join`, `renumbering`) e citazioni (`riferimento`,
`cita_costituzione`) sono altre famiglie, altri tool.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import duckdb

from legal_graph.freshness import SOGLIA_GIORNI
from legal_graph.mcp import db
from legal_graph.mcp.db import _has_col, _view_exists
from legal_graph.mcp.node_views import _find_node
from legal_graph.paths import resolve_freshness_file, resolve_metadata_file

# Relazione → quale lato dell'edge è il "monte" (l'origine della catena).
# `attua_delega` è l'unica invertita: source = decreto attuativo, target = legge
# delega. Il resto punta in avanti (source = origine).
LINEAGE: dict[str, str] = {
    "diventa_legge": "src_monte",
    "converte_decreto_legge": "src_monte",
    "attua_delega": "tgt_monte",
}

# Ordine di lettura di una catena: prima il passaggio parlamentare, poi la
# conversione, poi la delega. Usato per l'ordinamento deterministico.
_PRIORITY = {"diventa_legge": 0, "converte_decreto_legge": 1, "attua_delega": 2}

_DIRS = ("monte", "valle", "entrambe")

# Support che alimentano ogni relazione di lineage: serve a dire quanto è
# vecchio il dato che sostiene un passo. Solo lineage — le altre relazioni
# hanno altri tool e altri support.
#
# La mappa segue l'SQL, non l'intuizione: `converte_decreto_legge` nasce da
# `atti` + `dl` (`conversione_dl`), non da `normativa`; `attua_delega` legge
# `normativa_types`, che è `normativa` ∪ `atti_api`. Una mappa sbagliata qui
# dichiara fresco un passo vecchio — è il caso che la feature esiste per evitare.
_REL_SUPPORT: dict[str, tuple[str, ...]] = {
    "diventa_legge": ("senato_ddl", "camera_leggi", "normativa"),
    "converte_decreto_legge": ("atti", "dl", "gu_acts"),
    "attua_delega": ("riferimenti_urn", "normativa", "atti_api"),
}
_MAX_PER_REL_HOP = 10
_MAX_FRONTIER = 60
_TITLE = 200


def _titolo(value: Any) -> str:
    """Titolo su una riga: le fonti hanno `\\r` e spazi doppi."""
    return " ".join(str(value or "").split())[:_TITLE]


def _move(
    relation: str, source_id: str, target_id: str, current: str, direzione: str
) -> str | None:
    """Prossimo nodo dell'arco, o None se il verso non è percorribile.

    `direzione="monte"` significa "verso l'origine": si percorre l'arco solo se
    il nodo corrente è dal lato `valle`. Da un nodo già sul lato `monte` non si
    sale oltre *in quella relazione* (può comunque salire con un'altra).
    """
    src_monte = LINEAGE[relation] == "src_monte"
    if current == source_id:
        other, cur_is_monte = target_id, src_monte
    elif current == target_id:
        other, cur_is_monte = source_id, not src_monte
    else:  # pragma: no cover — l'arco è stato letto perché incidente
        return None
    if direzione == "entrambe":
        return other
    if direzione == "monte" and cur_is_monte:
        return None
    if direzione == "valle" and not cur_is_monte:
        return None
    return other


def _edges_for(
    con: duckdb.DuckDBPyConnection, ids: list[str]
) -> list[tuple[Any, ...]]:
    """Archi di lineage incidenti agli id, in una query sola.

    `conf_evidence` esiste dal build 2026-11-19: su un mart precedente la
    colonna manca e va sostituita con NULL, altrimenti la query fallisce con un
    binder error (#85, regola `_has_col`).
    """
    rels = ", ".join(f"'{r}'" for r in LINEAGE)
    marks = ", ".join("?" for _ in ids)
    conf = (
        "conf_evidence"
        if _has_col(con, "edges", "conf_evidence")
        else "NULL::VARCHAR"
    )
    return con.execute(
        f"""
        SELECT relation, source_id, target_id, weight, method,
               prov_evidence_kind, {conf} AS conf_evidence, evidence_locator
        FROM edges
        WHERE relation IN ({rels})
          AND (source_id IN ({marks}) OR target_id IN ({marks}))
        """,
        [*ids, *ids],
    ).fetchall()


def _node_meta(con: duckdb.DuckDBPyConnection, ids: list[str]) -> dict[str, tuple]:
    if not ids:
        return {}
    marks = ", ".join("?" for _ in ids)
    rows = con.execute(
        f"SELECT id, tipo, title, anno FROM nodes WHERE id IN ({marks})", ids
    ).fetchall()
    return {r[0]: r for r in rows}


def _summary(
    con: duckdb.DuckDBPyConnection, start: str, shown: dict[tuple[str, str], int]
) -> list[dict[str, Any]]:
    """Conteggio completo delle relazioni di lineage dal nodo, nei due versi.

    Resta completo anche quando il payload è troncato: 375 decreti attuativi si
    dichiarano anche se se ne mostrano 30.
    """
    counts: dict[tuple[str, str], int] = {}
    for relation, source_id, target_id in con.execute(
        f"""
        SELECT relation, source_id, target_id FROM edges
        WHERE relation IN ({", ".join(f"'{r}'" for r in LINEAGE)})
          AND (source_id = ? OR target_id = ?)
        """,
        [start, start],
    ).fetchall():
        src_monte = LINEAGE[relation] == "src_monte"
        # Il verso è quello dell'arco *visto dal nodo*, non dal suo source:
        # se il nodo è la sorgente, l'altro capo sta a valle quando la
        # sorgente è il monte (e viceversa).
        if source_id == start:
            verso = "valle" if src_monte else "monte"
        else:
            verso = "monte" if src_monte else "valle"
        counts[(relation, verso)] = counts.get((relation, verso), 0) + 1
    out = []
    for (relation, verso), n in sorted(
        counts.items(), key=lambda kv: (-kv[1], _PRIORITY.get(kv[0][0], 9), kv[0][1])
    ):
        out.append(
            {
                "relation": relation,
                "verso": verso,
                "n": n,
                "mostrati": shown.get((relation, verso), 0),
            }
        )
    return out


def _coverage(
    con: duckdb.DuckDBPyConnection, relations: list[str]
) -> list[dict[str, Any]]:
    """Funnel per le relazioni effettivamente attraversate."""
    if not relations or not _view_exists(con, "coverage"):
        return []
    marks = ", ".join("?" for _ in relations)
    rows = con.execute(
        f"""
        SELECT relation, upstream_rows, natural_pairs, edges, edge_pct, note
        FROM coverage WHERE relation IN ({marks}) ORDER BY relation
        """,
        relations,
    ).fetchall()
    out = []
    for relation, upstream, pairs, edges, pct, note in rows:
        item: dict[str, Any] = {
            "relation": relation,
            "pct": float(pct) if pct is not None else None,
            "edges": int(edges) if edges is not None else None,
        }
        if upstream is not None:
            item["upstream_rows"] = int(upstream)
        if pairs is not None:
            item["natural_pairs"] = int(pairs)
        if pct is None and note:
            item["nota"] = str(note)
        out.append(item)
    return out


def _giorni_da(as_of: Any) -> int | None:
    """Giorni trascorsi da `as_of` **adesso** (naive → UTC)."""
    if as_of is None:
        return None
    if isinstance(as_of, str):
        try:
            as_of = datetime.fromisoformat(as_of)
        except ValueError:
            return None
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    return (datetime.now(UTC) - as_of).days


def _dati_freschezza(
    con: duckdb.DuckDBPyConnection, relations: list[str]
) -> dict[str, Any]:
    """Età del support più vecchio che sostiene i passi, se la tabella c'è.

    Legge `mart_legal_freshness` (prodotta da `legal_graph.freshness`) e
    ricalcola l'età da `as_of` **al momento della domanda**: `age_days` nella
    tabella è la fotografia del build, e riusarla farebbe dire 57 giorni per
    sempre a un support che nel frattempo ne ha 67.
    """
    path = resolve_freshness_file()
    if path is None:
        return {}
    try:
        rows = con.execute(
            "SELECT support, as_of FROM read_parquet(?)", [str(path)]
        ).fetchall()
    except duckdb.Error:
        return {}
    per_support = {r[0]: r[1] for r in rows}
    wanted = {s for rel in relations for s in _REL_SUPPORT.get(rel, ())}
    dates = [per_support[s] for s in wanted if per_support.get(s) is not None]
    ages = [_giorni_da(d) for d in dates]
    ages = [a for a in ages if a is not None]
    out: dict[str, Any] = {}
    if ages:
        out["dati_age_days"] = int(max(ages))
    if dates:
        out["dati_as_of"] = str(min(dates))[:10]
    return out


def _build_at(con: duckdb.DuckDBPyConnection) -> str | None:
    """Data del build (non la vigenza dell'atto: quella è legal-graph#68)."""
    path = resolve_metadata_file()
    if path is None:
        return None
    try:
        row = con.execute(
            "SELECT timestamp_utc FROM read_json_auto(?)", [str(path)]
        ).fetchone()
    except duckdb.Error:
        return None
    return str(row[0]) if row else None


def _impl_catena(
    node_id: str,
    direzione: str = "monte",
    profondita: int = 3,
    max_passi: int = 30,
) -> dict[str, Any]:
    con = db._get_con()
    direzione = (direzione or "monte").strip().lower()
    if direzione == "entrambi":  # tolleranza sull'input
        direzione = "entrambe"
    if direzione not in _DIRS:
        return {"error": f"direzione non valida: {direzione!r}. Ammesse: {', '.join(_DIRS)}"}
    profondita = max(1, min(int(profondita or 3), 4))
    max_passi = max(1, min(int(max_passi or 30), 100))

    found = _find_node(con, node_id)
    if found.row is None:
        if found.candidates:
            return {
                "error": (
                    f"id ambiguo: {node_id!r} corrisponde a più nodi. "
                    "Passa l'id completo."
                ),
                "ambiguous": True,
                "matched_by": "ambiguous",
                "node_id": node_id,
                "candidates": found.candidates,
            }
        return {"error": f"Nodo non trovato: {node_id!r}"}

    start = found.row[0]
    steps: list[dict[str, Any]] = []
    shown: dict[tuple[str, str], int] = {}
    seen = {start}
    frontier = [start]
    truncated = False
    no_evidence = 0
    exhausted = False

    for hop in range(1, profondita + 1):
        rows = _edges_for(con, frontier[:200])
        candidates: list[tuple[Any, ...]] = []  # (hop, cur, next, edge_row)
        for row in rows:
            source_id, target_id = row[1], row[2]
            for cur in (source_id, target_id):
                if cur not in frontier:
                    continue
                nxt = _move(row[0], source_id, target_id, cur, direzione)
                if nxt and nxt not in seen:
                    candidates.append((hop, cur, nxt, row))
                    break
        # Ordine deterministico: hop, priorità della relazione, peso, id.
        candidates.sort(key=lambda c: (c[0], _PRIORITY.get(c[3][0], 9), -(c[3][3] or 0), c[2]))

        per_rel: dict[str, int] = {}
        next_frontier: list[str] = []
        for hop_n, cur, nxt, row in candidates:
            if len(steps) >= max_passi:
                truncated = True
                exhausted = True
                break
            relation = row[0]
            if per_rel.get(relation, 0) >= _MAX_PER_REL_HOP:
                truncated = True
                continue
            per_rel[relation] = per_rel.get(relation, 0) + 1
            src_monte = LINEAGE[relation] == "src_monte"
            # Da un nodo già sul lato monte si scende (verso=valle), e viceversa.
            cur_is_monte = (cur == row[1]) == src_monte
            verso = "valle" if cur_is_monte else "monte"
            seen.add(nxt)
            next_frontier.append(nxt)
            shown[(relation, verso)] = shown.get((relation, verso), 0) + 1
            if row[5] == "none":
                no_evidence += 1
            steps.append(
                {
                    "hop": hop_n,
                    "relation": relation,
                    "verso": verso,
                    "id": nxt,
                    "evidence": {
                        "method": row[4],
                        "kind": row[5],
                        "conf": row[6],
                        "locator": row[7],
                    },
                    "affidabile": row[5] != "none",
                }
            )
        if exhausted:
            break
        if len(next_frontier) > _MAX_FRONTIER:
            truncated = True
        frontier = next_frontier[:_MAX_FRONTIER]
        if not frontier:
            break

    meta = _node_meta(con, [s["id"] for s in steps])
    for step in steps:
        row = meta.get(step["id"])
        if row:
            step["tipo"] = row[1]
            step["titolo"] = _titolo(row[2])
            step["anno"] = row[3]

    relations = sorted({s["relation"] for s in steps})
    dati = _dati_freschezza(con, relations)
    notes: list[str] = []
    if no_evidence:
        parola = "passaggio" if no_evidence == 1 else "passaggi"
        notes.append(
            f"{no_evidence} {parola} senza citazione a supporto (affidabile: false)"
        )
    if dati.get("dati_age_days", 0) > SOGLIA_GIORNI:
        notes.append(
            f"la catena poggia su dati di {dati['dati_age_days']} giorni fa "
            f"(soglia {SOGLIA_GIORNI})"
        )
    if "attua_delega" in relations:
        notes.append(
            "attua_delega è inferita da titolo/citazione: è un'ipotesi, non una "
            "dichiarazione esplicita dell'atto (italia-corpus#170)"
        )
    if not steps:
        notes.append("nessuna relazione di provenienza per questo nodo")

    return {
        "nodo": {
            "id": start,
            "tipo": found.row[1],
            "titolo": _titolo(found.row[2]),
            "anno": found.row[4],
        },
        "direzione": direzione,
        "catena": steps,
        "summary": _summary(con, start, shown),
        "copertura": _coverage(con, relations),
        "troncato": {
            "si": truncated,
            "motivo": "max_passi" if truncated else None,
        },
        **dati,
        "build_at": _build_at(con),
        "note": notes,
    }
