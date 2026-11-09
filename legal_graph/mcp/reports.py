"""Implementazioni di legal_text, legal_query e legal_insights."""
from __future__ import annotations

import re
from typing import Any

import duckdb

from legal_graph.legal_text import fetch_mart_text, fetch_normativa_text
from legal_graph.legal_text import resolve_node as resolve_text_node
from legal_graph.mcp import db
from legal_graph.mcp.db import _view_exists, db_lock, locked

_MAX_ROWS = 100

# --- Guardia read-only per legal_query -------------------------------------
# Non basta il primo token: DuckDB esegue più statement separati da ';' e
# funzioni come read_csv_auto/read_text leggono file locali. La guardia:
# 1) usa extract_statements (se disponibile) per rifiutare più statement e
#    i tipi non-SELECT/EXPLAIN;
# 2) rifiuta keyword di scrittura e funzioni di lettura file;
# 3) rifiuta FROM/JOIN con un path tra apici (DuckDB legge il file);
# 4) consente PRAGMA solo per metadati (table_info/table_xinfo/tables/database_list).
_READONLY_FIRST = {"SELECT", "WITH", "PRAGMA", "DESCRIBE", "SHOW", "EXPLAIN"}
_READONLY_TYPES = {"SELECT", "EXPLAIN"}
_FORBIDDEN_WORDS = (
    "insert", "update", "delete", "merge", "drop", "create", "alter", "copy",
    "attach", "detach", "install", "export", "import", "call", "vacuum",
    "checkpoint",
)
_FORBIDDEN_FUNC_RE = re.compile(
    r"\b(read_[a-z0-9_]+|[a-z0-9_]+_scan|glob|sniff_[a-z0-9_]+|load_extension|st_read)\s*\(",
    re.IGNORECASE,
)
_PRAGMA_META = re.compile(
    r"^PRAGMA\s+(table_info|table_xinfo|database_list|tables)\b",
    re.IGNORECASE,
)


def _strip_comments(sql: str) -> str:
    """Rimuove -- e /* */ fuori dalle stringhe, preservando il testo."""
    out: list[str] = []
    i, n = 0, len(sql)
    quote: str | None = None
    while i < n:
        ch = sql[i]
        if quote:
            out.append(ch)
            if ch == quote:
                if i + 1 < n and sql[i + 1] == quote:
                    out.append(sql[i + 1])
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            i += 2
            while i < n and sql[i] not in "\r\n":
                i += 1
            out.append(" ")
            continue
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i = i + 2 if i + 1 < n else n
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_literals(sql: str) -> str:
    """Sostituisce il contenuto delle stringhe con spazi (per lo scan keyword)."""
    out: list[str] = []
    i, n = 0, len(sql)
    quote: str | None = None
    while i < n:
        ch = sql[i]
        if quote:
            if ch == quote:
                if i + 1 < n and sql[i + 1] == quote:
                    out.append("  ")
                    i += 2
                    continue
                quote = None
                out.append(ch)
                i += 1
                continue
            out.append(" ")
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _has_semicolon_outside(sql: str) -> bool:
    return ";" in _strip_literals(_strip_comments(sql))


def _statement_types(sql: str) -> list[str] | None:
    """Tipi degli statement via DuckDB; None se l'API non è disponibile."""
    extract = getattr(duckdb, "extract_statements", None)
    if extract is None:
        return None
    out: list[str] = []
    for stmt in extract(sql):
        typ = getattr(stmt, "type", None)
        name = getattr(typ, "name", None) or str(typ).rsplit(".", 1)[-1]
        out.append(str(name).upper())
    return out


def _readonly_error(sql: str) -> str | None:
    """None se la query è read-only consentita; altrimenti messaggio d'errore."""
    clean = _strip_comments(sql).strip()
    if not clean:
        return "Query vuota."
    first = clean.split(None, 1)[0].upper()
    if first not in _READONLY_FIRST:
        return (
            f"Comando non consentito: {first!r}. "
            "Usa SELECT/WITH/PRAGMA/DESCRIBE/SHOW/EXPLAIN."
        )
    if first == "PRAGMA" and not _PRAGMA_META.match(clean):
        return (
            "PRAGMA non consentito: usa solo "
            "table_info, table_xinfo, database_list, tables."
        )
    try:
        types = _statement_types(sql)
    except duckdb.Error as exc:
        return f"SQL non valido: {str(exc)[:160]}"
    if types is not None:
        if len(types) != 1:
            return "Una sola istruzione per query."
        if types[0] not in _READONLY_TYPES:
            return f"Comando non consentito: {types[0]}. legal_query è read-only."
    elif _has_semicolon_outside(sql):
        return "Una sola istruzione per query."

    bare = _strip_literals(clean).lower()
    if first != "PRAGMA" and re.search(r"\bpragma\b", bare):
        return "PRAGMA non consentito in una query."
    for word in _FORBIDDEN_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", bare):
            return f"Comando non consentito: {word!r}."
    func = _FORBIDDEN_FUNC_RE.search(bare)
    if func:
        return f"Accesso a file non consentito: {func.group(1)}."
    if re.search(r"(?is)\b(from|join)\s+'[^']*(?:/|\\|\.(?:parquet|csv|json|ndjson|duckdb|db|txt|gz))[^']*'", clean):
        return "Accesso a file non consentito: FROM/JOIN con path tra apici."
    return None


