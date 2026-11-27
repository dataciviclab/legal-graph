"""Path resolution per legal-graph.

Priorità sorgente mart (tabelle del compose):
1. Variabile d'ambiente LEGAL_GRAPH_MART_DIR (override locale/CI)
2. compose toolkit locale: out/data/mart/legal_graph/<year>/
3. GCS pubblico: via lab_connectors.gcs.paths (path contract Lab)

`LEGAL_GRAPH_SOURCE` sceglie la sorgente in modo esplicito:
- `auto` (default): locale se esiste, altrimenti GCS (comportamento storico)
- `local`: solo mart locali (nessun fallback: se manca, errore a valle)
- `gcs`: solo GCS, anche se un build locale è presente (evita di servire
  dati vecchi quando il publish è più fresco)

Il contract di publish copre 14 tabelle su tre dataset:
- `legal_graph`: 10 tabelle del grafo (nodes, edges, entity_alias,
  relation_type, edges_in, coverage, metrics, search_keys, node_rel,
  emend_leg) + `freshness` prodotta dal repo;
- `legal_texts`: `texts` e `massime` (payload di testo, picco di memoria);
- `legal_index`: `search_terms` (indice BM25).
Solo mart compose (locale o GCS): nessun fallback su data/*.parquet.
"""
from __future__ import annotations

import os
from pathlib import Path

from lab_connectors.gcs.paths import https_url

PathLike = Path | str

REPO_ROOT = Path(__file__).resolve().parent.parent
YEAR = "2026"

DEFAULT_MART_DIR = REPO_ROOT / "out" / "data" / "mart" / "legal_graph" / YEAR
# L'indice lessicale vive nel dataset fratello `legal_index` (compose/legal-index/):
# isolato per memoria, non per semantica. Stesso prefix GCS, slug diverso.
DEFAULT_INDEX_DIR = REPO_ROOT / "out" / "data" / "mart" / "legal_index" / YEAR
# Testi e massime vivono in `legal_texts` (compose/legal-texts/): payload di
# testo e picco di memoria isolati dal grafo, nessuna dipendenza da nodi/archi.
DEFAULT_TEXT_DIR = REPO_ROOT / "out" / "data" / "mart" / "legal_texts" / YEAR

SOURCE_ENV = "LEGAL_GRAPH_SOURCE"
SOURCE_MODES = ("auto", "local", "gcs")

# Tabelle pubblicate dal dataset `legal_index` invece che da `legal_graph`.
INDEX_TABLES = frozenset({"mart_legal_search_terms"})
# Tabelle pubblicate dal dataset `legal_texts` invece che da `legal_graph`.
TEXT_TABLES = frozenset({"mart_legal_texts", "mart_legal_massime"})

# Tabelle coperte dal publish GCS (dal 2026-11: anche texts/massime)
GCS_MART_TABLES = frozenset(
    {
        "mart_legal_nodes",
        "mart_legal_edges",
        "mart_legal_edges_in",
        "mart_legal_entity_alias",
        "mart_legal_relation_type",
        "mart_legal_coverage",
        "mart_legal_node_metrics",
        "mart_legal_search_keys",
        "mart_legal_search_terms",
        "mart_legal_node_rel",
        "mart_legal_emend_leg",
        "mart_legal_texts",
        "mart_legal_massime",
    }
)


def mart_dir() -> Path:
    """Directory locale dei mart compose (env override inclusa)."""
    env = os.environ.get("LEGAL_GRAPH_MART_DIR")
    if env:
        return Path(env)
    return DEFAULT_MART_DIR


def index_dir() -> Path:
    """Directory locale dell'indice lessicale (dataset `legal_index`)."""
    env = os.environ.get("LEGAL_GRAPH_INDEX_DIR")
    if env:
        return Path(env)
    return DEFAULT_INDEX_DIR


def text_dir() -> Path:
    """Directory locale di testi e massime (dataset `legal_texts`)."""
    env = os.environ.get("LEGAL_GRAPH_TEXT_DIR")
    if env:
        return Path(env)
    return DEFAULT_TEXT_DIR


def dataset_of(table: str) -> str:
    """Dataset che pubblica la tabella: graph, texts o index."""
    if table in INDEX_TABLES:
        return "legal_index"
    if table in TEXT_TABLES:
        return "legal_texts"
    return "legal_graph"


def table_dir(table: str) -> Path:
    """Directory locale della tabella, nel dataset che la produce."""
    if table in INDEX_TABLES:
        return index_dir()
    if table in TEXT_TABLES:
        return text_dir()
    return mart_dir()


def source_mode() -> str:
    """Sorgente richiesta: `auto` | `local` | `gcs` (default `auto`)."""
    raw = os.environ.get(SOURCE_ENV, "").strip().lower()
    if not raw:
        return "auto"
    if raw not in SOURCE_MODES:
        raise ValueError(
            f"{SOURCE_ENV}={raw!r} non valido (attesi: {', '.join(SOURCE_MODES)})"
        )
    return raw


def gcs_url(table: str) -> str:
    """URL HTTPS pubblico via path contract Lab (bucket mart)."""
    if table not in GCS_MART_TABLES:
        raise ValueError(f"tabella non pubblicata su GCS: {table}")
    return https_url(
        "mart",
        "mart_parquet",
        prefix="legal-graph/",
        slug=dataset_of(table),
        year=int(YEAR),
        table=table,
    )


