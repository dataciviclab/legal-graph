"""Corte Costituzionale — sentenze, relatori e parametri (mart legal-graph)."""

import plotly.express as px
import streamlit as st
from sources import mart_sql

st.title("🏛️ Corte Costituzionale")
st.caption(
    "Sentenze, massime e giudici dal mart. `relatore_sentenza` copre il 100% "
    "delle sentenze; i testi restano su MCP (`legal_text`) / Normattiva."
)

try:
    kpi = mart_sql("""
        SELECT
            (SELECT COUNT(*) FROM nodes WHERE id LIKE 'sentenza:%') AS n_sent,
            (SELECT COUNT(*) FROM nodes WHERE id LIKE 'giudice:%') AS n_giudici,
            (SELECT COUNT(*) FROM edges WHERE relation = 'relatore_sentenza') AS n_relat,
            (SELECT COUNT(*) FROM edges WHERE relation = 'impugna') AS n_impugna
    """).iloc[0]
except Exception as e:  # noqa: BLE001
    st.error(f"Mart non raggiungibile: {e}")
    st.stop()

k1, k2, k3, k4 = st.columns(4)
k1.metric("Sentenze/ordinanze", f"{int(kpi['n_sent']):,}".replace(",", "."))
k2.metric("Giudici (anagrafica)", f"{int(kpi['n_giudici']):,}".replace(",", "."))
k3.metric("Archi relatore_sentenza", f"{int(kpi['n_relat']):,}".replace(",", "."))
k4.metric("Impugnazioni", f"{int(kpi['n_impugna']):,}".replace(",", "."))

st.divider()
col_l, col_r = st.columns(2)

with col_l:
    st.subheader("Pronunce per decennio")
    df_dec = mart_sql("""
        SELECT (CAST(anno AS INTEGER) / 10) * 10 AS decennio, COUNT(*) AS n
        FROM nodes WHERE id LIKE 'sentenza:%' AND anno IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)
    fig = px.bar(df_dec, x="decennio", y="n", color_discrete_sequence=["#6366f1"])
    fig.update_layout(height=340, margin={"t": 10, "b": 30}, bargap=0.15)
    st.plotly_chart(fig, width="stretch")

with col_r:
    st.subheader("Relatori più attivi")
    df_rel = mart_sql("""
        SELECT e.target_id AS giudice, n.title, COUNT(*) AS n_sentenze
        FROM edges e JOIN nodes n ON n.id = e.target_id
        WHERE e.relation = 'relatore_sentenza'
        GROUP BY 1, 2 ORDER BY n_sentenze DESC LIMIT 10
    """)
    fig = px.bar(
        df_rel.sort_values("n_sentenze"), x="n_sentenze", y="title",
        orientation="h", color_discrete_sequence=["#8b5cf6"],
    )
    fig.update_layout(height=340, margin={"t": 10, "b": 30}, yaxis_title="")
    st.plotly_chart(fig, width="stretch")

st.subheader("Parametri Cost. più invocati (dalle massime)")
df_par = mart_sql("""
    SELECT e.target_id AS articolo, n.title, COUNT(*) AS n
    FROM edges e JOIN nodes n ON n.id = e.target_id
    WHERE e.relation = 'invoca_parametro'
    GROUP BY 1, 2 ORDER BY n DESC LIMIT 15
""")
fig = px.bar(df_par, x="n", y="title", orientation="h",
             color_discrete_sequence=["#0ea5e9"])
fig.update_layout(height=420, margin={"t": 10, "b": 30}, yaxis_title="",
                  yaxis={"categoryorder": "total ascending"})
st.plotly_chart(fig, width="stretch")

with st.expander("Ultime pronunce con relatore"):
    df_ult = mart_sql("""
        SELECT s.id, s.title AS ecli, s.anno, g.title AS relatore
        FROM nodes s
        LEFT JOIN edges e ON e.source_id = s.id AND e.relation = 'relatore_sentenza'
        LEFT JOIN nodes g ON g.id = e.target_id
        WHERE s.id LIKE 'sentenza:%'
        ORDER BY s.anno DESC NULLS LAST LIMIT 25
    """)
    st.dataframe(df_ult, hide_index=True, width="stretch")

st.caption("Disclaimer: dal mart legal-graph (non Normattiva live).")
