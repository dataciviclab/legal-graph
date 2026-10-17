"""Intent parsing e _impl_search su mart search_keys."""
from __future__ import annotations

import re
from typing import Any

import duckdb

from legal_graph.mcp.db import _get_con, _refresh_views, _view_exists
from legal_graph.mcp.textfold import (
    _EN_IT_GLOSSARY,
    _STOP,
    _fold,
    _parse_costituzione,
    _parse_date_query,
    _parse_ecli_or_sentenza,
    _stem_prefix,
)

_MAX_ROWS = 100
_TITLE_SEARCH = 280

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
    """mode: 'and' | 'or' — costruisce WHERE su title_folded.

    Nota: search_text è stata rimossa dal mart (ottimizzazione 2026-10).
    La ricerca su numero/tipo/collezione avviene tramite le colonne
    dedicate (id_num, tipo, collezione) nei filtri del chiamante.
    """
    if mode == "or":
        clauses = []
        params: list[Any] = []
        for term in terms:
            stem = _stem_prefix(term)
            if stem != term:
                clauses.append(
                    "(LOWER(k.title_folded) LIKE ? OR LOWER(k.title_folded) LIKE ?)"
                )
                params.extend([f"%{stem}%", f"%{term}%"])
            else:
                clauses.append("LOWER(k.title_folded) LIKE ?")
                params.append(f"%{term}%")
        return "(" + " OR ".join(clauses) + ")", params
    clauses = []
    params = []
    for term in terms:
        stem = _stem_prefix(term)
        if stem != term:
            clauses.append(
                "(LOWER(k.title_folded) LIKE ? OR LOWER(k.title_folded) LIKE ?)"
            )
            params.extend([f"%{stem}%", f"%{term}%"])
        else:
            clauses.append("LOWER(k.title_folded) LIKE ?")
            params.append(f"%{term}%")
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

    # codici non navigabili (COVERAGE: c.c./c.p. fuori grafo) → errore
    # strutturato, mai atti a caso. "codice del terzo settore" ecc. restano
    # cercabili: il pattern cattura solo i codici sistematici.
    if re.search(
        r"codice\s+(?:del\s+)?(?:civile|penale|procedura\s+civile|procedura\s+penale)\b",
        folded,
    ):
        return [
            {
                "error": (
                    "Fuori copertura: codici sistematici (c.c., c.p., c.p.c., "
                    "c.p.p.) non navigabili nel grafo — vedi docs/COVERAGE.md. "
                    "Cerca l'atto che li modifica (es. 'decreto legislativo "
                    "N/AAAA') o usa legal_query sui nodi normativa."
                )
            }
        ]

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
