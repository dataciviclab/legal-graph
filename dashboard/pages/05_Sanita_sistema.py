"""Salute del sistema — triage normativo (basi della discussion Analisi #9)."""

import plotly.express as px
import streamlit as st
from sources import mart_sql

st.title("🩺 Salute del sistema normativo")
st.caption(
    "Radiografia del testo normativo nel corpus: stabilità, ritardi, "
    "riferimenti spenti, complessità. **Non** è una diagnosi giuridica — "
    "è una lista di triage per occhi umani e agenti."
)

try:
    salute = mart_sql("""
        SELECT
            (SELECT COUNT(*) FROM edges e
               JOIN nodes s ON s.id = e.source_id
               JOIN nodes t ON t.id = e.target_id
             WHERE e.relation = 'riferimento'
               AND s.stato = 'vigente' AND t.stato = 'abrogato'
            ) AS citazioni_spenti,
            (SELECT COUNT(DISTINCT e.source_id) FROM edges e
               JOIN nodes s ON s.id = e.source_id
               JOIN nodes t ON t.id = e.target_id
             WHERE e.relation = 'riferimento'
               AND s.stato = 'vigente' AND t.stato = 'abrogato'
            ) AS atti_spenti,
            (SELECT COUNT(*) FROM edges e
               JOIN nodes s ON s.id = e.source_id
               JOIN nodes t ON t.id = e.target_id
             WHERE e.relation = 'attua_delega'
               AND s.anno IS NOT NULL AND t.anno IS NOT NULL
               AND s.anno - t.anno > 15
            ) AS deleghe_lente
    """).iloc[0]
except Exception as e:  # noqa: BLE001
    st.error(f"Mart non raggiungibile: {e}")
    st.stop()

k1, k2, k3 = st.columns(3)
k1.metric("Citazioni a norme abrogate", f"{int(salute['citazioni_spenti']):,}".replace(",", "."),
          help="Da atti vigenti: triage, non prova d'errore (citare abrogato è a volte legittimo)")
k2.metric("Atti vigenti coinvolti", f"{int(salute['atti_spenti']):,}".replace(",", "."))
k3.metric("Deleghe attuate a >15 anni", int(salute["deleghe_lente"]),
          help="D.Lgs arrivati oltre 15 anni dopo la delega")

st.divider()
col_l, col_r = st.columns([3, 2])

with col_l:
    st.subheader("🔗 Triage: norme abrogate ancora più citate")
    df_triage = mart_sql("""
        SELECT t.id AS norma_abrogata, LEFT(t.title, 70) AS titolo, COUNT(*) AS n_citazioni
        FROM edges e
        JOIN nodes s ON s.id = e.source_id
        JOIN nodes t ON t.id = e.target_id
        WHERE e.relation = 'riferimento'
          AND s.stato = 'vigente' AND t.stato = 'abrogato'
        GROUP BY 1, 2 ORDER BY n_citazioni DESC LIMIT 30
    """)
    st.dataframe(df_triage, hide_index=True, width="stretch")
    st.download_button(
        "⬇️ Esporta top-30 (CSV)",
        df_triage.to_csv(index=False).encode("utf-8"),
        file_name="triage_norme_spent.csv",
        mime="text/csv",
    )

with col_r:
    st.subheader("Top abrogatori (cascata)")
    df_abro = mart_sql("""
        SELECT LEFT(n.title, 60) AS atto, COUNT(*) AS n_abrogati
        FROM edges e JOIN nodes n ON n.id = e.source_id
        WHERE e.relation = 'abroga' AND e.source_id LIKE 'urn:nir:%'
        GROUP BY 1 ORDER BY n_abrogati DESC LIMIT 8
    """)
    fig = px.bar(df_abro.sort_values("n_abrogati"), x="n_abrogati", y="atto",
                 orientation="h", color_discrete_sequence=["#f59e0b"])
    fig.update_layout(height=360, margin={"t": 10, "b": 30}, yaxis_title="")
    st.plotly_chart(fig, width="stretch")

st.subheader("⏱️ Deleghe dimenticate (attuazione a >15 anni)")
df_deleghe = mart_sql("""
    SELECT s.anno - t.anno AS ritardo_anni,
           LEFT(t.title, 70) AS delega,
           LEFT(s.title, 70) AS attuazione
    FROM edges e
    JOIN nodes s ON s.id = e.source_id
    JOIN nodes t ON t.id = e.target_id
    WHERE e.relation = 'attua_delega'
      AND s.anno IS NOT NULL AND t.anno IS NOT NULL
      AND s.anno - t.anno > 15
    ORDER BY ritardo_anni DESC
""")
st.dataframe(df_deleghe, hide_index=True, width="stretch")

st.subheader("🏋️ I testi più corposi (normativa)")
df_lunghi = mart_sql("""
    SELECT LEFT(title, 70) AS titolo, tipo, length_chars
    FROM nodes
    WHERE source = 'normativa' AND length_chars > 100000
    ORDER BY length_chars DESC LIMIT 10
""")
st.dataframe(df_lunghi, hide_index=True, width="stretch")

st.caption(
    "⚠️ Limiti: corpus ~21k atti (non l'intera normativa); stato = marker IC; "
    "citazioni = estrazione regex. Discussione di riferimento: Analisi #9."
)
