"""Lookup nodi, payload, intelligence e 4 viste di legal_node."""
from __future__ import annotations

import re
from typing import Any

import duckdb

from legal_graph.mcp import db
from legal_graph.mcp.db import _has_col, _view_exists, locked
from legal_graph.mcp.textfold import _fold
from legal_graph.resolve import Resolved, resolve_row

_NODE_VIEWS = ("overview", "chain", "jurisprudence", "parliament")
# Fallback per mart legacy: la sorgente primaria è `relation_type.in_chain_view`
# (mart_legal_relation_type). Questa lista non va estesa.
_CHAIN_RELS_FALLBACK = (
    "diventa_legge",
    "attua_delega",
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


def _chain_rels(con: duckdb.DuckDBPyConnection) -> tuple[str, ...]:
    """Relazioni attraversate da `view=chain`, lette dal vocabolario.

    `in_chain_view` esclude `riferimento` (221k edge: la vista chain mostra
    la catena legislativa, le citazioni hanno il fallback dedicato) e le
    relazioni con una vista propria (`modifica`, `impugna`).
    """
    if _view_exists(con, "relation_type") and _has_col(
        con, "relation_type", "in_chain_view"
    ):
        try:
            rows = con.execute(
                "SELECT relation FROM relation_type WHERE in_chain_view "
                "ORDER BY relation"
            ).fetchall()
            if rows:
                return tuple(str(r[0]) for r in rows)
        except duckdb.Error:
            pass
    return _CHAIN_RELS_FALLBACK

def _find_node(con: duckdb.DuckDBPyConnection, node_id: str) -> Resolved:
    """Risolvi l'id su `nodes`: exact → alias → parziale solo se unico.

    Il match parziale ambiguo non sceglie: restituisce i candidati, così
    `legal_node` risponde "id ambiguo" invece di consegnare un altro atto.
    """
    cols = (
        "id, tipo, title, CAST(data AS VARCHAR) AS data, anno, source, "
        "length_chars, length_words, celex, collezione, source_filename, "
        "stato, materia, qualita_score, sunsetting_score"
    )
    # vigenza_fonte (IC #74) ed eiv (akn_act_meta 2026-10): colonne nuove,
    # assenti sui mart legacy → placeholder NULL per mantenere le posizioni
    # fisse attese da _node_payload.
    cols += (
        ", vigenza_fonte"
        if _has_col(con, "nodes", "vigenza_fonte")
        else ", NULL::VARCHAR AS vigenza_fonte"
    )
    cols += (
        ", eiv" if _has_col(con, "nodes", "eiv") else ", NULL::VARCHAR AS eiv"
    )
    # Alias di identità (M1): `camera:atto:17_100` → `camera:17:100`,
    # `dl:*`/`norma:*` → URN. Il resolver interroga `entity_alias` solo se
    # l'id esatto non esiste: gli id canonici non pagano il lookup (che su
    # GCS è un read remoto in più).
    alias_source = (
        "entity_alias" if _view_exists(con, "entity_alias") else None
    )
    return resolve_row(
        con,
        source="nodes",
        cols=cols,
        node_id=node_id,
        ecli_alias=True,
        alias_source=alias_source,
    )


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
    # vigenza_fonte: origine di stato (IC #74) — opzionale (mart legacy)
    if len(row) > 15:
        payload["vigenza_fonte"] = row[15]
    # EIV AKN (opzionale — mart legacy senza colonna)
    if len(row) > 16:
        payload["eiv"] = row[16]
    return payload


def _tabella_in(con: duckdb.DuckDBPyConnection) -> str:
    """Relazione archi per le query "in entrata".

    `edges_in` è la stessa tabella di `edges` ordinata per `target_id`: con il
    row-group pruning le query che filtrano per target costano ~14ms invece di
    ~50ms. Se il mart non è pubblicato si usa `edges` (stessi risultati).
    """
    return "edges_in" if _view_exists(con, "edges_in") else "edges"


def _relation_counts(con: duckdb.DuckDBPyConnection, actual_id: str) -> dict[str, Any]:
    if _view_exists(con, "node_rel"):
        # una scansione sola: dentro e fuori stanno nella stessa riga. Le due
        # query separate costavano due scan del mart (5,7MB) per un nodo.
        rows = con.execute(
            "SELECT relation, n_in, n_out FROM node_rel WHERE id = ?",
            [actual_id],
        ).fetchall()
        out = {r[0]: int(r[2] or 0) for r in rows}
        inn = {r[0]: int(r[1] or 0) for r in rows}
        def top(conteggi: dict[str, int]) -> dict[str, int]:
            """Top 12, tie-break sul nome: ordine stabile tra chiamate."""
            return dict(sorted(conteggi.items(), key=lambda kv: (-kv[1], kv[0]))[:12])

        return {"out": top(out), "in": top(inn), "source": "mart_legal_node_rel"}
    out = dict(
        con.execute(
            """
            SELECT relation, COUNT(*) FROM edges WHERE source_id = ?
            GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 12
            """,
            [actual_id],
        ).fetchall()
    )
    inn = dict(
        con.execute(
            f"""
            SELECT relation, COUNT(*) FROM {_tabella_in(con)} WHERE target_id = ?
            GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 12
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
    tabella = _tabella_in(con) if column == "target_id" else "edges"
    try:
        rows = con.execute(
        f"""
        SELECT e.relation, e.{join_col}, e.weight, e.source_year, e.target_year,
               n.tipo, LEFT(n.title, 90)
        FROM {tabella} e
        LEFT JOIN nodes n ON n.id = e.{join_col}
        WHERE e.{column} = ?
        ORDER BY e.weight DESC, e.{join_col}, e.relation
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


def _node_intelligence(
    con: duckdb.DuckDBPyConnection, actual_id: str
) -> dict[str, Any] | None:
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
    # fallback LIKE per URN non normalizzate: solo se il match è unico
    if legge is None:
        rows = con.execute(
            """
            SELECT tipo, title, anno FROM nodes
            WHERE source='normativa' AND id LIKE ?
            ORDER BY length(id), id LIMIT 2
            """,
            [f"%legge:{hint['legge_anno']}%;{hint['legge_n']}"],
        ).fetchall()
        legge = rows[0] if len(rows) == 1 else None
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
        intel = _node_intelligence(con, actual_id)
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


def _bfs(
    con: duckdb.DuckDBPyConnection,
    start: str,
    depth: int,
    *,
    outbound: bool,
) -> list[tuple[str, str, str, int, str, int | None]]:
    """BFS per livelli: **una query per livello**, non una per nodo.

    La versione precedente faceva 2 query per ogni nodo visitato (arco + nodo):
    ~71 query per `depth=3`, ognuna uno scan completo di `edges` (1,2M righe,
    nessun indice su parquet) → 3,3s sul codice 36/2023. Qui il frontier del
    livello è un `IN (...)`: 3 scan per direzione.

    Il tie-break esplicito `ORDER BY weight DESC, {nid}` non è cosmetico: i
    pesi sono interi piccoli, e senza tie-break il `LIMIT 8` per nodo sceglieva
    righe diverse a ogni chiamata (ordine di scan parallelo) → la stessa catena
    cambiava da una chiamata all'altra.
    """
    a, b = ("source_id", "target_id") if outbound else ("target_id", "source_id")
    tabella = "edges" if outbound else _tabella_in(con)
    rels = _chain_rels(con)
    in_rel = ",".join("?" * len(rels))
    visited = {start}
    frontier = [start]
    trovate: list[tuple[str, str, str, int, str, int | None]] = []
    for _ in range(depth):
        if not frontier:
            break
        placeholders = ",".join("?" * len(frontier))
        rows = con.execute(
            f"""
            WITH cand AS (
                SELECT e.{a} AS parent, e.{b} AS nid, e.relation, e.weight,
                       ROW_NUMBER() OVER (
                           PARTITION BY e.{a}
                           ORDER BY e.weight DESC, e.{b}, e.relation
                       ) AS rn
                FROM {tabella} e
                WHERE e.{a} IN ({placeholders}) AND e.relation IN ({in_rel})
            )
            SELECT c.parent, c.nid, c.relation, c.weight,
                   n.tipo, LEFT(n.title, 100), n.anno
            FROM cand c LEFT JOIN nodes n ON n.id = c.nid
            WHERE c.rn <= 8
            """,
            [*frontier, *rels],
        ).fetchall()
        posizione = {nodo: i for i, nodo in enumerate(frontier)}
        # `relation` chiude l'ordinamento: due relazioni diverse verso lo stesso
        # nodo (es. abroga + sostituisce) hanno lo stesso peso, e senza il quarto
        # campo l'etichetta che esce dipende dall'ordine di scan.
        rows.sort(key=lambda r: (posizione[r[0]], -r[3], r[1], r[2]))
        prossimo: list[str] = []
        for _parent, nid, relation, weight, tipo, title, anno in rows:
            if nid in visited:
                continue
            visited.add(nid)
            prossimo.append(nid)
            trovate.append((relation, nid, tipo or "?", title or "", weight, anno))
        frontier = prossimo
    return trovate


def _view_chain(con: duckdb.DuckDBPyConnection, node_row: tuple, depth: int) -> dict[str, Any]:
    actual_id = node_row[0]
    depth = max(1, min(int(depth or 3), 5))

    forward = [
        {
            "relation": relation,
            "target_id": nid,
            "target_tipo": tipo,
            "target_title": title,
            "target_anno": anno,
            "weight": weight,
        }
        for relation, nid, tipo, title, weight, anno in _bfs(
            con, actual_id, depth, outbound=True
        )
    ]
    backward = [
        {
            "relation": relation,
            "source_id": nid,
            "source_tipo": tipo,
            "source_title": title,
            "source_anno": anno,
            "weight": weight,
        }
        for relation, nid, tipo, title, weight, anno in _bfs(
            con, actual_id, depth, outbound=False
        )
    ]

    top_riferimenti_in: list[dict[str, Any]] = []
    top_riferimenti_out: list[dict[str, Any]] = []
    if not backward:
        rows_in = con.execute(
            """
            SELECT e.source_id, e.weight, n.tipo, LEFT(n.title, 120), n.anno
            FROM edges e JOIN nodes n ON n.id = e.source_id
            WHERE e.target_id = ? AND e.relation = 'riferimento'
            ORDER BY e.weight DESC, e.source_id LIMIT 5
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
            ORDER BY e.weight DESC, e.target_id LIMIT 5
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
        "note": (
            "chain_* = relazioni legislative tipizzate + modifiche AKN "
            "(abroga, sostituisce, split, join, renumbering). "
            "top_riferimenti_* = campione riferimento se la chain è vuota. "
            "attua_delega_hint = euristica dal titolo (legge-base citata)."
        ),
    }


def _view_jurisprudence(con: duckdb.DuckDBPyConnection, node_row: tuple) -> dict[str, Any]:
    actual_id = node_row[0]
    tabella_in = _tabella_in(con)
    impugna = con.execute(
        """
        SELECT e.target_id, n.tipo, LEFT(n.title, 120), n.anno, e.weight
        FROM edges e LEFT JOIN nodes n ON e.target_id = n.id
        WHERE e.source_id = ? AND e.relation = 'impugna'
        ORDER BY e.weight DESC, e.target_id LIMIT 15
        """,
        [actual_id],
    ).fetchall()
    impugnata_da = con.execute(
        f"""
        SELECT e.source_id, n.tipo, LEFT(n.title, 120), n.anno, e.weight
        FROM {tabella_in} e LEFT JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'impugna'
        ORDER BY e.weight DESC, e.source_id LIMIT 15
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
        f"""
        SELECT e.source_id, n.tipo, LEFT(n.title, 120), n.anno, e.weight
        FROM {tabella_in} e LEFT JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'cita_costituzione'
        ORDER BY e.weight DESC, e.source_id LIMIT 15
        """,
        [actual_id],
    ).fetchall()
    # relatore_sentenza: da sentenza → giudice; da giudice → sentenze redate
    relatore = con.execute(
        """
        SELECT e.target_id, LEFT(n.title, 120), e.evidence
        FROM edges e LEFT JOIN nodes n ON e.target_id = n.id
        WHERE e.source_id = ? AND e.relation = 'relatore_sentenza'
        ORDER BY e.target_id
        LIMIT 5
        """,
        [actual_id],
    ).fetchall()
    sentenze_relatore = con.execute(
        f"""
        SELECT e.source_id, LEFT(n.title, 120), n.anno
        FROM {tabella_in} e LEFT JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'relatore_sentenza'
        ORDER BY n.anno DESC, e.source_id LIMIT 15
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
    tabella_in = _tabella_in(con)
    use_emend_leg = _view_exists(con, "emend_leg")
    if use_emend_leg:
        n_emendamenti = con.execute(
            "SELECT COALESCE(SUM(n_emend), 0) FROM emend_leg WHERE target_id = ?",
            [actual_id],
        ).fetchone()[0]
    else:
        n_emendamenti = con.execute(
            f"SELECT COUNT(*) FROM {tabella_in} WHERE target_id = ? AND relation = 'emendamento'",
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
            f"""
            SELECT e.source_id, LEFT(n.title, 140), n.anno, e.weight,
                   n.source_filename
            FROM {tabella_in} e
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
            f"""
            SELECT e.source_id, LEFT(n.title, 140), n.anno, e.weight, n.source_filename
            FROM {tabella_in} e
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
                f"""
                SELECT source_id FROM {tabella_in}
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
            ORDER BY n_emend DESC, legislatura
            LIMIT 8
            """,
            [actual_id],
        ).fetchall()
    else:
        top_emendatori = con.execute(
            f"""
            SELECT
              CAST(NULLIF(regexp_extract(e.source_id, 'senato:emend:(\\d+):', 1), '') AS INTEGER)
                AS legislatura,
              COUNT(*) AS cnt
            FROM {tabella_in} e
            WHERE e.target_id = ? AND e.relation = 'emendamento'
            GROUP BY 1
            ORDER BY 2 DESC, 1
            LIMIT 8
            """,
            [actual_id],
        ).fetchall()
    top_emendatori = [
        {"legislatura": r[0], "n_emendamenti": r[1]} for r in top_emendatori if r[0] is not None
    ]
    n_interventi = con.execute(
        f"SELECT COUNT(*) FROM {tabella_in} WHERE target_id = ? AND relation = 'intervento'",
        [actual_id],
    ).fetchone()[0]
    interventi = con.execute(
        f"""
        SELECT e.source_id, LEFT(n.title, 120), n.anno
        FROM {tabella_in} e JOIN nodes n ON e.source_id = n.id
        WHERE e.target_id = ? AND e.relation = 'intervento'
        ORDER BY n.anno DESC, e.source_id LIMIT 10
        """,
        [actual_id],
    ).fetchall()
    diventa = con.execute(
        """
        SELECT e.target_id, LEFT(n.title, 120), n.anno
        FROM edges e JOIN nodes n ON e.target_id = n.id
        WHERE e.source_id = ? AND e.relation = 'diventa_legge'
        ORDER BY e.target_id
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
        f"SELECT COUNT(*) FROM {tabella_in} WHERE target_id = ? AND relation = 'firmatario'",
        [actual_id],
    ).fetchone()[0]
    firmatari = con.execute(
        f"""
        SELECT e.source_id, LEFT(n.title, 120), e.weight, e.evidence
        FROM {tabella_in} e LEFT JOIN nodes n ON e.source_id = n.id
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


@locked
def _impl_node(node_id: str, view: str = "overview", depth: int = 3) -> dict[str, Any]:
    con = db._get_con()
    view = (view or "overview").strip().lower()
    if view not in _NODE_VIEWS:
        return {
            "error": f"view non valida: {view!r}. Ammesse: {', '.join(_NODE_VIEWS)}"
        }
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
    node_row = found.row
    actual_id = node_row[0]
    try:
        if view == "overview":
            out = _view_overview(con, node_row)
        elif view == "chain":
            out = _view_chain(con, node_row, depth)
        elif view == "jurisprudence":
            out = _view_jurisprudence(con, node_row)
        else:
            out = _view_parliament(con, node_row)
    except Exception as e:  # noqa: BLE001 — non far crashare l'MCP su nodi pesanti
        return {
            "error": f"view={view} fallita per {actual_id!r}: {type(e).__name__}: {e}",
            "node_id": actual_id,
        }
    # Un id parziale è un'inferenza: dichiarala, così chi legge sa che non è
    # stato passato l'id esatto.
    if found.match != "exact":
        out["matched_by"] = found.match
    return out

