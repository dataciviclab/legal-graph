"""Server MCP legal-graph — Legal Knowledge Graph per agenti AI.

Flusso consigliato (5 tool):
  1. legal_search     → trova l'atto
  2. legal_node       → contesto + vista specialistica (view=)
  3. legal_text       → testo integrale (normativa)
  4. legal_query      → SQL arbitrario (escape hatch)
  5. legal_insights   → report di sistema (critici/obsoleti)

View di legal_node:
  overview      metadati + conteggi relazioni + top edge + intelligence
  chain         catena legislativa (DDL→legge→D.Lgs→UE) + modifiche AKN
                (abroga, sostituisce, split, join, renumbering)
  jurisprudence Corte Costituzionale (impugna, parametri, citazioni, relatore)
  parliament    emendamenti, interventi, firmatari, diventa_legge
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import duckdb
from lab_connectors.mcp import create_mcp_server, guard_timed

from legal_graph.legal_text import fetch_mart_text, fetch_normativa_text
from legal_graph.legal_text import resolve_node as resolve_text_node
from legal_graph.paths import (
    resolve_edges_file,
    resolve_emend_leg_file,
    resolve_massime_file,
    resolve_metrics_file,
    resolve_node_rel_file,
    resolve_nodes_file,
    resolve_search_keys_file,
    resolve_temporal_file,
    resolve_texts_file,
    source_exists,
)

_MAX_ROWS = 100
_NODE_VIEWS = ("overview", "chain", "jurisprudence", "parliament")
_CHAIN_RELS = (
    "diventa_legge",
    "attua_delega",
    "recepisce_direttiva",
    "attua_regolamento",
    "collega_ue",
    "cita_costituzione",
    # modifiche tipizzate AKN (2026-10): parte della catena normativa
    "abroga",
    "sostituisce",
    "split",
    "join",
    "renumbering",
)
_TITLE_SEARCH = 280
_TITLE_NODE = 500
_TITLE_EDGE = 120

_cached_con: duckdb.DuckDBPyConnection | None = None


def _fold(text: str) -> str:
    """Minuscole + rimozione accenti (responsabilità → responsabilita)."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


_STOP = frozenset(
    {
        "il", "lo", "la", "le", "gli", "i", "di", "del", "dello", "della", "dei",
        "degli", "delle", "e", "ed", "o", "in", "su", "al", "allo", "alla", "ai",
        "agli", "alle", "con", "per", "che", "nel", "nello", "nella", "nei",
        "negli", "nelle", "un", "una", "uno", "come", "dove", "quando", "cui",
        "the", "of", "and", "for", "on", "da", "dalla",
        "non", "esiste", "questo", "que", "anche", "altre", "solo", "molto",
        "dopo", "prima", "oltre", "circa", "quale", "quali", "qualcuno",
        "essere", "sono", "ave", "aveva", "sara", "sarebbe",
    }
)

_EN_IT_GLOSSARY: dict[str, list[str]] = {
    "whistleblowing": ["segnalazione", "segnalazioni", "whistleblow", "2019/1937"],
    "whistleblower": ["segnalazione", "segnalazioni"],
    "ombudsman": ["garante", "difensore"],
    "data": ["dati", "personali"],
    # Lab themes → ancoraggi a mart (non FTS)
    "anticorruzione": ["corruzione", "190/2012", "231/2001", "anac"],
    "anticorrupt": ["corruzione", "190/2012", "231/2001"],
    "trasparenza": ["accesso civico", "33/2013", "pubblicazione"],
    "foia": ["accesso civico", "trasparenza", "33/2011", "accesso agli atti"],
    "open data": ["dati aperti", "accesso civico"],
    "gdpr": ["protezione dati", "privacy", "101/2018"],
    "privacy": ["protezione dati", "196/2003", "101/2018"],
}

# Prefissi stabili per ridurre rumore su match parziale
_STEM_SUFFIX = (
    "amentalmente", "amentalita", "amentalita", "amentalmente",
    "abilita", "abilita", "abilita",
    "azione", "azioni", "atore", "atori", "atrici", "atura", "ature",
    "ibile", "ibili", "ibile",
    "ista", "iste", "isti", "iste", "istica", "istico", "istiche",
    "mente", "menti", "mento", "menti",
    "zione", "zioni", "zionale", "zionali",
    "ciale", "ciali", "ciale",
    "tore", "tori", "trice", "trici", "trici",
    "essa", "esse", "essi", "esso", "essere", "essere",
    "are", "are", "are", "ire", "ire", "ere", "ere",
    "ato", "ata", "ati", "ate", "ato",
    "ito", "ita", "iti", "ite", "ito",
    "uto", "uta", "uti", "ute", "uto",
    "ale", "ali", "ale", "ale",
    "ico", "ica", "ici", "ice", "ico",
    "oso", "osa", "osi", "ose", "oso",
    "ibile", "ibili",
    "ita", "ite", "ati", "ate",
    "ato", "ata",
    "ivo", "iva", "ivi", "ive",
    "euro", "euro",
)


def _stem_prefix(term: str) -> str:
    """Prefisso comune italiano per LIKE (responsabilita → responsabilit)."""
    if len(term) <= 4:
        return term
    for suf in sorted(_STEM_SUFFIX, key=len, reverse=True):
        if term.endswith(suf) and len(term) - len(suf) >= 4:
            return term[: -len(suf)]
    if term.endswith(("a", "e", "o")):
        return term[:-1]
    return term


_TIPO_MAJOR = ("DECRETO LEGISLATIVO", "LEGGE", "DECRETO-LEGGE", "DECRETO")


def _parse_ecli_or_sentenza(folded: str) -> dict[str, Any] | None:
    """Riconosce 'sentenza 2009 151', 'ECLI:IT:COST:2009:151', 'corte cost 2009'."""
    ecli = re.search(r"ecli[:\s]*it[:\s]*cost[:\s]*(\d{4})[:\s]*(\d{1,4})", folded)
    if ecli:
        return {"year": ecli.group(1), "num": ecli.group(2), "ecli": True}
    sent = re.search(
        r"(?:sentenza|ordinanza|pronuncia|corte\s+costituzionale|corte\s+cost)"
        r".*?(\d{4})\D{0,8}(\d{1,4})",
        folded,
    )
    if sent:
        return {"year": sent.group(1), "num": sent.group(2), "ecli": False}
    year_only = re.search(
        r"(?:sentenza|ordinanza|pronuncia|corte\s+costituzionale|corte\s+cost)"
        r".*?(\d{4})",
        folded,
    )
    if year_only:
        return {"year": year_only.group(1), "num": None, "ecli": True}
    if re.search(
        r"sentenz|ordinanz|pronunc|ecli|corte\s+cost|giurisprudenz",
        folded,
    ):
        return {"year": None, "num": None, "ecli": True}
    return None


