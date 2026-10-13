"""Lookup nodi, payload, intelligence e 4 viste di legal_node."""
from __future__ import annotations

import re
from typing import Any

import duckdb

from legal_graph.mcp.db import _get_con, _refresh_views, _view_exists
from legal_graph.mcp.textfold import _fold

_NODE_VIEWS = ("overview", "chain", "jurisprudence", "parliament")
_CHAIN_RELS = (
    "diventa_legge",
    "attua_delega",
    "recepisce_direttiva",
    "attua_regolamento",
    "collega_ue",
    "cita_costituzione",
    "converte_decreto_legge",
    # modifiche tipizzate AKN (2026-10): parte della catena normativa
    "abroga",
    "sostituisce",
    "split",
    "join",
    "renumbering",
)
_TITLE_NODE = 500

def _find_node(con: duckdb.DuckDBPyConnection, node_id: str) -> tuple | None:
    """Preferisci match esatto; LIKE solo se l'id esatto non esiste."""
    cols = (
        "id, tipo, title, CAST(data AS VARCHAR) AS data, anno, source, "
        "length_chars, length_words, celex, collezione, source_filename, "
        "stato, materia, qualita_score, sunsetting_score"
    )
    # eiv: colonna nuova (akn_act_meta 2026-10) — assente sui mart legacy
    has_eiv = con.execute(
        "SELECT COUNT(*) FROM (DESCRIBE nodes) WHERE column_name = 'eiv'"
    ).fetchone()[0] > 0
    if has_eiv:
        cols += ", eiv"
    exact = con.execute(
        f"""
        SELECT {cols}
        FROM nodes WHERE id = ? LIMIT 1
        """,
        [node_id],
    ).fetchone()
    if exact:
        return exact
    # varianti sentenza: YYYY-NNN senza zero-pad
    m = re.fullmatch(r"sentenza:(\d{4})-(\d{1,4})", node_id.strip(), re.IGNORECASE)
    if m:
        padded = f"sentenza:{m.group(1)}-{int(m.group(2)):04d}"
        hit = con.execute(
            f"""
            SELECT {cols}
            FROM nodes WHERE id = ? LIMIT 1
            """,
            [padded],
        ).fetchone()
        if hit:
            return hit
    # ECLI parziale nel titolo
    if re.search(r"ecli|:\d{4}:\d+", node_id, re.IGNORECASE):
        hit = con.execute(
            f"""
            SELECT {cols}
            FROM nodes
            WHERE source='costituzione' AND LOWER(title) LIKE ?
            ORDER BY id LIMIT 1
            """,
            [f"%{_fold(node_id)}%"],
        ).fetchone()
        if hit:
            return hit
    return con.execute(
        f"""
        SELECT {cols}
        FROM nodes
        WHERE id LIKE ?
        ORDER BY length(id)
        LIMIT 1
        """,
        [f"%{node_id}%"],
    ).fetchone()


def _node_payload(row: tuple) -> dict[str, Any]:
    payload = {
        "id": row[0],
        "tipo": row[1],
        "title": (row[2] or "")[:_TITLE_NODE],
        "data": row[3],
        "anno": row[4],
        "source": row[5],
        "length_chars": row[6],
        "length_words": row[7],
        "celex": row[8],
        "collezione": row[9],
        "source_filename": row[10],
    }
    # Qualità IC (opzionali — mart legacy senza colonne)
    if len(row) > 11:
        payload["stato"] = row[11]
        payload["materia"] = row[12]
        payload["qualita_score"] = row[13]
        payload["sunsetting_score"] = row[14]
    # EIV AKN (opzionale — mart legacy senza colonna)
    if len(row) > 15:
        payload["eiv"] = row[15]
    return payload


