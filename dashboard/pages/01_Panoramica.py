"""Panoramica — il grafo in numeri e i tre indicatori di salute."""

import plotly.graph_objects as go
import streamlit as st
from lab_connectors.formatters import fmt_num, fmt_pct
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
    fig = go.Figure(
        go.Pie(labels=df_src["source"], values=df_src["n"], hole=0.45)
    )
    fig.update_layout(height=380, margin={"t": 10, "b": 30})
    st.plotly_chart(fig, width="stretch")

st.subheader("🩺 Tre segnali di salute (dal mart)")

try:
    salute = mart_sql("""
        SELECT
            (SELECT COUNT(*) FROM edges e
               JOIN nodes s ON s.id = e.source_id
               JOIN nodes t ON t.id = e.target_id
             WHERE e.relation = 'riferimento'
               AND s.stato = 'vigente' AND t.stato = 'abrogato'
            ) AS citazioni_spenti,
            (SELECT COUNT(*) FROM edges e
               JOIN nodes s ON s.id = e.source_id
               JOIN nodes t ON t.id = e.target_id
             WHERE e.relation = 'attua_delega'
               AND s.anno IS NOT NULL AND t.anno IS NOT NULL
               AND s.anno - t.anno > 15
            ) AS deleghe_lente,
            (SELECT COUNT(*) FROM edges
             WHERE relation IN ('abroga', 'sostituisce') AND weight > 1
            ) AS modifiche_doppie
    """).iloc[0]
    s1, s2, s3 = st.columns(3)
    s1.metric(
        "Citazioni a norme abrogate",
        fmt_num(salute["citazioni_spenti"]),
        help="Riferimenti da atti vigenti a norme abrogate: lista di triage, non prova d'errore",
    )
    s2.metric(
        "Deleghe attuate a >15 anni",
        fmt_num(salute["deleghe_lente"]),
        help="D.Lgs arrivati oltre 15 anni dopo la delega",
    )
    s3.metric(
        "Modifiche AKN dedup (peso>1)",
        fmt_num(salute["modifiche_doppie"]),
        help="Abiti abroga/sostituisce confermati da due fonti (regex + AKN)",
    )
except Exception as e:  # noqa: BLE001
    st.warning(f"Indicatori salute non calcolabili: {e}")

st.caption(
    "Disclaimer: dal mart legal-graph (non Normattiva live). "
    f"Stato normativo = marker corpus. Copertura eiv: finestra {fmt_pct(0.48)} dei nodi normativa."
)
