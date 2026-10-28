"""Implementazioni di legal_text, legal_query e legal_insights."""
from __future__ import annotations

import re
from typing import Any

import duckdb

from legal_graph.legal_text import fetch_mart_text, fetch_normativa_text
from legal_graph.legal_text import resolve_node as resolve_text_node
from legal_graph.mcp.db import _get_con, _refresh_views, _view_exists, db_lock, locked

_MAX_ROWS = 100

def _impl_legal_text(node_id: str, max_chars: int = 8000) -> dict[str, Any]:
    # solo le letture sul grafo stanno sotto lock: il fetch del testo è di rete
    with db_lock():
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
            with db_lock():
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
        with db_lock():
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



@locked
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



@locked
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