def _relation_counts(con: duckdb.DuckDBPyConnection, actual_id: str) -> dict[str, Any]:
    if _view_exists(con, "node_rel"):
        out = dict(
            con.execute(
                "SELECT relation, n_out FROM node_rel WHERE id = ? ORDER BY n_out DESC LIMIT 12",
                [actual_id],
            ).fetchall()
        )
        inn = dict(
            con.execute(
                "SELECT relation, n_in FROM node_rel WHERE id = ? ORDER BY n_in DESC LIMIT 12",
                [actual_id],
            ).fetchall()
        )
        out = {k: int(v or 0) for k, v in out.items()}
        inn = {k: int(v or 0) for k, v in inn.items()}
        return {"out": out, "in": inn, "source": "mart_legal_node_rel"}
    out = dict(
        con.execute(
            """
            SELECT relation, COUNT(*) FROM edges WHERE source_id = ?
            GROUP BY 1 ORDER BY 2 DESC LIMIT 12
            """,
            [actual_id],
        ).fetchall()
    )
    inn = dict(
        con.execute(
            """
            SELECT relation, COUNT(*) FROM edges WHERE target_id = ?
            GROUP BY 1 ORDER BY 2 DESC LIMIT 12
            """,
            [actual_id],
        ).fetchall()
    )
    return {"out": out, "in": inn, "source": "edges"}


