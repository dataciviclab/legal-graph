"""Panoramica — il grafo in numeri (orientamento; salute su pagina dedicata)."""

import plotly.graph_objects as go
import streamlit as st
from lab_connectors.formatters import fmt_num
from sources import mart_sql

st.title("📊 Legal Graph — visione d'insieme")

try:
    kpis = mart_sql("""
        SELECT
            (SELECT COUNT(*) FROM nodes) AS n_nodi,
            (SELECT COUNT(*) FROM edges) AS n_archi,
            (SELECT COUNT(DISTINCT relation) FROM edges) AS n_relazioni,
            (SELECT COUNT(*) FROM nodes WHERE source = 'normativa') AS n_normativa
    """).iloc[0]
except Exception as e:  # noqa: BLE001 — rete/mart assente
    st.error(f"Mart non raggiungibile: {e}")
    st.stop()

k1, k2, k3, k4 = st.columns(4)
k1.metric("Nodi", fmt_num(kpis["n_nodi"]))
k2.metric("Relazioni (archi)", fmt_num(kpis["n_archi"]))
k3.metric("Tipi di relazione", fmt_num(kpis["n_relazioni"]))
k4.metric("Atti normativa", fmt_num(kpis["n_normativa"]))

st.divider()

col_l, col_r = st.columns([3, 2])

with col_l:
    st.subheader("Top relazioni per numero di archi")
    df_rel = mart_sql("""
        SELECT relation, COUNT(*) AS n
        FROM edges GROUP BY 1 ORDER BY n DESC LIMIT 12
    """)
    fig = go.Figure(go.Bar(x=df_rel["n"], y=df_rel["relation"], orientation="h"))
    fig.update_layout(height=380, margin={"t": 10, "b": 30}, yaxis={"autorange": "reversed"})
    st.plotly_chart(fig, width="stretch")

with col_r:
    st.subheader("Composizione nodi per sorgente")
    df_src = mart_sql("""
        SELECT source, COUNT(*) AS n
        FROM nodes GROUP BY 1 ORDER BY n DESC LIMIT 10
    """)
    fig = go.Figure(go.Pie(labels=df_src["source"], values=df_src["n"], hole=0.45))
    fig.update_layout(height=380, margin={"t": 10, "b": 30})
    st.plotly_chart(fig, width="stretch")

st.caption(
    "Disclaimer: dal mart legal-graph (non Normattiva live). "
    "Stato normativo = marker di corpus, non vigenza live. "
    "Per un atto singolo: pagina Esplora atto."
)
