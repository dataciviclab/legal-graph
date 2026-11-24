"""Freschezza degli input del compose: quando è stato prodotto ogni support.

Il grafo dichiara `build_at` (quando è stato costruito) ma non l'età dei dati
che ha letto: il 24/11, 1.112.525 archi su 1.411.670 (79%) poggiavano su un
support più vecchio di 10 giorni, con `senato_firmatari` fermo a 57.

Questo modulo legge il `metadata.json` accanto a ogni parquet risolto dal
compose e scrive `mart_legal_freshness.parquet`: una riga per support, con
`as_of`, `age_days` e la provenienza del dato. È **metadato sulla filiera**,
non un dato del grafo: non entra nel compose SQL e non aggiunge archi.

Soglia: piatta (>14 giorni). La cadenza per fonte non è derivabile dai
metadati (57 giorni è rotto per un quindicinale, normale per un mensile), e
inventarla per 35 support sarebbe peggio che dichiarare una soglia sola.

Uso: python -m legal_graph.freshness --config compose/legal-graph/dataset.yml
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen

SOGLIA_GIORNI = 14
NOME_FILE = "mart_legal_freshness.parquet"

_FILENAME_RE = re.compile(r"[^/]+$")


def _as_of(parquet: str) -> datetime | None:
    """`timestamp_utc` del metadata accanto al parquet, o None."""
    meta = _FILENAME_RE.sub("metadata.json", str(parquet))
    if meta.startswith(("http://", "https://")):
        try:
            with urlopen(meta, timeout=10) as resp:
                payload = json.load(resp)
        except (OSError, ValueError):  # rete, timeout, JSON malformato
            return None
    else:
        path = Path(meta)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    ts = payload.get("timestamp_utc")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _anni(entry: dict[str, Any]) -> str:
    years = [int(y) for y in (entry.get("years") or [])]
    if not years:
        return ""
    return str(years[0]) if len(years) == 1 else f"{min(years)}-{max(years)}"


def collect(config: str | Path) -> list[tuple]:
    """Una riga per support del compose: (support, repo, slug, layer, anni, as_of, age_days, fonte, nota).

    Richiede il toolkit (extra `pipeline`): risolve i support esattamente come
    il build, così la tabella descrive gli stessi artifact che il grafo legge.
    """
    from toolkit.core.config import load_config
    from toolkit.core.support import resolve_support_payloads

    cfg = load_config(str(config))
    payloads = {p["name"]: p for p in resolve_support_payloads(cfg.support, require_exists=False, root=cfg.root)}
    now = datetime.now(UTC)
    rows: list[tuple] = []
    for entry in cfg.support or []:
        name = str(entry.get("name"))
        payload = payloads.get(name, {})
        files = [p for p in (payload.get("outputs") or []) if p]
        if not files:
            files = [v for v in (payload.get("mart_by_table") or {}).values() if v]
        dates = [d for d in (_as_of(f) for f in files) if d is not None]
        oldest = min(dates) if dates else None
        age = (now - oldest).days if oldest else None
        rows.append(
            (
                name,
                str(entry.get("repo") or ""),
                str(entry.get("slug") or ""),
                str(entry.get("layer") or ""),
                _anni(entry),
                oldest,
                age,
                "metadata_json" if dates else "assente",
                "" if dates else "nessun metadata.json accanto al parquet",
            )
        )
    return rows


def write(rows: list[tuple], out: Path) -> None:
    import duckdb

    out.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE f (
            support VARCHAR, repo VARCHAR, slug VARCHAR, layer VARCHAR, anni VARCHAR,
            as_of TIMESTAMP, age_days INTEGER, fonte VARCHAR, nota VARCHAR
        )
        """
    )
    if rows:
        con.executemany("INSERT INTO f VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    con.execute(f"COPY f TO '{out}' (FORMAT PARQUET)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="compose/legal-graph/dataset.yml")
    parser.add_argument("--out", default=f"out/data/mart/legal_graph/2026/{NOME_FILE}")
    args = parser.parse_args(argv)

    rows = collect(args.config)
    write(rows, Path(args.out))
    oltre = [r for r in rows if r[6] is not None and r[6] > SOGLIA_GIORNI]
    print(f"{len(rows)} support · {len(oltre)} oltre {SOGLIA_GIORNI} giorni")
    for r in sorted(rows, key=lambda r: -(r[6] or -1))[:5]:
        print(f"  {r[6] if r[6] is not None else 'n/d':>4} gg  {r[0]}")
    print(f"scritto: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
