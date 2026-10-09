"""Decretazione d'urgenza — 30 anni di DL dal lato Senato (Analisi #8)."""

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from lab_connectors.formatters import fmt_num
from sources import LEG_CASE, decreti_sql

st.title("⚡ Decretazione d'urgenza — DL dal lato conversione (Leg13-19)")
st.caption(
    "Sorgente: `open-politica/decreti_legge` (compose multi-leg, GCS). "
    "⚠️ Solo lato Senato; esiti da marker iter, non da Gazzetta Ufficiale."
)

try:
    kpi = decreti_sql("""
        SELECT COUNT(*) AS n,
               COUNT(*) FILTER (WHERE esito = 'convertito') AS conv,
               ROUND(100.0 * COUNT(*) FILTER (WHERE esito = 'convertito') / COUNT(*), 1) AS pct_conv,
               ROUND(MEDIAN(giorni_conversione) FILTER (WHERE esito = 'convertito'), 0) AS mediana,
               COUNT(*) FILTER (WHERE esito = 'decaduto') AS dec
        FROM dl
    """).iloc[0]
except Exception as e:  # noqa: BLE001
    st.error(f"Dataset decreti_legge non raggiungibile: {e}")
    st.stop()

k1, k2, k3, k4 = st.columns(4)
k1.metric("DL distinti (1996-2026)", fmt_num(kpi["n"]))
k2.metric("Tasso conversione", f"{kpi['pct_conv']}%", help="Esito = nostro marker da iter Senato")
k3.metric("Mediana giorni", fmt_num(kpi["mediana"]), help="Conversione: solo convertiti")
k4.metric("Decaduti", fmt_num(kpi["dec"]))

st.divider()
col_l, col_r = st.columns([2, 1])

with col_l:
    st.subheader("DL per legislatura ed esito")
    df_leg = decreti_sql(f"""
        SELECT {LEG_CASE} AS leg, esito, COUNT(*) AS n
        FROM dl GROUP BY 1, 2 ORDER BY MIN(dl_anno), n DESC
    """)
    fig = px.bar(
        df_leg, x="leg", y="n", color="esito",
        barmode="stack", color_discrete_sequence=px.colors.qualitative.Set2,
    )
    fig.update_layout(height=380, margin={"t": 10, "b": 30}, legend_title_text="")
    st.plotly_chart(fig, width="stretch")

with col_r:
    st.subheader("Tasso conversione per leg.")
    df_rate = decreti_sql(f"""
        SELECT {LEG_CASE} AS leg,
               ROUND(100.0 * COUNT(*) FILTER (WHERE esito = 'convertito') / COUNT(*), 1) AS pct
        FROM dl GROUP BY 1 ORDER BY MIN(dl_anno)
    """)
    fig = go.Figure(go.Scatter(
        x=df_rate["leg"], y=df_rate["pct"], mode="lines+markers", line={"color": "#6366f1"},
    ))
    fig.update_layout(height=380, margin={"t": 10, "b": 30}, yaxis_title="% convertiti")
    st.plotly_chart(fig, width="stretch")

st.subheader("Tempi di conversione (giorni)")
df_gg = decreti_sql("""
    SELECT giorni_conversione AS gg
    FROM dl WHERE esito = 'convertito'
      AND giorni_conversione IS NOT NULL AND giorni_conversione BETWEEN 1 AND 120
""")
fig = px.histogram(df_gg, x="gg", nbins=40, color_discrete_sequence=["#6366f1"])
fig.add_vline(x=60, line_dash="dash", line_color="red",
              annotation_text="60gg (cost.)", annotation_position="top right")
fig.update_layout(height=340, margin={"t": 10, "b": 30}, bargap=0.1)
st.plotly_chart(fig, width="stretch")
st.caption(
    "Il 90%+ converte entro 60gg. I casi oltre 120gg (esclusi dall'istogramma) "
    "sono quasi tutti artefatti di ricostruzione iter — vedi discussion Analisi #8."
)

with st.expander("I più lenti (>=120 giorni — verificare i casi singoli)"):
    df_lenti = decreti_sql("""
        SELECT dl_numero, dl_anno, data_presentazione, data_conversione,
               giorni_conversione, LEFT(titolo, 70) AS titolo
        FROM dl WHERE esito = 'convertito'
          AND giorni_conversione IS NOT NULL AND giorni_conversione >= 120
        ORDER BY giorni_conversione DESC
    """)
    st.dataframe(df_lenti, hide_index=True, width="stretch")

st.caption("Cifre verificate sul mart 2026-10-08 · discussion: Analisi #8.")
