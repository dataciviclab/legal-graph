"""Risoluzione id → riga nei mart: exact, alias noti, match parziale unico.

Contratto: un id parziale si risolve **solo se il candidato è unico**. Prima
ogni call site faceva `WHERE id LIKE '%x%' ORDER BY length(id) LIMIT 1`:
su 300 id troncati, 253 (84%) restituivano un atto diverso senza errore
(es. `senato:5907` → `senato:59077`). Un resolver che indovina è peggio di un
errore: chi legge non può accorgersi dello scambio.

`source` è un frammento SQL di FROM fornito dal chiamante (`nodes` per le view
MCP, `read_parquet('…')` per i resolver su file). Non passa mai input utente.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import duckdb

# Quanti candidati restituire in caso di ambiguità: bastano a capire cosa
# esiste, senza scaricare la lista completa (che può essere 100k righe).
_CANDIDATES = 5

_ECLI = re.compile(r"ecli|:\d{4}:\d+", re.IGNORECASE)
_SENTENZA = re.compile(r"sentenza:(\d{4})-(\d{1,4})", re.IGNORECASE)


@dataclass
class Resolved:
    row: tuple[Any, ...] | None = None
    candidates: list[str] = field(default_factory=list)
    # Come è stato risolto: l'id parziale è un'inferenza, e chi legge deve
    # poterla vedere (`matched_by` nelle risposte MCP).
    match: str = "none"

    @property
    def ambiguous(self) -> bool:
        return self.row is None and bool(self.candidates)


def _sentenza_pad(node_id: str) -> str | None:
    """`sentenza:2009-151` → `sentenza:2009-0151` (alias, non match parziale)."""
    m = _SENTENZA.fullmatch(node_id.strip())
    if not m:
        return None
    return f"sentenza:{m.group(1)}-{int(m.group(2)):04d}"


def resolve_row(
    con: duckdb.DuckDBPyConnection,
    *,
    source: str,
    cols: str,
    node_id: str,
    ecli_alias: bool = False,
) -> Resolved:
    """Risolvi `node_id` su `source`, con `cols` proiettate.

    Ordine: exact → alias sentenza (zero-pad) → alias ECLI nel titolo (solo se
    `ecli_alias`, richiede le colonne `source` e `title`) → match parziale se
    **unico**. In caso di ambiguità restituisce i candidati e nessuna riga.
    """
    nid = (node_id or "").strip()
    if not nid:
        return Resolved()

    row = con.execute(
        f"SELECT {cols} FROM {source} WHERE id = ? LIMIT 1", [nid]
    ).fetchone()
    if row is not None:
        return Resolved(row, match="exact")

    padded = _sentenza_pad(nid)
    if padded and padded != nid:
        row = con.execute(
            f"SELECT {cols} FROM {source} WHERE id = ? LIMIT 1", [padded]
        ).fetchone()
        if row is not None:
            return Resolved(row, match="alias")

    if ecli_alias and _ECLI.search(nid):
        rows = con.execute(
            f"SELECT {cols} FROM {source} "
            "WHERE source = 'costituzione' AND LOWER(title) LIKE ? "
            f"ORDER BY id LIMIT {_CANDIDATES + 1}",
            [f"%{nid.lower()}%"],
        ).fetchall()
        if len(rows) == 1:
            return Resolved(rows[0], match="alias")
        if len(rows) > 1:
            return Resolved(None, [str(r[0]) for r in rows], match="ambiguous")

    ids = [
        str(r[0])
        for r in con.execute(
            f"SELECT id FROM {source} WHERE id LIKE ? "
            f"ORDER BY length(id), id LIMIT {_CANDIDATES + 1}",
            [f"%{nid}%"],
        ).fetchall()
    ]
    if len(ids) == 1:
        row = con.execute(
            f"SELECT {cols} FROM {source} WHERE id = ? LIMIT 1", [ids[0]]
        ).fetchone()
        return Resolved(row, match="partial")
    if ids:
        return Resolved(None, ids, match="ambiguous")
    return Resolved()


def sql_quote(value: str) -> str:
    """Escape per interpolare un path in `read_parquet('…')` (solo valori interni)."""
    return value.replace("'", "''")