def _impl_legal_text(
    node_id: str, max_chars: int = 8000, articolo: int | str | None = None
) -> dict[str, Any]:
    # solo le letture sul grafo stanno sotto lock: il fetch del testo è di rete
    with db_lock():
        found = resolve_text_node(node_id)
    if found.node is None:
        if found.candidates:
            return {
                "error": (
                    f"id ambiguo: {node_id!r} corrisponde a più nodi. "
                    "Passa l'id completo."
                ),
                "ambiguous": True,
                "candidates": found.candidates,
            }
        return {"error": f"Nodo non trovato: {node_id}"}
    node = found.node

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
    if found.match != "exact":
        # id parziale/alias: dichiara che l'id risolto è un'inferenza
        base["matched_by"] = found.match

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
            if articolo:
                result["note"] = (
                    "articolo non applicabile: nodo già a livello di articolo "
                    "(Costituzione) o pronuncia."
                )
            return {**base, **result}
        # nodi norma:* con filename IC → prova fetch normativa
        fn = node.get("source_filename") or ""
        coll = node.get("collezione") or ""
        if source != "normativa" and fn.endswith(".md") and coll:
            ic = fetch_normativa_text(
                collezione=coll, filename=fn, max_chars=max_chars, articolo=articolo
            )
            if "error" not in ic:
                return {**base, **ic}
        if source != "normativa":
            with db_lock():
                texts_ok = _view_exists(db._get_con(), "texts")
            return {
                **base,
                "error": (
                    f"legal_text: source={source!r} non in mart_legal_texts"
                    f"{' (view presente ma nodo assente)' if texts_ok else ' (mart_legal_texts assente — make run)'}. "
                    "Coperti: normativa (IC) + articoli Cost. + pronunce Corte."
                ),
            }

    if source != "normativa":
        # Direttive UE: nodo di identità, il testo resta su EUR-Lex.
        if source == "ue" or str(nid).startswith("celex:"):
            celex = str(nid).removeprefix("celex:")
            return {
                **base,
                "note": "Il testo della direttiva UE non è nel corpus: apri il link EUR-Lex.",
                "text_available": False,
                "eurlex_url": f"https://eur-lex.europa.eu/legal-content/IT/TXT/?uri=CELEX:{celex}",
                "eli_url": f"https://eur-lex.europa.eu/eli/dir/{celex[1:5]}/{int(celex[6:])}/oj",
                "hint": "Nodo di identità (direttiva UE); per il testo consolidato usa EUR-Lex.",
            }
        # nodi norma:* dalle massime: se hanno filename IC, prova lo stesso fetch
        fn = node.get("source_filename") or ""
        coll = node.get("collezione") or ""
        if fn.endswith(".md") and coll:
            result = fetch_normativa_text(
                collezione=coll,
                filename=fn,
                max_chars=max_chars,
                articolo=articolo,
            )
            if "error" not in result:
                return {**base, **result}
        with db_lock():
            texts_ok = _view_exists(db._get_con(), "texts")
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
        articolo=articolo,
    )
    return {**base, **result}



@locked
def _impl_query(sql: str, limit: int = 50) -> list[dict[str, Any]]:
    con = db._get_con()
    limit = min(max(limit, 1), _MAX_ROWS)
    sql_clean = (sql or "").strip().rstrip(";").strip()
    if not sql_clean:
        return [{"error": "Query vuota."}]
    invalid = _readonly_error(sql_clean)
    if invalid:
        return [{"error": invalid}]
    first = sql_clean.upper().split(None, 1)[0]
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
            # DESCRIBE nodes → DESCRIBE SELECT * FROM nodes (compatibile DuckDB)
            m = re.match(r"DESCRIBE\s+(?:TABLE\s+)?(\w+)\s*$", sql_clean, re.IGNORECASE)
            if m and m.group(1).upper() not in {"SELECT", "FROM"}:
                sql_clean = f"DESCRIBE (SELECT * FROM {m.group(1)} LIMIT {limit})"
        if first in {"SELECT", "WITH"}:
            # Wrap invece di appendere LIMIT: un LIMIT dentro una stringa/commento
            # non deve bypassare il cap, e un UNION/CTE non deve perdere il limite.
            sql_clean = f"SELECT * FROM ({sql_clean}) AS _lg_q LIMIT {limit}"
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
        hint = "Verifica WHERE/relation; tabelle: nodes, edges."
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
    con = db._get_con()
    if not _view_exists(con, "metrics"):
        return {
            "error": (
                "Metriche non presenti nel mart: esegui `make run` "
                "(mart_legal_node_metrics)."
            ),
            "hint": "Senza metrics, usa legal_query su edges per impatto grezzo.",
        }
    report_type = (report or "summary").lower().strip()

    if report_type == "critical":
        rows = con.execute(
            """
            SELECT id, title, referenced_by, impact_score, age_years
            FROM metrics WHERE impact_level = 'critical'
            ORDER BY referenced_by DESC, id LIMIT 20
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
            ORDER BY age_years DESC, id LIMIT 20
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
            ORDER BY "references" DESC, id LIMIT 20
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
            ORDER BY referenced_by DESC, id LIMIT 20
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
        WHERE impact_level = 'critical' ORDER BY referenced_by DESC, id LIMIT 5
        """
    ).fetchall()
    obsolete = con.execute(
        """
        SELECT id, title, age_years, referenced_by FROM metrics
        WHERE age_risk = 'obsolete_candidate' ORDER BY age_years DESC, id LIMIT 5
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