def _top_edges(
    con: duckdb.DuckDBPyConnection,
    *,
    column: str,
    actual_id: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Top edge in uscita (source_id) o in entrata (target_id)."""
    join_col = "target_id" if column == "source_id" else "source_id"
    try:
        rows = con.execute(
        f"""
        SELECT e.relation, e.{join_col}, e.weight, e.source_year, e.target_year,
               n.tipo, LEFT(n.title, 90)
        FROM edges e
        LEFT JOIN nodes n ON n.id = e.{join_col}
        WHERE e.{column} = ?
        ORDER BY e.weight DESC
        LIMIT ?
        """,
        [actual_id, limit],
    ).fetchall()
    except duckdb.Error:
        return []
    key = "target" if column == "source_id" else "source"
    return [
        {
            "relation": r[0],
            key: r[1],
            "weight": r[2],
            "source_year": r[3],
            "target_year": r[4],
            "tipo": r[5],
            "title": r[6],
        }
        for r in rows
    ]


def _node_intelligence(actual_id: str) -> dict[str, Any] | None:
    con = _get_con()
    if not _view_exists(con, "metrics"):
        return None
    try:
        row = con.execute(
        """
        SELECT referenced_by, impact_score, impact_level, "references",
               complexity_level, age_years, age_risk, activity_level
        FROM metrics
        WHERE id = ?
        """,
        [actual_id],
    ).fetchone()
    except duckdb.Error:
        return None
    if not row:
        return None
    return {
        "referenced_by": row[0],
        "impact_score": row[1],
        "impact_level": row[2],
        "references": row[3],
        "complexity_level": row[4],
        "age_years": row[5],
        "age_risk": row[6],
        "activity_level": row[7],
    }


def _parse_delega_hint(title: str) -> dict[str, str] | None:
    """Euristica da titolo: 'articolo X della legge DD mese YYYY, n. Y' → delega."""
    if not title:
        return None
    m = re.search(
        r"articolo\s+(\d+)[^.,;]{0,80}della\s+legge\s+"
        r"(\d{1,2})\s+([a-zà-ù]+)\s+(\d{4})\s*,?\s*n\.\s*(\d+)",
        _fold(title),
    )
    if not m:
        return None
    months = {
        "gennaio": "01", "febbraio": "02", "marzo": "03", "aprile": "04",
        "maggio": "05", "giugno": "06", "luglio": "07", "agosto": "08",
        "settembre": "09", "ottobre": "10", "novembre": "11", "dicembre": "12",
        "gen": "01", "feb": "02", "mar": "03", "apr": "04", "mag": "05",
        "giu": "06", "lug": "07", "ago": "08", "set": "09", "ott": "10",
        "nov": "11", "dic": "12",
    }
    mon = months.get(m.group(3), "")
    if not mon:
        return None
    return {
        "articolo": m.group(1),
        "legge_id": f"urn:nir:stato:legge:{m.group(4)}-{mon}-{int(m.group(2)):02d};{m.group(5)}",
        "legge_n": m.group(5),
        "legge_anno": m.group(4),
    }


def _attua_delega_hint(con: duckdb.DuckDBPyConnection, node_row: tuple) -> dict[str, Any] | None:
    """Se il titolo cita una delega (legge-base), mostra l'attuazione anche senza edge tipizzato."""
    actual_id = node_row[0]
    hint = _parse_delega_hint(node_row[2] or "")
    if not hint:
        return None
    # già presente come edge tipizzato?
    exists = con.execute(
        """
        SELECT 1 FROM edges
        WHERE source_id = ? AND relation = 'attua_delega' AND target_id = ?
        LIMIT 1
        """,
        [actual_id, hint["legge_id"]],
    ).fetchone()
    legge = con.execute(
        "SELECT tipo, title, anno FROM nodes WHERE id = ? LIMIT 1",
        [hint["legge_id"]],
    ).fetchone()
    # fallback LIKE per URN non normalizzate
    if legge is None:
        legge = con.execute(
            """
            SELECT tipo, title, anno FROM nodes
            WHERE source='normativa' AND id LIKE ?
            ORDER BY length(id) LIMIT 1
            """,
            [f"%legge:{hint['legge_anno']}%;{hint['legge_n']}"],
        ).fetchone()
    if legge is None:
        return {
            "heuristic": True,
            "articolo": hint["articolo"],
            "delega_urn_guess": hint["legge_id"],
            "delega_in_grafo": False,
            "note": (
                "Titolo cita legge-base n. "
                f"{hint['legge_n']}/{hint['legge_anno']} art. {hint['articolo']}, "
                "ma il nodo legge non è nel mart normativa."
            ),
        }
    return {
        "heuristic": True,
        "edge_presente": bool(exists),
        "articolo": hint["articolo"],
        "delega_tipo": legge[0],
        "delega_title": (legge[1] or "")[:200],
        "delega_anno": legge[2],
        "delega_urn_guess": hint["legge_id"],
        "note": (
            "attua_delega euristica dal titolo (non un edge tipizzato). "
            "Verifica con legal_text / legal_query su edges."
        ),
    }


def _view_overview(con: duckdb.DuckDBPyConnection, node_row: tuple) -> dict[str, Any]:
    actual_id = node_row[0]
    try:
        counts = _relation_counts(con, actual_id)
    except Exception:  # noqa: BLE001
        counts = {"out": {}, "in": {}, "source": "error"}
    try:
        top_out = _top_edges(con, column="source_id", actual_id=actual_id)
    except Exception:  # noqa: BLE001
        top_out = []
    try:
        top_in = _top_edges(con, column="target_id", actual_id=actual_id)
    except Exception:  # noqa: BLE001
        top_in = []
    try:
        hint = _attua_delega_hint(con, node_row)
    except Exception:  # noqa: BLE001
        hint = None
    try:
        intel = _node_intelligence(actual_id)
    except Exception:  # noqa: BLE001
        intel = None
    return {
        "view": "overview",
        "node": _node_payload(node_row),
        "relation_counts": counts,
        "top_outgoing": top_out,
        "top_incoming": top_in,
        "attua_delega_hint": hint,
        "intelligence": intel,
        "next": [
            "legal_text(node_id) per il testo (se source=normativa)",
            "legal_node(node_id, view='chain'|'jurisprudence'|'parliament')",
        ],
    }


def _view_chain(con: duckdb.DuckDBPyConnection, node_row: tuple, depth: int) -> dict[str, Any]:
    actual_id = node_row[0]
    depth = max(1, min(int(depth or 3), 5))
    forward: list[dict[str, Any]] = []
    visited = {actual_id}
    queue: list[tuple[str, int]] = [(actual_id, 0)]
    in_clause = ",".join("?" * len(_CHAIN_RELS))
    while queue:
        current, d = queue.pop(0)
        if d >= depth:
            continue
        edges = con.execute(
            f"""
            SELECT relation, target_id, weight FROM edges
            WHERE source_id = ? AND relation IN ({in_clause})
            ORDER BY weight DESC LIMIT 8
            """,
            [current, *_CHAIN_RELS],
        ).fetchall()
        for rel, target, weight in edges:
            if target in visited:
                continue
            visited.add(target)
            tnode = con.execute(
                "SELECT tipo, title, anno FROM nodes WHERE id = ? LIMIT 1", [target]
            ).fetchone()
            forward.append(
                {
                    "relation": rel,
                    "target_id": target,
                    "target_tipo": tnode[0] if tnode else "?",
                    "target_title": (tnode[1] or "")[:100] if tnode else "",
                    "target_anno": tnode[2] if tnode else None,
                    "weight": weight,
                }
            )
            queue.append((target, d + 1))

    backward: list[dict[str, Any]] = []
    visited_b = {actual_id}
    queue_b: list[tuple[str, int]] = [(actual_id, 0)]
    while queue_b:
        current, d = queue_b.pop(0)
        if d >= depth:
            continue
        edges = con.execute(
            f"""
            SELECT relation, source_id, weight FROM edges
            WHERE target_id = ? AND relation IN ({in_clause})
            ORDER BY weight DESC LIMIT 8
            """,
            [current, *_CHAIN_RELS],
        ).fetchall()
        for rel, source, weight in edges:
            if source in visited_b:
                continue
            visited_b.add(source)
            snode = con.execute(
                "SELECT tipo, title, anno FROM nodes WHERE id = ? LIMIT 1", [source]
            ).fetchone()
            backward.append(
                {
                    "relation": rel,
                    "source_id": source,
                    "source_tipo": snode[0] if snode else "?",
                    "source_title": (snode[1] or "")[:100] if snode else "",
                    "source_anno": snode[2] if snode else None,
                    "weight": weight,
                }
            )
            queue_b.append((source, d + 1))

    temporal = []
    if _view_exists(con, "temporal"):
        temporal = con.execute(
            "SELECT relation, evidence FROM temporal WHERE source_id = ? LIMIT 5",
            [actual_id],
        ).fetchall()

    top_riferimenti_in: list[dict[str, Any]] = []
    top_riferimenti_out: list[dict[str, Any]] = []
    if not backward:
        rows_in = con.execute(
            """
            SELECT e.source_id, e.weight, n.tipo, LEFT(n.title, 120), n.anno
            FROM edges e JOIN nodes n ON n.id = e.source_id
            WHERE e.target_id = ? AND e.relation = 'riferimento'
            ORDER BY e.weight DESC LIMIT 5
            """,
            [actual_id],
        ).fetchall()
        top_riferimenti_in = [
            {"source_id": r[0], "weight": r[1], "tipo": r[2], "title": r[3], "anno": r[4]}
            for r in rows_in
        ]
    if not forward:
        rows_out = con.execute(
            """
            SELECT e.target_id, e.weight, n.tipo, LEFT(n.title, 120), n.anno
            FROM edges e JOIN nodes n ON n.id = e.target_id
            WHERE e.source_id = ? AND e.relation = 'riferimento'
            ORDER BY e.weight DESC LIMIT 5
            """,
            [actual_id],
        ).fetchall()
        top_riferimenti_out = [
            {"target_id": r[0], "weight": r[1], "tipo": r[2], "title": r[3], "anno": r[4]}
            for r in rows_out
        ]

    return {
        "view": "chain",
        "depth": depth,
        "node": _node_payload(node_row),
        "chain_forward": forward,
        "chain_backward": backward,
        "top_riferimenti_out": top_riferimenti_out,
        "top_riferimenti_in": top_riferimenti_in,
        "attua_delega_hint": _attua_delega_hint(con, node_row),
        "temporal": [{"relation": t[0], "evidence": t[1]} for t in temporal],
        "note": (
            "chain_* = relazioni legislative tipizzate + modifiche AKN "
            "(abroga, sostituisce, split, join, renumbering). "
            "top_riferimenti_* = campione riferimento se la chain è vuota. "
            "attua_delega_hint = euristica dal titolo (legge-base citata)."
        ),
    }


def _view_jurisprudence(con: duckdb.DuckDBPyConnection, node_row: tuple) -> dict[str, Any]:
    actual_id = node_row[0]
    _refresh_views(con)
    impugna = con.execute(
        """
        SELECT e.target_id, n.tipo, LEFT(n.title, 120), n.anno, e.weight
        FROM edges e LEFT JOIN nodes n ON e.target_id = n.id
        WHERE e.source_id = ? AND e.relation = 'impugna'
        ORDER BY e.weight DESC LIMIT 15
        """,
        [actual_id],
    ).fetchall()
    impugnata_da = con.execute(
        """
        SELECT e.source_id, n.tipo, LEFT(n.title, 120), n.anno, e.weight
        FROM edges e LEFT JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'impugna'
        ORDER BY e.weight DESC LIMIT 15
        """,
        [actual_id],
    ).fetchall()
    # LEFT JOIN: non azzera la lista se qualche target node manca nel mart
    try:
        parametri = con.execute(
            """
            SELECT e.target_id, LEFT(n.title, 120), e.weight
            FROM edges e LEFT JOIN nodes n ON e.target_id = n.id
            WHERE e.source_id = ? AND e.relation = 'invoca_parametro'
            ORDER BY e.weight DESC, e.target_id LIMIT 20
            """,
            [actual_id],
        ).fetchall()
    except duckdb.Error:
        parametri = []
    if not parametri:
        # fallback: articoli Cost. invocati come target di impugna
        parametri = con.execute(
            """
            SELECT e.target_id, LEFT(n.title, 120), e.weight
            FROM edges e LEFT JOIN nodes n ON e.target_id = n.id
            WHERE e.source_id = ? AND e.relation = 'impugna'
              AND (e.target_id LIKE 'costituzione:%' OR n.source = 'costituzione')
            ORDER BY e.weight DESC, e.target_id LIMIT 20
            """,
            [actual_id],
        ).fetchall()
    citata_da = con.execute(
        """
        SELECT e.source_id, n.tipo, LEFT(n.title, 120), n.anno, e.weight
        FROM edges e LEFT JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'cita_costituzione'
        ORDER BY e.weight DESC LIMIT 15
        """,
        [actual_id],
    ).fetchall()
    # relatore_sentenza: da sentenza → giudice; da giudice → sentenze redate
    relatore = con.execute(
        """
        SELECT e.target_id, LEFT(n.title, 120), e.evidence
        FROM edges e LEFT JOIN nodes n ON e.target_id = n.id
        WHERE e.source_id = ? AND e.relation = 'relatore_sentenza'
        LIMIT 5
        """,
        [actual_id],
    ).fetchall()
    sentenze_relatore = con.execute(
        """
        SELECT e.source_id, LEFT(n.title, 120), n.anno
        FROM edges e LEFT JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'relatore_sentenza'
        ORDER BY n.anno DESC LIMIT 15
        """,
        [actual_id],
    ).fetchall()
    parametri_out = []
    for r in parametri:
        tid = r[0] or ""
        art_m = re.search(r"costituzione:art:(\d+)", tid)
        parametri_out.append(
            {
                "id": tid,
                "articolo": art_m.group(1) if art_m else tid,
                "title": r[1],
                "weight": r[2],
            }
        )
    return {
        "view": "jurisprudence",
        "node": _node_payload(node_row),
        "impugna": [
            {"id": r[0], "tipo": r[1], "title": r[2], "anno": r[3], "weight": r[4]}
            for r in impugna
        ],
        "impugnata_da": [
            {"id": r[0], "tipo": r[1], "title": r[2], "anno": r[3], "weight": r[4]}
            for r in impugnata_da
        ],
        "parametri_invocati": parametri_out,
        "n_parametri": len(parametri_out),
        "citata_da": [
            {"id": r[0], "tipo": r[1], "title": r[2], "anno": r[3], "weight": r[4]}
            for r in citata_da
        ],
        "relatore": [
            {"id": r[0], "title": r[1], "ecli": r[2]} for r in relatore
        ],
        "sentenze_relatore": [
            {"id": r[0], "title": r[1], "anno": r[2]} for r in sentenze_relatore
        ],
    }


def _view_parliament(con: duckdb.DuckDBPyConnection, node_row: tuple) -> dict[str, Any]:
    actual_id = node_row[0]
    use_emend_leg = _view_exists(con, "emend_leg")
    if use_emend_leg:
        n_emendamenti = con.execute(
            "SELECT COALESCE(SUM(n_emend), 0) FROM emend_leg WHERE target_id = ?",
            [actual_id],
        ).fetchone()[0]
    else:
        n_emendamenti = con.execute(
            "SELECT COUNT(*) FROM edges WHERE target_id = ? AND relation = 'emendamento'",
            [actual_id],
        ).fetchone()[0]
    parent_title = node_row[2] or ""
    ddl_m = re.search(r"\bDDL\s+(\d+)", parent_title, re.IGNORECASE)
    emendamenti: list[tuple] = []
    sample_source = "edges"
    if use_emend_leg:
        # campioni dal pre-aggregato (id reali emendamento, non nodi generici)
        emendamenti = con.execute(
            """
            SELECT el.sample_emend_id, LEFT(n.title, 140), n.anno, NULL::DOUBLE AS weight,
                   n.source_filename
            FROM emend_leg el
            LEFT JOIN nodes n ON n.id = el.sample_emend_id
            WHERE el.target_id = ?
              AND el.sample_emend_id IS NOT NULL
              AND el.sample_emend_id LIKE 'senato:emend:%'
            ORDER BY el.n_emend DESC, el.sample_emend_id
            LIMIT 15
            """,
            [actual_id],
        ).fetchall()
        sample_source = "emend_leg"
    if not emendamenti:
        emendamenti = con.execute(
            """
            SELECT e.source_id, LEFT(n.title, 140), n.anno, e.weight,
                   n.source_filename
            FROM edges e
            LEFT JOIN nodes n ON e.source_id = n.id
            WHERE e.target_id = ? AND e.relation = 'emendamento'
              AND e.source_id LIKE 'senato:emend:%'
            ORDER BY e.weight DESC, e.source_id
            LIMIT 15
            """,
            [actual_id],
        ).fetchall()
        sample_source = "edges"
    # difesa: mai id che non sono emendamenti
    emendamenti = [
        r
        for r in emendamenti
        if (r[0] or "").startswith("senato:emend:")
    ]
    if ddl_m and not any(r[1] for r in emendamenti):
        # campioni vuoti o senza titolo: campiona per numero DDL nei source_id
        ddl_n = ddl_m.group(1)
        emendamenti = con.execute(
            """
            SELECT e.source_id, LEFT(n.title, 140), n.anno, e.weight, n.source_filename
            FROM edges e
            LEFT JOIN nodes n ON e.source_id = n.id
            WHERE e.target_id = ? AND e.relation = 'emendamento'
              AND (e.source_id ILIKE ? OR LOWER(COALESCE(n.title,'')) ILIKE ?)
            ORDER BY e.source_id
            LIMIT 15
            """,
            [actual_id, f"%DDL {ddl_n}%", f"%ddl {ddl_n}%"],
        ).fetchall()
    # se ancora vuoti, almeno id emendamento senza join (coerenza count)
    if not emendamenti:
        emendamenti = [
            (r[0], None, None, None, None)
            for r in con.execute(
                """
                SELECT source_id FROM edges
                WHERE target_id = ? AND relation = 'emendamento'
                ORDER BY weight DESC, source_id LIMIT 15
                """,
                [actual_id],
            ).fetchall()
        ]
    # I nodi emendamento hanno titolo unico per emendamento: raggruppare per
    # title dà sempre n=1. Utile: distribuzione per legislatura (dal source_id).
    if use_emend_leg:
        top_emendatori = con.execute(
            """
            SELECT legislatura, n_emend
            FROM emend_leg
            WHERE target_id = ? AND legislatura IS NOT NULL
            ORDER BY n_emend DESC
            LIMIT 8
            """,
            [actual_id],
        ).fetchall()
    else:
        top_emendatori = con.execute(
            """
            SELECT
              CAST(NULLIF(regexp_extract(e.source_id, 'senato:emend:(\\d+):', 1), '') AS INTEGER)
                AS legislatura,
              COUNT(*) AS cnt
            FROM edges e
            WHERE e.target_id = ? AND e.relation = 'emendamento'
            GROUP BY 1
            ORDER BY 2 DESC
            LIMIT 8
            """,
            [actual_id],
        ).fetchall()
    top_emendatori = [
        {"legislatura": r[0], "n_emendamenti": r[1]} for r in top_emendatori if r[0] is not None
    ]
    n_interventi = con.execute(
        "SELECT COUNT(*) FROM edges WHERE target_id = ? AND relation = 'intervento'",
        [actual_id],
    ).fetchone()[0]
    interventi = con.execute(
        """
        SELECT e.source_id, LEFT(n.title, 120), n.anno
        FROM edges e JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'intervento'
        ORDER BY n.anno DESC LIMIT 10
        """,
        [actual_id],
    ).fetchall()
    diventa = con.execute(
        """
        SELECT e.target_id, LEFT(n.title, 120), n.anno
        FROM edges e JOIN nodes n ON e.target_id = n.id
        WHERE e.source_id = ? AND e.relation = 'diventa_legge'
        LIMIT 5
        """,
        [actual_id],
    ).fetchall()
    testo = con.execute(
        """
        SELECT e.target_id, LEFT(n.title, 120)
        FROM edges e JOIN nodes n ON e.target_id = n.id
        WHERE e.source_id = ? AND e.relation = 'testo_atto'
        LIMIT 5
        """,
        [actual_id],
    ).fetchall()
    # firmatari (Camera deputato:*/Senato senatore:* → atto) — edge incoming
    n_firmatari = con.execute(
        "SELECT COUNT(*) FROM edges WHERE target_id = ? AND relation = 'firmatario'",
        [actual_id],
    ).fetchone()[0]
    firmatari = con.execute(
        """
        SELECT e.source_id, LEFT(n.title, 120), e.weight, e.evidence
        FROM edges e LEFT JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'firmatario'
        ORDER BY e.weight DESC, e.source_id LIMIT 15
        """,
        [actual_id],
    ).fetchall()
    return {
        "view": "parliament",
        "node": _node_payload(node_row),
        "n_emendamenti": n_emendamenti,
        "note": "n_emendamenti/n_interventi = COUNT(*); liste = campioni LIMIT.",
        "emendamenti": [
            {
                "id": r[0],
                "title": r[1],
                "anno": r[2],
                "weight": r[3],
                "source_filename": r[4],
            }
            for r in emendamenti
        ],
        "emendamenti_note": (
            f"Campioni da {sample_source}; solo id senato:emend:*. "
            "title mancante se il nodo emendamento non è nel mart nodes."
        ),
        "emendamenti_per_legislatura": top_emendatori,
        "n_interventi": n_interventi,
        "interventi": [{"id": r[0], "title": r[1], "anno": r[2]} for r in interventi],
        "diventa_legge": [{"id": r[0], "title": r[1], "anno": r[2]} for r in diventa],
        "testo": [{"id": r[0], "title": r[1]} for r in testo],
        "n_firmatari": n_firmatari,
        "firmatari": [
            {"id": r[0], "title": r[1], "weight": r[2], "ruolo": r[3]}
            for r in firmatari
        ],
    }


def _impl_node(node_id: str, view: str = "overview", depth: int = 3) -> dict[str, Any]:
    con = _get_con()
    view = (view or "overview").strip().lower()
    if view not in _NODE_VIEWS:
        return {
            "error": f"view non valida: {view!r}. Ammesse: {', '.join(_NODE_VIEWS)}"
        }
    node_row = _find_node(con, node_id)
    if node_row is None:
        return {"error": f"Nodo non trovato: {node_id!r}"}
    actual_id = node_row[0]
    try:
        if view == "overview":
            return _view_overview(con, node_row)
        if view == "chain":
            return _view_chain(con, node_row, depth)
        if view == "jurisprudence":
            return _view_jurisprudence(con, node_row)
        return _view_parliament(con, node_row)
    except Exception as e:  # noqa: BLE001 — non far crashare l'MCP su nodi pesanti
        return {
            "error": f"view={view} fallita per {actual_id!r}: {type(e).__name__}: {e}",
            "node_id": actual_id,
        }

