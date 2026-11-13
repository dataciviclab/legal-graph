"""Guardia sul costo di build: il testo pieno non entra nel compose.

Perché serve un test sul **testo** del SQL: sostituire `text_preview` con
`text_integrale` produce lo stesso mart — la preview è il prefisso del testo —
quindi nessuna asserzione sui dati se ne accorgerebbe. Il costo però raddoppia:
157,5 MB letti due volte (nodi emendamento + archi `emendamento`) per 100
caratteri di evidenza, più 81,5 MB per i titoli degli interventi. È una
regressione che non ha segnale nell'output, solo nel tempo di build.

Il contratto opposto — l'evidenza resta un'anteprima — si verifica sui dati:
senza il `LEFT(..., 100)` il mart esplode in dimensione (oggi `evidence` è il
71% dei 17,5 MB di `mart_legal_edges`).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from legal_graph.paths import resolve_texts_file
from legal_graph.resolve import sql_quote

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"

# Colonne del clean `senato-akn` che costano 157,5 MB e 81,5 MB: il compose
# deve leggere `text_preview`, non queste.
_TESTI_PIENI = ("text_integrale", "text_intervento")


def _codice(sql: str) -> str:
    """SQL senza commenti `-- …`: i commenti possono nominare le colonne."""
    return "\n".join(
        re.sub(r"--.*$", "", riga) for riga in sql.splitlines()
    )


@pytest.mark.parametrize("file_sql", ["mart_legal_nodes.sql", "mart_legal_edges.sql"])
def test_compose_non_legge_il_testo_pieno(file_sql: str) -> None:
    sql = _codice((SQL_DIR / file_sql).read_text(encoding="utf-8"))
    trovate = [col for col in _TESTI_PIENI if col in sql]
    assert not trovate, (
        f"{file_sql} legge {trovate}: usa `text_preview` (clean senato-akn). "
        "L'output è identico ma il build rilegge 240 MB di testo per 100 char."
    )


def test_archi_emendamento_usano_la_preview(con) -> None:
    """L'evidenza degli edge resta ≤100 char (non il testo da 240)."""
    totale = con.execute(
        "SELECT COUNT(*) FROM edges WHERE relation = 'emendamento'"
    ).fetchone()[0]
    assert totale > 1_000, f"solo {totale} archi emendamento: test vacuo"

    lunghi = con.execute(
        """
        SELECT COUNT(*) FROM edges
        WHERE relation = 'emendamento' AND LENGTH(evidence) > 100
        """
    ).fetchone()[0]
    assert lunghi == 0, f"{lunghi} archi emendamento con evidenza > 100 char"


def test_titoli_di_processo_restano_anteprime(con) -> None:
    """Titoli di emendamenti e interventi: anteprima, non testo integrale."""
    totale = con.execute(
        """
        SELECT COUNT(*) FROM nodes
        WHERE source IN ('senato_emend', 'senato_dib')
        """
    ).fetchone()[0]
    assert totale > 10_000, f"solo {totale} nodi di processo: test vacuo"

    lunghi = con.execute(
        """
        SELECT COUNT(*) FROM nodes
        WHERE source IN ('senato_emend', 'senato_dib') AND LENGTH(title) > 100
        """
    ).fetchone()[0]
    assert lunghi == 0, f"{lunghi} titoli > 100 char"


def test_texts_ha_row_group_piccoli_per_il_lookup(con) -> None:
    """`mart_legal_texts` è letto solo per `id`: un row group grande costa caro.

    Il parquet è l'unità di lettura di DuckDB: con UN row group (il default,
    122.880 righe) un `legal_text` da GCS legge 77 MB — il file intero — per
    una riga. `row_group_size: 2048` in dataset.yml lo spezza in 12 gruppi e
    il lookup scende a ~6 MB. Se il campo sparisce, la tabella resta corretta
    e il file torna monolitico: nessun test sui dati se ne accorgerebbe.
    """
    path = sql_quote(str(resolve_texts_file()))
    righe_max = con.execute(
        f"SELECT MAX(row_group_num_rows) FROM parquet_metadata('{path}')"
    ).fetchone()[0]
    gruppi = con.execute(
        f"SELECT COUNT(DISTINCT row_group_id) FROM parquet_metadata('{path}')"
    ).fetchone()[0]

    assert righe_max is not None, "mart_legal_texts senza row group"
    assert righe_max <= 2048, (
        f"row group da {righe_max} righe: un lookup per id legge troppo. "
        "Imposta mart.tables[mart_legal_texts].row_group_size: 2048"
    )
    assert gruppi > 1, f"{gruppi} row group: la tabella sta in un blocco solo"
