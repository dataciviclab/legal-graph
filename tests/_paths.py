"""Path dei contratti letti dai test.

Alcuni test leggono il **testo** degli SQL del compose (costo del build,
locator, formattazione date): un refactor di layout non deve costringere a
toccare ogni modulo. Da qui, un solo posto da aggiornare.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_DIR = REPO_ROOT / "compose"
GRAPH_SQL_DIR = COMPOSE_DIR / "legal-graph" / "sql"
INDEX_SQL_DIR = COMPOSE_DIR / "legal-index" / "sql"
