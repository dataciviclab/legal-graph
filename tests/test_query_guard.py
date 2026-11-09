"""Guardia read-only: compatibilità tra versioni DuckDB.

La pipeline ha trovato il bug: con una versione di DuckDB che mappa
`PRAGMA table_info(...)` al tipo `PRAGMA` (invece di `SELECT`), la guardia
rifiutava un comando consentito. Il tipo ammesso dipende dal primo token, non
da una lista unica. Questi test non richiedono mart: esercitano la sola
funzione pura con i tipi che le diverse versioni possono restituire.
"""

from __future__ import annotations

import pytest

from legal_graph.mcp import reports


@pytest.mark.parametrize(
    ("first_type", "sql"),
    [
        ("PRAGMA", "PRAGMA table_info('nodes')"),
        ("PRAGMA", "PRAGMA database_list"),
        ("SELECT", "PRAGMA table_info('nodes')"),
        ("DESCRIBE", "DESCRIBE nodes"),
        ("SELECT", "DESCRIBE nodes"),
        ("SHOW", "SHOW TABLES"),
        ("SELECT", "SHOW TABLES"),
        ("EXPLAIN", "EXPLAIN SELECT 1"),
        ("SELECT", "EXPLAIN SELECT 1"),
    ],
)
def test_comandi_meta_ammessi_con_tipi_di_versioni_diverse(
    monkeypatch, first_type, sql
):
    monkeypatch.setattr(reports, "_statement_types", lambda _sql: [first_type])
    assert reports._readonly_error(sql) is None


@pytest.mark.parametrize(
    ("first_type", "sql"),
    [
        ("INSERT", "INSERT INTO nodes VALUES (1)"),
        ("DELETE", "DELETE FROM nodes"),
        ("CREATE", "CREATE TABLE x (a INT)"),
        ("SET", "SET threads=1"),
    ],
)
def test_scritture_rifiutate_qualunque_tipo(monkeypatch, first_type, sql):
    monkeypatch.setattr(reports, "_statement_types", lambda _sql: [first_type])
    err = reports._readonly_error(sql)
    assert err is not None


def test_pragma_di_scrittura_rifiutato_prima_del_tipo(monkeypatch):
    """`_PRAGMA_META` blocca i PRAGMA non-meta anche se il tipo è PRAGMA."""
    monkeypatch.setattr(reports, "_statement_types", lambda _sql: ["PRAGMA"])
    assert reports._readonly_error("PRAGMA threads=1") is not None
    assert reports._readonly_error("PRAGMA table_info('nodes')") is None
