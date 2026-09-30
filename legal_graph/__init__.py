"""Legal Graph — package del compose + MCP.

Percorso critico (MCP + test):
  - mcp_server.py   5 tool: search, node, text, query, insights
  - paths.py
  - legal_text.py

CLI in scripts/ (non nel package):
  - scripts/graph_intelligence.py   → metriche per legal_insights
  - scripts/eu_enrichment.py        → EUR-Lex (sperimentale)
  - scripts/temporal_enrichment.py  → archi temporali (sperimentale)
"""

from __future__ import annotations

__version__ = "0.3.0"

__all__ = ["__version__"]