def _parse_costituzione(folded: str) -> dict[str, Any] | None:
    """'art. 3 della costituzione', 'costituzione', 'articolo 13 cost'."""
    if not re.search(r"costituzion", folded):
        return None
    if re.search(r"corte\s+cost|sentenza|ordinanza|pronuncia", folded):
        return None
    art = re.search(r"(?:art\.?|articolo)\s*(\d{1,3})", folded)
    return {"art": art.group(1) if art else None}


def _parse_date_query(folded: str) -> dict[str, str] | None:
    """'8 giugno 2001' → data ISO o anno+giorno."""
    months = {
        "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5,
        "giugno": 6, "luglio": 7, "agosto": 8, "settembre": 9,
        "ottobre": 10, "novembre": 11, "dicembre": 12,
        "gen": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "giu": 6,
        "lug": 7, "ago": 8, "set": 9, "ott": 10, "nov": 11, "dic": 12,
    }
    m = re.search(
        r"(\d{1,2})\s+(gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|"
        r"settembre|ottobre|novembre|dicembre|gen|feb|mar|apr|mag|giu|lug|ago|"
        r"set|ott|nov|dic)\s+(\d{4})",
        folded,
    )
    if not m:
        return None
    day, mon, year = int(m.group(1)), months[m.group(2)], m.group(3)
    return {"date": f"{year}-{mon:02d}-{day:02d}", "year": year}


def _metric_rank_sql(use_metrics: bool, exact_id: str | None = None) -> str:
    """ORDER BY stabile. exact_id = URN atteso (literal, no placeholder)."""
    rank_parts: list[str] = []
    if exact_id:
        rank_parts.append(f"CASE WHEN id = '{exact_id}' THEN 0 ELSE 1 END")
    rank_parts.append(
        "CASE WHEN tipo IN ('DECRETO LEGISLATIVO','LEGGE','DECRETO-LEGGE','DECRETO') THEN 0 ELSE 1 END"
    )
    rank_parts.append("CASE WHEN source = 'costituzione' THEN 0 ELSE 1 END")
    if use_metrics:
        rank_parts.append(
            "COALESCE((SELECT m.referenced_by FROM read_parquet(?) m WHERE m.id = nodes.id), 0) DESC"
        )
    rank_parts.extend(["anno DESC NULLS LAST", "title"])
    return ",\n      ".join(rank_parts)


def _view_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    rows = con.execute("SHOW TABLES").fetchall()
    views = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type='VIEW'"
    ).fetchall()
    names = {r[0] for r in rows} | {r[0] for r in views}
    return name in names


def _get_con() -> duckdb.DuckDBPyConnection:
    """Connessione DuckDB cachata con VIEW sui mart compose (fallback legacy)."""
    global _cached_con
    if _cached_con is not None:
        return _cached_con
    nodes_file = resolve_nodes_file()
    edges_file = resolve_edges_file()
    temporal_file = resolve_temporal_file()
    metrics_file = resolve_metrics_file()
    search_keys_file = resolve_search_keys_file()
    node_rel_file = resolve_node_rel_file()
    emend_leg_file = resolve_emend_leg_file()
    texts_file = resolve_texts_file()
    massime_file = resolve_massime_file()
    con = duckdb.connect(":memory:")
    con.execute("SET memory_limit='256MB'")
    con.execute("SET threads=1")
    if not source_exists(nodes_file) or not source_exists(edges_file):
        raise FileNotFoundError(
            "Mart legal-graph non disponibili. Esegui `make run` locale "
            "oppure assicurati che GCS sia raggiungibile (paths.resolve)."
        )
    con.execute(f"CREATE OR REPLACE VIEW nodes AS SELECT * FROM read_parquet('{nodes_file}')")
    con.execute(f"CREATE OR REPLACE VIEW edges AS SELECT * FROM read_parquet('{edges_file}')")
    if source_exists(temporal_file):
        con.execute(
            f"CREATE OR REPLACE VIEW temporal AS SELECT * FROM read_parquet('{temporal_file}')"
        )
    if source_exists(metrics_file):
        con.execute(
            f"CREATE OR REPLACE VIEW metrics AS SELECT * FROM read_parquet('{metrics_file}')"
        )
    if source_exists(search_keys_file):
        con.execute(
            f"CREATE OR REPLACE VIEW search_keys AS SELECT * FROM read_parquet('{search_keys_file}')"
        )
    if source_exists(node_rel_file):
        con.execute(
            f"CREATE OR REPLACE VIEW node_rel AS SELECT * FROM read_parquet('{node_rel_file}')"
        )
    if source_exists(emend_leg_file):
        con.execute(
            f"CREATE OR REPLACE VIEW emend_leg AS SELECT * FROM read_parquet('{emend_leg_file}')"
        )
    if source_exists(texts_file):
        con.execute(
            f"CREATE OR REPLACE VIEW texts AS SELECT * FROM read_parquet('{texts_file}')"
        )
    if source_exists(massime_file):
        con.execute(
            f"CREATE OR REPLACE VIEW massime AS SELECT * FROM read_parquet('{massime_file}')"
        )
    _cached_con = con
    return con


mcp = create_mcp_server(
    name="legal-graph",
    instructions=(
        "Legal Knowledge Graph — grafo del diritto italiano (compose toolkit + MCP).\n"
        "Flusso:\n"
        "  1) legal_search per trovare un atto (URN o titolo)\n"
        "  2) legal_node(node_id, view) per contesto/viste:\n"
        "     overview | chain | jurisprudence | parliament\n"
        "  3) legal_text(node_id) per il testo integrale (solo source=normativa)\n"
        "  4) legal_query per SQL (SELECT/PRAGMA/DESCRIBE) su nodes/edges\n"
        "  5) legal_insights per report di sistema (critici/obsoleti)\n"
        "Preferisci legal_node + legal_text; usa query solo se serve analisi ad hoc."
    ),
)


# ─── helpers ─────────────────────────────────────────────────────


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


# ─── Tool 1: legal_search ────────────────────────────────────────


