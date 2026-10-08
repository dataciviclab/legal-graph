"""Query SQL — escape hatch sulle 6 tabelle del mart."""

import streamlit as st
from sources import MART_TABLES, mart_sql

st.title("🧪 Query SQL")
st.markdown(
    "Interroga direttamente il mart. **Viste disponibili**: "
    + " · ".join(f"`{t.removeprefix('mart_legal_')}`" for t in MART_TABLES)
    + " (schema: `nodes`, `edges`, `metrics`, `search_keys`, `node_rel`, `emend_leg`)."
)

DEFAULT_SQL = """
SELECT relation, COUNT(*) AS n
FROM edges
GROUP BY 1
ORDER BY n DESC
LIMIT 10
""".strip()

sql = st.text_area("SQL", value=DEFAULT_SQL, height=180)

if st.button("Esegui", type="primary"):
    stripped = sql.strip().upper()
    if not stripped.startswith(("SELECT", "WITH", "DESCRIBE", "PRAGMA", "SHOW")):
        st.error("Solo SELECT/WITH/DESCRIBE/PRAGMA/SHOW — niente scritture.")
        st.stop()
    try:
        df = mart_sql(sql)
        st.dataframe(df.head(500), width="stretch")
        st.caption(f"{len(df)} righe (cap 500 in vista; esporta dal CSV se serve).")
        st.download_button(
            "⬇️ CSV",
            df.to_csv(index=False).encode("utf-8"),
            file_name="query_result.csv",
            mime="text/csv",
        )
    except Exception as e:  # noqa: BLE001
        st.error(f"Errore: {e}")

st.caption("Dati: gs://dataciviclab-mart/legal-graph/ — disclaimer: non Normattiva live.")