def resolve(table: str, *, allow_gcs: bool = True) -> PathLike | None:
    """Risolvi una tabella mart secondo `LEGAL_GRAPH_SOURCE`.

    `auto`: locale se presente, altrimenti GCS.
    `local`: solo locale (None se assente, nessun fallback remoto).
    `gcs`: solo GCS (None se la tabella non è pubblicata o `allow_gcs=False`).
    """
    mode = source_mode()
    local = table_dir(table) / f"{table}.parquet"
    if mode in ("auto", "local") and local.exists():
        return local
    if mode in ("auto", "gcs") and allow_gcs and table in GCS_MART_TABLES:
        return gcs_url(table)
    return None


def _metadata_url(table: str) -> str:
    """`metadata.json` accanto ai parquet di `table`, su GCS."""
    return gcs_url(table).rsplit("/", 1)[0] + "/metadata.json"


def resolve_metadata_file() -> PathLike | None:
    """`metadata.json` del publish del grafo: elenca le tabelle caricate.

    Locale se c'è (accanto ai mart), altrimenti accanto ai parquet su GCS.
    `None` se la modalità non ammette quella sorgente.
    """
    mode = source_mode()
    local = mart_dir() / "metadata.json"
    if mode in ("auto", "local") and local.exists():
        return local
    if mode in ("auto", "gcs"):
        # stessa directory dei parquet: lo scrive il publish accanto a loro
        return _metadata_url("mart_legal_nodes")
    return None


def resolve_index_metadata_file() -> PathLike | None:
    """`metadata.json` di `legal_index`.

    `search_terms` vive nel dataset fratello: il suo metadata non è quello del
    grafo, e senza questo la tabella risulta "non pubblicata" e la view non
    viene creata (regressione 2026-11-24).
    """
    mode = source_mode()
    local = index_dir() / "metadata.json"
    if mode in ("auto", "local") and local.exists():
        return local
    if mode in ("auto", "gcs"):
        return _metadata_url("mart_legal_search_terms")
    return None


def resolve_texts_metadata_file() -> PathLike | None:
    """`metadata.json` di `legal_texts`.

    Testi e massime vivono nel dataset fratello: il gate delle tabelle
    pubblicate va applicato al loro publish, non a quello del grafo.
    """
    mode = source_mode()
    local = text_dir() / "metadata.json"
    if mode in ("auto", "local") and local.exists():
        return local
    if mode in ("auto", "gcs"):
        return _metadata_url("mart_legal_texts")
    return None


def resolve_freshness_file() -> PathLike | None:
    """Freschezza degli input (`mart_legal_freshness`), se pubblicata.

    Prodotta da `legal_graph.freshness`, non dal compose SQL: è metadato sulla
    filiera. Vive nella directory dei mart del grafo.
    """
    mode = source_mode()
    local = mart_dir() / "mart_legal_freshness.parquet"
    if mode in ("auto", "local") and local.exists():
        return local
    if mode in ("auto", "gcs"):
        return gcs_url("mart_legal_nodes").rsplit("/", 1)[0] + "/mart_legal_freshness.parquet"
    return None


def is_remote(source: PathLike | None) -> bool:
    return isinstance(source, str) and source.startswith(("http://", "https://"))


def source_exists(source: PathLike | None) -> bool:
    """True se la sorgente è leggibile (file locale o URL remote)."""
    if source is None:
        return False
    if is_remote(source):
        return True
    return Path(source).exists()


# ── Resolver mart (compat test / MCP) ──────────────────────────────


def resolve_nodes_file() -> PathLike | None:
    """Parquet dei nodi: solo mart compose (locale o GCS)."""
    return resolve("mart_legal_nodes")


def resolve_edges_file() -> PathLike | None:
    """Parquet degli archi: solo mart compose (locale o GCS)."""
    return resolve("mart_legal_edges")


def resolve_edges_in_file() -> PathLike | None:
    """Archi ordinati per `target_id` (query in entrata); None se non pubblicato."""
    return resolve("mart_legal_edges_in")


def resolve_entity_alias_file() -> PathLike | None:
    """Alias di identità (contratto `entity_alias`)."""
    return resolve("mart_legal_entity_alias")


def resolve_relation_type_file() -> PathLike | None:
    """Vocabolario relazioni (contratto `relation_type`)."""
    return resolve("mart_legal_relation_type")


def resolve_metrics_file() -> PathLike | None:
    """Parquet delle metriche: solo mart compose (locale o GCS)."""
    return resolve("mart_legal_node_metrics")


def resolve_coverage_file() -> PathLike | None:
    """Bilancio di copertura delle relazioni (mart compose, locale o GCS)."""
    return resolve("mart_legal_coverage")


def resolve_search_keys_file() -> PathLike | None:
    return resolve("mart_legal_search_keys")


def resolve_search_terms_file() -> PathLike | None:
    """Indice lessicale full-text (BM25) per `legal_search`."""
    return resolve("mart_legal_search_terms")


def resolve_node_rel_file() -> PathLike | None:
    return resolve("mart_legal_node_rel")


def resolve_emend_leg_file() -> PathLike | None:
    return resolve("mart_legal_emend_leg")


def resolve_texts_file() -> PathLike | None:
    return resolve("mart_legal_texts")


def resolve_massime_file() -> PathLike | None:
    return resolve("mart_legal_massime")
