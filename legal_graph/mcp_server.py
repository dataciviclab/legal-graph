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

Implementazione: legal_graph.mcp.{search,node_views,reports,textfold,db}.
Questa facade espone solo i 5 tool e il wiring FastMCP.
"""
from __future__ import annotations

from typing import Any

from lab_connectors.mcp import create_mcp_server, guard_timed

# Re-export per i test golden (import legal_graph.mcp_server as m)
from legal_graph.mcp import db as _db
from legal_graph.mcp.db import _get_con, _view_exists  # noqa: F401 — re-export test
from legal_graph.mcp.node_views import _impl_node
from legal_graph.mcp.reports import _impl_insights, _impl_legal_text, _impl_query
from legal_graph.mcp.search import _has_mart_search, _impl_search  # noqa: F401


def _reset_con() -> None:
    """Drop connessione DuckDB cachata (uso test: m._reset_con())."""
    _db.reset_con()


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


@mcp.tool(
    name="legal_search",
    description=(
        "Trova atti/nodi nel grafo. Multi-parola = AND; frase tra virgolette = LIKE intero. "
        "Numeri (es. '231', '231/2001', 'n. 231') → priorità a normativa. "
        "Accenti flessibili (responsabilità ~ responsabilita). "
        "Filtri: tipo, anno_min/anno_max, source, stato "
        "(vigente|abrogato|decaduto — status autorevole Normattiva, "
        "classeProvvedimento atto-level; `decaduto` = collezione DL decaduti), "
        "vigenza_fonte (normattiva|non_coperto|esclusione|atti_api — origine di "
        "`stato`; `esclusione` = vigente non verificato atto per atto, "
        "`atti_api` = atto fuori collezione IC), "
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
    vigenza_fonte: str = "",
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
        vigenza_fonte=vigenza_fonte,
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
        "Input: URN completo o id parziale. Un parziale si risolve solo se "
        "indica un unico nodo; altrimenti la risposta è 'id ambiguo' con i "
        "candidati (non viene scelto un atto a caso)."
    ),
    structured_output=True,
)
def legal_node(
    node_id: str,
    view: str = "overview",
    depth: int = 3,
) -> dict[str, Any]:
    return guard_timed(_impl_node, "legal_node", node_id, view=view, depth=depth)


@mcp.tool(
    name="legal_text",
    description=(
        "Testo di un atto normativa da nodo/URN (italia-corpus, GitHub raw). "
        "Copre normativa + articoli Cost. + pronunce Corte. "
        "`articolo=N` estrae il singolo articolo dagli heading AKN (per i codici "
        "il troncamento a max_chars non arriva agli articoli alti). "
        "Restituisce anche total_chars."
    ),
    structured_output=True,
)
def legal_text(
    node_id: str, max_chars: int = 8000, articolo: int | str = 0
) -> dict[str, Any]:
    return guard_timed(
        _impl_legal_text,
        "legal_text",
        node_id,
        max_chars=max_chars,
        articolo=articolo or None,
    )


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


@mcp.tool(
    name="legal_insights",
    description=(
        "Report di sistema sul grafo. report: summary | critical | obsolete | complex | dormant. "
        "obsolete = candidati per età/anagrafica storica, NON lista abrogazioni formali. "
        "Richiede: mart_legal_node_metrics (make run)."
    ),
    structured_output=True,
)
def legal_insights(report: str = "summary") -> dict[str, Any]:
    return guard_timed(_impl_insights, "legal_insights", report)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