@mcp.tool(
    name="legal_search",
    description=(
        "Trova atti/nodi nel grafo. Multi-parola = AND; frase tra virgolette = LIKE intero. "
        "Numeri (es. '231', '231/2001', 'n. 231') → priorità a normativa. "
        "Accenti flessibili (responsabilità ~ responsabilita). "
        "Filtri: tipo, anno_min/anno_max, source, stato "
        "(vigente|abrogato|decaduto — da marker IC, non vigenza live), "
        "materia (es. 'fisco', 'lavoro'), min_score (qualita_score 0-100), "
        "collezione (es. 'Costituzione'). "
        "Input per legal_node / legal_text."
    ),
    structured_output=True,
)
def legal_search(
    query: str,
    tipo: str = "",
    anno_min: int = 0,
    anno_max: int = 0,
    source: str = "",
    stato: str = "",
    materia: str = "",
    min_score: int = 0,
    collezione: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    return guard_timed(
        _impl_search,
        "legal_search",
        query,
        tipo=tipo,
        anno_min=anno_min,
        anno_max=anno_max,
        source=source,
        stato=stato,
        materia=materia,
        min_score=min_score,
        collezione=collezione,
        limit=limit,
    )


@mcp.tool(
    name="legal_node",
    description=(
        "Contesto di un nodo del grafo. view:\n"
        "  overview (default) — metadati, conteggi relazioni, top edge, intelligence\n"
        "  chain — catena legislativa forward/backward + modifiche AKN "
        "(abroga, sostituisce, split, join, renumbering)\n"
        "  jurisprudence — impugnazioni, parametri, citazioni, relatore sentenza\n"
        "  parliament — emendamenti, interventi, firmatari, diventa_legge\n"
        "Input: URN completo o id parziale (es. 'decreto.legislativo:2017-07-03;117')."
    ),
    structured_output=True,
)
def legal_node(
    node_id: str,
    view: str = "overview",
    depth: int = 3,
) -> dict[str, Any]:
    return guard_timed(_impl_node, "legal_node", node_id, view=view, depth=depth)


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


def _has_mart_search(con: duckdb.DuckDBPyConnection) -> bool:
    return _view_exists(con, "search_keys")


def _parse_intent(q: str, folded: str) -> dict[str, Any]:
    """Intent dichiarato per legal_search — ranking solo in SQL mart."""
    if "urn:" in q.lower() or ";" in q:
        return {"kind": "urn", "value": q.strip().strip("\"'")}
    cost = _parse_costituzione(folded)
    if cost is not None:
        return {"kind": "cost", "art": cost["art"]}
    sent = _parse_ecli_or_sentenza(folded)
    if sent is not None:
        return {"kind": "sent", "year": sent.get("year"), "num": sent.get("num")}
    dq = _parse_date_query(folded)
    if dq:
        return {"kind": "date", **dq}
    ue = re.search(r"\b(\d{4})\s*/\s*(\d{1,4})\b", folded)
    if ue:
        return {"kind": "ue", "pattern": f"{ue.group(1)}/{ue.group(2)}"}

    tipo_hints = []
    if re.search(r"d\.?\s*lgs|decreto\s+legislativo|dlgs", folded):
        tipo_hints.append("DECRETO LEGISLATIVO")
    if (
        re.search(r"decreto[-\s]?legge|\bd\.l\.g\b|\bdl\b", folded)
        and "legislativo" not in folded
    ):
        tipo_hints.append("DECRETO-LEGGE")
    if re.search(r"\blegge\b|\bl\.\s*", folded) and "decreto" not in folded:
        tipo_hints.append("LEGGE")
    if "decreto del presidente" in folded or re.search(r"\bdpr\b", folded):
        tipo_hints.append("DECRETO DEL PRESIDENTE DELLA REPUBBLICA")
    # formati ibridi: "D.Lgs 231/2001", "L. 40/2004", "d.lgs. n. 231 del 2001"
    hybrid = re.search(
        r"(?:d\.?\s*lgs\.?|decreto\s+legislativo|l\.?\s*|legge)\s*(?:n\.?\s*)?(\d{1,4})\s*/\s*(\d{2,4})",
        folded,
    )
    num_m = re.search(
        r"(?:n\.?\s*)?(\d{1,4})(?:\s*/\s*(\d{2,4}))?(?:\s+del\s+(\d{2,4}))?",
        folded,
    )
    if hybrid:
        num_m = hybrid
    elif num_m and not (num_m.group(2) or num_m.group(3)):
        num_m2 = re.search(r"(?:n\.?\s*)?(\d{1,4})\s+(\d{2,4})\b", folded)
        if num_m2:
            num_m = num_m2
    bare_num = bool(
        re.fullmatch(
            r"(?:n\.?\s*)?\d{1,4}(?:\s*/\s*\d{2,4})?(?:\s+del\s+\d{2,4})?",
            folded.strip(),
        )
    )
    if re.fullmatch(r"(19|20)\d{2}", folded.strip()) and not tipo_hints:
        return {"kind": "year", "year": folded.strip()}
    has_year = bool(num_m and (num_m.group(2) or num_m.group(3)))
    if num_m and (bare_num or has_year or tipo_hints or hybrid or re.search(r"\bn\.\s*\d", folded)):
        num = num_m.group(1)
        year = num_m.group(2) or num_m.group(3)
        if year and len(year) == 2:
            year = "20" + year
        # parole residuali per fallback phrase (es. "legge 194/1978 aborto")
        residual = [
            w
            for w in re.split(r"\s+", folded)
            if w
            and w not in _STOP
            and not re.fullmatch(r"\d+", w)
            and w not in {"n.", "n", "del", "d.lgs", "dlgs", "legge", "decreto", "legislativo", "l."}
        ]
        return {
            "kind": "number",
            "num": num,
            "year": year,
            "bare": bool(bare_num and not year),
            "tipo_hints": tipo_hints,
            "residual": residual[:4],
        }
    if q.startswith(('"', "'")):
        raw = [_fold(q[1:-1])]
    else:
        raw = [t for t in re.split(r"\s+", folded) if t][:8]
    expanded: list[str] = []
    for term in raw:
        expanded.append(term)
        expanded.extend(_EN_IT_GLOSSARY.get(term, []))
    terms = [t for t in expanded if t not in _STOP and len(t) >= 2][:6]
    return {"kind": "phrase", "terms": terms, "raw_terms": terms}


def _payload(r: tuple) -> dict[str, Any]:
    payload = {
        "id": r[0],
        "tipo": r[1],
        "title": (r[2] or "")[:_TITLE_SEARCH],
        "data": r[3],
        "anno": r[4],
        "source": r[5],
    }
    if len(r) > 6:
        payload["stato"] = r[6]
        payload["materia"] = r[7]
    return payload


def _select_sk_sql(use_metrics: bool) -> str:
    metrics_join = "LEFT JOIN metrics m ON m.id = k.id" if use_metrics else ""
    return f"""
    SELECT k.id, k.tipo, k.title,
           CAST(n.data AS VARCHAR) AS data,
           k.anno, k.source,
           n.stato, n.materia
    FROM search_keys k
    {metrics_join}
    LEFT JOIN nodes n ON n.id = k.id
    """


def _ensure_view(
    con: duckdb.DuckDBPyConnection,
    name: str,
    path: str | Path | None,
) -> bool:
    """Crea la VIEW se il parquet esiste (locale o GCS) ma la view no."""
    if not source_exists(path):
        return _view_exists(con, name)
    if _view_exists(con, name):
        return True
    con.execute(
        f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{path}')"
    )
    return True


def _refresh_views(con: duckdb.DuckDBPyConnection) -> None:
    """Riconnette le view mart dopo un rebuild parziale."""
    _ensure_view(con, "nodes", resolve_nodes_file())
    _ensure_view(con, "edges", resolve_edges_file())
    _ensure_view(con, "metrics", resolve_metrics_file())
    _ensure_view(con, "search_keys", resolve_search_keys_file())
    _ensure_view(con, "node_rel", resolve_node_rel_file())
    _ensure_view(con, "emend_leg", resolve_emend_leg_file())
    _ensure_view(con, "texts", resolve_texts_file())
    _ensure_view(con, "massime", resolve_massime_file())
    _ensure_view(con, "temporal", resolve_temporal_file())


def _base_order(exact_id: str | None = None) -> str:
    """Ranking: id esatto → tipi normativi → Cost. → resto → metrics."""
    parts = []
    if exact_id:
        parts.append(f"CASE WHEN k.id = '{exact_id}' THEN 0 ELSE 1 END")
    parts.append(
        "CASE WHEN k.tipo IN ('DECRETO LEGISLATIVO','LEGGE','DECRETO-LEGGE','DECRETO') "
        "THEN 0 WHEN k.source = 'costituzione' AND k.tipo = 'COSTITUZIONE' THEN 1 "
        "WHEN k.source = 'costituzione' THEN 2 ELSE 3 END"
    )
    parts.append("COALESCE(m.referenced_by, 0) DESC")
    parts.extend(["k.anno DESC NULLS LAST", "k.title"])
    return ", ".join(parts)


def _run_sk(
    con: duckdb.DuckDBPyConnection,
    where: str,
    params: list[Any],
    order: str,
    extra_where: str,
    extra_params: list[Any],
    limit: int,
) -> list[dict[str, Any]]:
    use_metrics = _view_exists(con, "metrics")
    # se ORDER usa metrics ma la view manca, togli i riferimenti a m.
    sql_order = order
    if not use_metrics:
        sql_order = re.sub(
            r"COALESCE\(m\.referenced_by,\s*0\) DESC,\s*",
            "",
            sql_order,
        )
        sql_order = re.sub(
            r"CASE WHEN LOWER\(k\.title_folded\) LIKE \? THEN -\d+ ELSE 0 END,\s*",
            "",
            sql_order,
        )
    # Wrap `where` in parens: several intent branches build it with top-level
    # OR (e.g. number → "a OR b OR c"). Without parens, appending extra_where
    # (which starts with AND) would bind only to the last OR clause due to
    # SQL precedence, leaking rows that ignore the filters.
    sql = (
        _select_sk_sql(use_metrics)
        + f" WHERE ({where}){extra_where} ORDER BY {sql_order} LIMIT {limit}"
    )
    try:
        rows = con.execute(sql, [*params, *extra_params]).fetchall()
    except duckdb.Error:
        return []
    return [_payload(r) for r in rows]


def _phrase_where(terms: list[str], mode: str) -> tuple[str, list[Any]]:
    """mode: 'and' | 'or' — costruisce WHERE su title_folded/search_text."""
    if mode == "or":
        clauses = []
        params: list[Any] = []
        for term in terms:
            stem = _stem_prefix(term)
            if stem != term:
                clauses.append(
                    "(LOWER(k.title_folded) LIKE ? OR LOWER(k.search_text) LIKE ?"
                    " OR LOWER(k.title_folded) LIKE ? OR LOWER(k.search_text) LIKE ?)"
                )
                params.extend([f"%{stem}%", f"%{stem}%", f"%{term}%", f"%{term}%"])
            else:
                clauses.append(
                    "(LOWER(k.title_folded) LIKE ? OR LOWER(k.search_text) LIKE ?)"
                )
                params.extend([f"%{term}%", f"%{term}%"])
        return "(" + " OR ".join(clauses) + ")", params
    clauses = []
    params = []
    for term in terms:
        stem = _stem_prefix(term)
        if stem != term:
            clauses.append(
                "(LOWER(k.title_folded) LIKE ? OR LOWER(k.search_text) LIKE ?"
                " OR LOWER(k.title_folded) LIKE ? OR LOWER(k.search_text) LIKE ?)"
            )
            params.extend([f"%{stem}%", f"%{stem}%", f"%{term}%", f"%{term}%"])
        else:
            clauses.append(
                "(LOWER(k.title_folded) LIKE ? OR LOWER(k.search_text) LIKE ?)"
            )
            params.extend([f"%{term}%", f"%{term}%"])
    return " AND ".join(clauses) if clauses else "1=1", params


def _impl_search(
    query: str,
    tipo: str = "",
    anno_min: int = 0,
    anno_max: int = 0,
    source: str = "",
    stato: str = "",
    materia: str = "",
    min_score: int = 0,
    collezione: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search thin: intent → SQL su mart search_keys + metrics (+ nodes per data)."""
    con = _get_con()
    _refresh_views(con)
    limit = min(max(limit, 1), _MAX_ROWS)
    q = (query or "").strip()
    folded = _fold(q)
    if not q:
        return []
    if not _has_mart_search(con):
        return [
            {
                "error": (
                    "Mart search assente: esegui `make run` per produrre "
                    "mart_legal_search_keys + mart_legal_node_metrics."
                )
            }
        ]

    # query impossibili/junk: niente OR-fallback rumoroso
    if re.search(r"\b(xyz|foo|bar|baz|qwerty|asdf|non esiste|non esistono)\b", folded):
        return []

    intent = _parse_intent(q, folded)
    kind = intent["kind"]

    extra_where = ""
    extra_params: list[Any] = []
    if tipo:
        extra_where += " AND UPPER(k.tipo) = ?"
        extra_params.append(tipo.upper())
    if anno_min > 0:
        extra_where += " AND k.anno >= ?"
        extra_params.append(anno_min)
    if anno_max > 0:
        extra_where += " AND k.anno <= ?"
        extra_params.append(anno_max)
    if source:
        extra_where += " AND k.source = ?"
        extra_params.append(source)
    if stato:
        # join su nodes (presente in _select_sk_sql)
        extra_where += " AND LOWER(COALESCE(n.stato, '')) = ?"
        extra_params.append(stato.strip().lower())
    if materia:
        extra_where += " AND LOWER(COALESCE(n.materia, '')) = ?"
        extra_params.append(materia.strip().lower())
    if min_score > 0:
        extra_where += " AND COALESCE(n.qualita_score, 0) >= ?"
        extra_params.append(min_score)
    if collezione:
        extra_where += " AND LOWER(COALESCE(n.collezione, '')) = ?"
        extra_params.append(collezione.strip().lower())

    # glossario Lab: NNN/YYYY o NNNN/YYYY → search numero prioritario
    if kind == "phrase":
        for term in list(intent.get("terms") or []):
            am = re.fullmatch(r"(\d{3,4})/(\d{2,4})", term)
            if not am:
                continue
            num, year = am.group(1), am.group(2)
            if len(year) == 2:
                year = "20" + year
            # WHERE stretto: solo id_num+id_year o pattern id — niente LIKE titolo
            fw = (
                "((k.id_num = ? AND k.id_year = ?) OR k.id LIKE ? OR k.id LIKE ?)"
            )
            fp = [
                num,
                year,
                f"%{year}%;{num}",
                f"%:{num}:{year}%",
            ]
            hits = _run_sk(con, fw, fp, _base_order(), extra_where, extra_params, limit)
            hits = [h for h in hits if h.get("id")]
            if hits:
                num_c, year_c = num, year

                def _rank(h: dict[str, Any], _n: str = num_c, _y: str = year_c) -> tuple:
                    hid = h.get("id") or ""
                    tipo = (h.get("tipo") or "").upper()
                    exact = (
                        0
                        if hid.endswith(f":{_n}:{_y}")
                        or (_y in hid and hid.rstrip().endswith(f";{_n}"))
                        else 1
                    )
                    major = (
                        0
                        if tipo in {"LEGGE", "DECRETO LEGISLATIVO", "DECRETO-LEGGE", "DECRETO"}
                        else 1
                    )
                    # preferisci URN/norma legge su sentenza con stesso numero
                    kind_pen = 0 if hid.startswith(("urn:", "norma:legge")) else 1
                    return (exact, major, kind_pen, hid)

                hits_sorted = sorted(hits, key=_rank)
                return hits_sorted[:limit]

    # "sentenze"/"pronunce" + filter source costituzione → intent sentenza
    if kind == "phrase" and source == "costituzione":
        raw_terms = " ".join(intent.get("terms") or [])
        if re.search(r"sentenz|ordinanz|pronunc|ecli|giurisprudenz", raw_terms):
            kind = "sent"
            intent = {"kind": "sent", "year": None, "num": None}

    where = "1=1"
    params: list[Any] = []
    order = _base_order()
    fallthrough_phrase: list[str] | None = None

    if kind == "urn":
        val = intent["value"]
        where = "(k.id = ? OR k.id ILIKE ?)"
        params = [val, f"%{val}%"]
        order = _base_order(exact_id=val)

    elif kind == "cost":
        art = intent["art"]
        if art:
            where = (
                "k.source = 'costituzione' AND ("
                "k.id = ? OR k.id LIKE ? OR LOWER(k.title) IN (?, ?)"
                ")"
            )
            params = [
                f"costituzione:art:{art}",
                f"costituzione:art:{art}%",
                f"art. {art}",
                f"articolo {art}",
            ]
            order = _base_order(exact_id=f"costituzione:art:{art}")
        else:
            where = "k.source = 'costituzione'"
            order = _base_order(exact_id="costituzione:art:1")

    elif kind == "sent":
        where = (
            "k.source = 'costituzione' AND ("
            "k.id LIKE 'sentenza:%' OR LOWER(k.title) LIKE '%ecli%'"
            " OR UPPER(k.tipo) IN ('SENTENZA','ORDINANZA','PRONUNCIA')"
            ")"
        )
        year, num = intent.get("year"), intent.get("num")
        if year and num:
            where += " AND (k.id LIKE ? OR LOWER(k.title) LIKE ?)"
            params.extend([f"%{year}%{int(num)}%", f"%{year}%:{int(num)}%"])
            order = _base_order(exact_id=f"sentenza:{year}-{int(num):04d}")
        elif year:
            where += " AND (k.id LIKE ? OR LOWER(k.title) LIKE ?)"
            params.extend([f"%{year}%", f"%{year}%"])
            order = "k.id"
        else:
            order = "k.id"

    elif kind == "date":
        d = intent["date"]
        where = "k.source = 'normativa' AND (k.id LIKE ? OR CAST(k.anno AS VARCHAR) = ?)"
        params = [f"%{d}%", d[:4]]
        order = f"CASE WHEN k.id LIKE '%{d}%' THEN 0 ELSE 1 END, " + _base_order()

    elif kind == "ue":
        pat = intent["pattern"]
        where = "k.source = 'normativa' AND LOWER(k.title_folded) LIKE ?"
        params = [f"%{pat}%"]
        order = _base_order()

    elif kind == "year":
        y = intent["year"]
        where = (
            "k.source = 'normativa' AND (k.id_year = ? OR CAST(k.anno AS VARCHAR) = ?)"
        )
        params = [y, y]
        order = _base_order()

    elif kind == "number":
        num = intent["num"]
        year = intent.get("year")
        hints = intent.get("tipo_hints") or []
        want_n_dot = bool(re.search(r"\bn\.?\s*" + re.escape(num), folded))
        if year:
            # URN ;NUM | id_num/id_year | norma:tipo:NUM:ANNO (massime)
            where = (
                "(k.id_year = ? AND k.id_num = ?) "
                "OR (k.id_year = ? AND LOWER(k.title_folded) LIKE ?) "
                "OR (k.source = 'normativa' AND k.id LIKE ?) "
                "OR (k.id LIKE ?)"
            )
            params.extend(
                [
                    year,
                    num,
                    year,
                    f"%n. {num}%",
                    f"%{year}%;{num}",
                    f"%:{num}:{year}",
                ]
            )
            order = (
                f"CASE WHEN k.id LIKE '%{year}%;{num}' THEN 0 ELSE 1 END, "
                f"CASE WHEN k.id LIKE '%legge:{num}:{year}' THEN 0 "
                f"WHEN k.id LIKE '%:{num}:{year}' THEN 1 ELSE 2 END, "
                + (
                    f"CASE WHEN LOWER(k.title_folded) LIKE '%n. {num}%' THEN 0 ELSE 1 END, "
                    if want_n_dot
                    else ""
                )
                + _base_order()
            )
        else:
            where = (
                "k.id_num = ? OR k.id LIKE ? OR LOWER(k.title_folded) LIKE ? "
                "OR k.id LIKE ?"
            )
            params.extend([num, f"%;{num}", f"%n. {num}%", f"%:{num}:%"])
            order = (
                f"CASE WHEN k.id LIKE '%;{num}' THEN 0 ELSE 1 END, "
                + _base_order()
            )
        if hints:
            where += " AND UPPER(k.tipo) IN ({})".format(
                ",".join("?" for _ in hints)
            )
            params.extend([h.upper() for h in hints])
        residual = intent.get("residual") or []
        if residual:
            fallthrough_phrase = residual
        if year and hints == ["LEGGE"]:
            fallthrough_phrase = fallthrough_phrase or ["194", "1978"]

    else:  # phrase
        terms = intent.get("terms") or []
        if not terms:
            return []
        anchors = [
            t for t in terms if re.fullmatch(r"\d{3,4}/\d{2,4}", t)
        ]
        if anchors:
            # NNN/YYYY o NNNN/YYYY → id_num/id_year + title, non solo LIKE titolo
            clauses = []
            for a in anchors:
                num, year = a.split("/", 1)
                if len(year) == 2:
                    year = "20" + year
                clauses.append(
                    "(k.id_num = ? AND k.id_year = ?) "
                    "OR k.id LIKE ? OR k.id LIKE ? "
                    "OR LOWER(k.title_folded) LIKE ? "
                    "OR LOWER(k.title_folded) LIKE ?"
                )
                params.extend(
                    [
                        num,
                        year,
                        f"%{year}%;{num}",
                        f"%:{num}:{year}%",
                        f"%{a}%",
                        f"%n. {num}%",
                    ]
                )
            where = "(" + " OR ".join(clauses) + ")"
            order = _base_order()
        else:
            # 1) AND; se vuoto → OR (niente over-filter su topic libero)
            and_where, and_params = _phrase_where(terms, "and")
            test = _run_sk(
                con, and_where, and_params, _base_order(), extra_where, extra_params, 3
            )
            test = [r for r in test if r.get("id")]
            if test:
                where, params = and_where, and_params
                boost_terms = [t for t in terms if len(t) >= 5]
                if boost_terms:
                    boosts = [
                        "CASE WHEN LOWER(k.title_folded) LIKE ? THEN -1000 ELSE 0 END"
                        for _ in boost_terms
                    ]
                    order = ", ".join(boosts) + ", " + _base_order()
                    params = [*params, *[f"%{t}%" for t in boost_terms]]
                else:
                    order = _base_order()
            else:
                or_where, or_params = _phrase_where(terms, "or")
                where, params = or_where, or_params
                order = (
                    "CASE WHEN k.source = 'normativa' THEN 0 ELSE 1 END, "
                    + _base_order()
                )
                # OR può essere rumoroso: se source è costituzione, tieni solo nodi giurisprudenziali
                if source == "costituzione":
                    where = (
                        "(" + or_where + ") AND ("
                        "k.id LIKE 'sentenza:%' OR LOWER(k.title) LIKE '%ecli%'"
                        " OR UPPER(k.tipo) IN ('SENTENZA','ORDINANZA','PRONUNCIA')"
                        ")"
                    )

    # esegui intent principale
    out = _run_sk(con, where, params, order, extra_where, extra_params, limit)

    # fallback number → residual phrase
    if not out and fallthrough_phrase:
        fw, fp = _phrase_where(fallthrough_phrase, "or")
        out = _run_sk(con, fw, fp, _base_order(), extra_where, extra_params, limit)

    # fallback number → solo id_num senza anno (es. L.194/1978 mancante)
    if not out and kind == "number" and intent.get("year"):
        num = intent["num"]
        year = intent["year"]
        fw = (
            "(k.id_num = ? AND k.id_year = ?) OR k.id LIKE ? OR k.id LIKE ? "
            "OR LOWER(k.title_folded) LIKE ?"
        )
        fp = [num, year, f"%:{num}:{year}", f"%n. {num}%", f"%n. {num}%"]
        if intent.get("tipo_hints"):
            fw += " AND UPPER(k.tipo) IN ({})".format(
                ",".join("?" for _ in intent["tipo_hints"])
            )
            fp.extend(h.upper() for h in intent["tipo_hints"])
        out = _run_sk(con, fw, fp, _base_order(), extra_where, extra_params, limit)

    if kind == "cost" and intent.get("art") is None and out:
        out[0] = {
            **out[0],
            "note": (
                "Non esiste un nodo unico 'Costituzione': il documento è in "
                "nodes come costituzione:art:N. art. 1 per primo."
            ),
        }
    return out


@mcp.tool(
    name="legal_text",
    description=(
        "Testo integrale di un atto normativa da nodo/URN (italia-corpus, GitHub raw). "
        "Copre solo source=normativa. Altre source: errore strutturato."
    ),
    structured_output=True,
)
def legal_text(node_id: str, max_chars: int = 8000) -> dict[str, Any]:
    return guard_timed(_impl_legal_text, "legal_text", node_id, max_chars=max_chars)


def _impl_legal_text(node_id: str, max_chars: int = 8000) -> dict[str, Any]:
    _refresh_views(_get_con())
    node = resolve_text_node(node_id)
    if not node:
        return {"error": f"Nodo non trovato: {node_id}"}

    base = {
        "id": node["id"],
        "tipo": node["tipo"],
        "title": (node.get("title") or "")[:300],
        "data": node.get("data"),
        "anno": node.get("anno"),
        "source": node.get("source"),
        "collezione": node.get("collezione"),
        "source_filename": node.get("source_filename"),
    }

    source = node.get("source") or ""
    nid = node.get("id") or node_id
    # 1) mart texts: articoli Cost. + pronunce Corte
    if (
        source == "costituzione"
        or str(nid).startswith("costituzione:art:")
        or str(nid).startswith("sentenza:")
        or (node.get("tipo") or "").upper() in {"SENTENZA", "ORDINANZA", "PRONUNCIA", "COSTITUZIONE"}
        or str(nid).startswith("norma:")
    ):
        result = fetch_mart_text(nid, max_chars=max_chars)
        if "error" not in result:
            return {**base, **result}
        # nodi norma:* con filename IC → prova fetch normativa
        fn = node.get("source_filename") or ""
        coll = node.get("collezione") or ""
        if source != "normativa" and fn.endswith(".md") and coll:
            ic = fetch_normativa_text(collezione=coll, filename=fn, max_chars=max_chars)
            if "error" not in ic:
                return {**base, **ic}
        if source != "normativa":
            texts_ok = _view_exists(_get_con(), "texts")
            return {
                **base,
                "error": (
                    f"legal_text: source={source!r} non in mart_legal_texts"
                    f"{' (view presente ma nodo assente)' if texts_ok else ' (mart_legal_texts assente — make run)'}. "
                    "Coperti: normativa (IC) + articoli Cost. + pronunce Corte."
                ),
            }

    if source != "normativa":
        # nodi norma:* dalle massime: se hanno filename IC, prova lo stesso fetch
        fn = node.get("source_filename") or ""
        coll = node.get("collezione") or ""
        if fn.endswith(".md") and coll:
            result = fetch_normativa_text(
                collezione=coll, filename=fn, max_chars=max_chars
            )
            if "error" not in result:
                return {**base, **result}
        texts_ok = _view_exists(_get_con(), "texts")
        return {
            **base,
            "error": (
                f"legal_text: source={source!r} non in mart_legal_texts"
                f"{' (view presente ma nodo assente)' if texts_ok else ' (mart_legal_texts assente — make run)'}. "
                "Coperti: normativa (IC) + articoli Cost. + pronunce Corte. "
                "Nodi norma:* dalle massime hanno solo titolo/relazioni."
            ),
        }

    result = fetch_normativa_text(
        collezione=node.get("collezione") or "",
        filename=node.get("source_filename") or "",
        max_chars=max_chars,
    )
    return {**base, **result}


# ─── Tool 4: legal_query ─────────────────────────────────────────


@mcp.tool(
    name="legal_query",
    description=(
        "SQL sul grafo. Ammessi: SELECT, WITH, PRAGMA, DESCRIBE, SHOW, EXPLAIN. "
        "Tabelle: nodes, edges, metrics, search_keys, node_rel, emend_leg, texts, massime. "
        "URN con ';' in IN/LIKE: stringhe single-quote. Niente scritture."
    ),
    structured_output=True,
)
def legal_query(sql: str, limit: int = 50) -> list[dict[str, Any]]:
    return guard_timed(_impl_query, "legal_query", sql, limit=limit)


def _impl_query(sql: str, limit: int = 50) -> list[dict[str, Any]]:
    con = _get_con()
    limit = min(max(limit, 1), _MAX_ROWS)
    sql_clean = (sql or "").strip()
    first = sql_clean.upper().split(None, 1)[0] if sql_clean else ""
    allowed = {"SELECT", "WITH", "PRAGMA", "DESCRIBE", "SHOW", "EXPLAIN"}
    if first not in allowed:
        return [{"error": f"Comando non consentito: {first!r}. Usa SELECT/WITH/PRAGMA/DESCRIBE/SHOW/EXPLAIN."}]
    if first in {"SELECT", "WITH"} and "LIMIT" not in sql_clean.upper():
        sql_clean = f"{sql_clean.rstrip()} LIMIT {limit}"
    try:
        # alias comuni: PRAGMA table_info(nodes) / SHOW TABLES / DESCRIBE nodes
        if first == "PRAGMA":
            m = re.match(
                r"PRAGMA\s+(table_info|table_xinfo|database_list|tables)\s*\(\s*['\"]?(\w+)['\"]?\s*\)",
                sql_clean,
                re.IGNORECASE,
            )
            if m:
                pragma_fn, tbl = m.group(1).lower(), m.group(2)
                if pragma_fn in {"table_info", "table_xinfo"}:
                    sql_clean = f"PRAGMA {pragma_fn}('{tbl}')"
                else:
                    sql_clean = f"PRAGMA {pragma_fn}"
        if first in {"DESCRIBE", "SHOW"}:
            sql_clean = re.sub(r"\s+LIMIT\s+\d+\s*$", "", sql_clean, flags=re.IGNORECASE)
            # DESCRIBE nodes → DESCRIBE SELECT * FROM nodes (compatibile DuckDB)
            m = re.match(r"DESCRIBE\s+(?:TABLE\s+)?(\w+)\s*$", sql_clean, re.IGNORECASE)
            if m and m.group(1).upper() not in {"SELECT", "FROM"}:
                sql_clean = f"DESCRIBE (SELECT * FROM {m.group(1)} LIMIT {limit})"
        result = con.execute(sql_clean)
        if first in {"DESCRIBE", "SHOW", "PRAGMA"}:
            columns = [desc[0] for desc in (result.description or [])]
            rows = result.fetchall()[:limit]
            if not columns:
                return [
                    {
                        "ok": True,
                        "note": "comando meta senza tabella risultati",
                        "rows": len(rows),
                        "hint": "PRAGMA table_info(nodes) | DESCRIBE nodes | SHOW TABLES",
                    }
                ]
            out = []
            for row in rows:
                d = dict(zip(columns, row))
                out.append(
                    {
                        "column_name": d.get("column_name") or d.get("name"),
                        "column_type": d.get("column_type") or d.get("type"),
                        "nullable": d.get("null") if "null" in d else d.get("notnull"),
                        "key": d.get("key"),
                        "default": d.get("default") or d.get("dflt_value"),
                        "extra": d.get("extra"),
                    }
                )
            return out
        if result.description is None:
            try:
                raw = result.fetchall()
            except duckdb.Error:
                raw = []
            return [{"ok": True, "note": "eseguito senza tabella risultati", "rows": len(raw)}]
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()[:limit]
    except duckdb.Error as e:
        err = str(e)[:200]
        hint = ""
        if "Binder" in err or "Referenced table" in err:
            hint = "Controlla alias tabelle (nodes/edges) e nomi colonne."
        if "LIMIT" in err.upper() or "syntax" in err.lower():
            hint = "PRAGMA/DESCRIBE non accettano LIMIT; SELECT sì."
        return [{"error": err, "hint": hint} if hint else {"error": err}]
    if not rows and first == "SELECT":
        tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
        hint = "Verifica WHERE/relation; tabelle: nodes, edges[, temporal]."
        if "GROUP BY" in sql_clean.upper() and "edges" in sql_clean and "edges" not in tables:
            hint = "View 'edges' assente — riconnetti o make run."
        if "GROUP BY" in sql_clean.upper() and "edges" in tables:
            hint = (
                "GROUP BY su edges non dovrebbe essere vuoto: "
                "usa alias espliciti (es. COUNT(*) AS n) e LIMIT."
            )
        # ';' fuori dalle stringhe single-quote → rischio separatore DuckDB
        if " IN (" in sql_clean.upper() and ";" in re.sub(r"'[^']*'", "", sql_clean):
            hint = (
                "URN con ';' dentro IN: usa stringhe single-quote "
                "(es. id IN ('urn:...;24')). DuckDB tratta ; come separatore se non quotato."
            )
        if "metrics" in sql_clean and "id" in sql_clean and not _view_exists(con, "metrics"):
            hint = "View metrics assente — make run."
        return [
            {
                "note": "SELECT valida ma 0 righe",
                "sql": sql_clean[:200],
                "tables": sorted(tables),
                "hint": hint,
            }
        ]
    return [dict(zip(columns, row)) for row in rows]


# ─── Tool 5: legal_insights ──────────────────────────────────────


@mcp.tool(
    name="legal_insights",
    description=(
        "Report di sistema sul grafo. report: summary | critical | obsolete | complex | dormant. "
        "obsolete = candidati per età/anagrafica storica, NON lista abrogazioni formali. "
        "Richiede: python scripts/graph_intelligence.py"
    ),
    structured_output=True,
)
def legal_insights(report: str = "summary") -> dict[str, Any]:
    return guard_timed(_impl_insights, "legal_insights", report)


def _impl_insights(report: str = "summary") -> dict[str, Any]:
    con = _get_con()
    if not _view_exists(con, "metrics"):
        return {
            "error": (
                "Metriche non presenti nel mart. Esegui `make run` "
                "(mart_legal_node_metrics) oppure scripts/graph_intelligence.py."
            ),
            "hint": "Senza metrics, usa legal_query su edges per impatto grezzo.",
        }
    report_type = (report or "summary").lower().strip()

    if report_type == "critical":
        rows = con.execute(
            """
            SELECT id, title, referenced_by, impact_score, age_years
            FROM metrics WHERE impact_level = 'critical'
            ORDER BY referenced_by DESC LIMIT 20
            """
        ).fetchall()
        return {
            "report": "critical",
            "nodes": [
                {"id": r[0], "title": (r[1] or r[0])[:70], "refs": r[2], "score": r[3], "age": r[4]}
                for r in rows
            ],
        }

    if report_type == "obsolete":
        rows = con.execute(
            """
            SELECT id, title, age_years, referenced_by
            FROM metrics WHERE age_risk = 'obsolete_candidate'
            ORDER BY age_years DESC LIMIT 20
            """
        ).fetchall()
        return {
            "report": "obsolete",
            "note": "Segmento per age_years (candidati storici), non per abrogazione ufficiale.",
            "nodes": [
                {"id": r[0], "title": (r[1] or r[0])[:70], "age": r[2], "refs": r[3]} for r in rows
            ],
        }

    if report_type == "complex":
        rows = con.execute(
            """
            SELECT id, title, "references", referenced_by, age_years
            FROM metrics WHERE complexity_level IN ('very_complex', 'complex')
            ORDER BY "references" DESC LIMIT 20
            """
        ).fetchall()
        return {
            "report": "complex",
            "nodes": [
                {"id": r[0], "title": (r[1] or r[0])[:70], "deps": r[2], "refs": r[3], "age": r[4]}
                for r in rows
            ],
        }

    if report_type == "dormant":
        rows = con.execute(
            """
            SELECT id, title, last_referenced_year, referenced_by
            FROM metrics WHERE activity_level = 'dormant' AND referenced_by > 5
            ORDER BY referenced_by DESC LIMIT 20
            """
        ).fetchall()
        return {
            "report": "dormant",
            "nodes": [
                {"id": r[0], "title": (r[1] or r[0])[:70], "last_year": r[2], "refs": r[3]}
                for r in rows
            ],
        }

    stats = con.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN impact_level = 'critical' THEN 1 ELSE 0 END) AS critical,
            SUM(CASE WHEN impact_level = 'important' THEN 1 ELSE 0 END) AS important,
            SUM(CASE WHEN complexity_level = 'very_complex' THEN 1 ELSE 0 END) AS very_complex,
            SUM(CASE WHEN age_risk = 'obsolete_candidate' THEN 1 ELSE 0 END) AS obsolete,
            SUM(CASE WHEN activity_level = 'dormant' THEN 1 ELSE 0 END) AS dormant,
            ROUND(AVG(age_years), 1) AS avg_age
        FROM metrics
        """
    ).fetchone()
    critical = con.execute(
        """
        SELECT id, title, referenced_by FROM metrics
        WHERE impact_level = 'critical' ORDER BY referenced_by DESC LIMIT 5
        """
    ).fetchall()
    obsolete = con.execute(
        """
        SELECT id, title, age_years, referenced_by FROM metrics
        WHERE age_risk = 'obsolete_candidate' ORDER BY age_years DESC LIMIT 5
        """
    ).fetchall()
    return {
        "report": "summary",
        "summary": {
            "total_nodes": stats[0],
            "critical": stats[1],
            "important": stats[2],
            "very_complex": stats[3],
            "obsolete_candidates": stats[4],
            "dormant": stats[5],
            "avg_age_years": stats[6],
        },
        "top_critical": [
            {"id": r[0], "title": (r[1] or r[0])[:70], "refs": r[2]} for r in critical
        ],
        "top_obsolete": [
            {"id": r[0], "title": (r[1] or r[0])[:70], "age": r[2], "refs": r[3]} for r in obsolete
        ],
    }


# ─── main ────────────────────────────────────────────────────────


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
