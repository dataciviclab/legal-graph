"""Cache locale dei support esterni del compose.

Un `run-graph` legge 62 file da GCS — **525 MB di egress a ogni build**, locale
o in CI — perché gli SQL puntano agli URL. Questo script li scarica una volta
in `.cache/support/` e genera una config equivalente che li legge da disco:

    python scripts/cache_support.py            # scarica ciò che manca
    python scripts/cache_support.py --refresh  # riscarica tutto

La config generata (`.cache/dataset.local.yml`) è una copia di quella vera con
ogni support `external` riscritto come `external` + `uri` locale, mantenendo
`{year}` dove c'è: il toolkit espande `outputs` e `path` esattamente come per
gli URL. Non è versionata: è una cache.

La fonte di verità resta `compose/*/dataset.yml`. Se i support cambiano, si
rigenera — il file non va editato a mano.
"""

from __future__ import annotations

import argparse
import shutil
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache" / "support"
GCS_HOST = "storage.googleapis.com/"


def _rel(url: str) -> str:
    """`https://storage.googleapis.com/BUCKET/PATH` → `BUCKET/PATH`."""
    if GCS_HOST not in url:
        raise ValueError(f"support non GCS, cache non applicabile: {url}")
    return url.split(GCS_HOST, 1)[1]


def _scarica(url: str, dest: Path, refresh: bool) -> tuple[bool, int]:
    """(scaricato, byte). Non riscarica se il file c'è e `refresh=False`."""
    if dest.exists() and not refresh:
        return False, 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as out:
        shutil.copyfileobj(resp, out)
    tmp.replace(dest)
    return True, dest.stat().st_size


def main() -> int:
    from toolkit.core.config import load_config
    from toolkit.core.support import resolve_support_payloads

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="compose/legal-graph/dataset.yml")
    ap.add_argument("--out", default=".cache/dataset.local.yml")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument(
        "--only",
        default="",
        help="nomi separati da virgola: cacha solo questi (la config resta mista)",
    )
    args = ap.parse_args()

    import yaml

    cfg_path = Path(args.config)
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg = load_config(str(cfg_path))
    payloads = {
        p["name"]: p
        for p in resolve_support_payloads(cfg.support, require_exists=False, root=cfg.root)
    }

    solo = {x.strip() for x in args.only.split(",") if x.strip()}
    scaricati = riusati = 0
    byte = 0
    for entry in raw.get("support") or []:
        if str(entry.get("type")) != "external":
            continue
        if solo and str(entry["name"]) not in solo:
            continue
        payload = payloads[str(entry["name"])]
        urls = [u for u in (payload.get("outputs") or []) if u]
        for url in urls:
            ok, n = _scarica(url, CACHE / _rel(url), args.refresh)
            scaricati += ok
            riusati += not ok
            byte += n
        # `path` è il template (con {year} se multi-anno): stessa forma locale.
        template = payload.get("path") or urls[0]
        entry.pop("repo", None)
        entry.pop("slug", None)
        entry.pop("layer", None)
        entry.pop("table", None)
        entry["uri"] = str(CACHE / _rel(template))

    # La config vive in `.cache/`: il root relativo non vale più, va assoluto.
    root = Path(raw.get("root") or "out")
    raw["root"] = str((cfg_path.resolve().parent / root).resolve())

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")

    print(f"file scaricati: {scaricati} | riusati: {riusati} | egress: {byte / 1048576:.0f} MB")
    print(f"config locale: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
