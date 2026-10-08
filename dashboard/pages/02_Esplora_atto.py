"""Esplora atto — ricerca libera + scheda con relazioni ed ego-network 1-hop."""

import networkx as nx
import plotly.graph_objects as go
import streamlit as st
from sources import mart_sql

st.title("🔍 Esplora atto")

q = st.text_input(
    "Cerca per URN, id o titolo",
    placeholder="es. 231/2001 · urn:nir:stato:decreto.legislativo:2001-06-08;231 · responsabilità",
)

if not q.strip():
    st.info("Digita almeno 3 caratteri. Esempi: `231`, `whistleblowing`, `art. 3`.")
    st.stop()

try:
    q_sql = q.replace("'", "''")
    results = mart_sql(f"""
        SELECT id, tipo, title, anno, stato, materia
        FROM nodes
        WHERE id ILIKE '%{q_sql}%'
           OR LOWER(title) LIKE '%{q_sql.lower()}%'
        ORDER BY CASE WHEN id = '{q_sql}' THEN 0 ELSE 1 END,
                 COALESCE(qualita_score, 0) DESC, anno DESC NULLS LAST
        LIMIT 40
    """)
except Exception as e:  # noqa: BLE001
    st.error(f"Ricerca fallita: {e}")
    st.stop()

if results.empty:
    st.warning("Nessun risultato nel mart.")
    st.stop()

labels = [
    f"{r.id} — {(r.title or '')[:80]}" for r in results.itertuples()
]
choice = st.selectbox(f"{len(results)} risultati", labels)
sel = results.iloc[labels.index(choice)]

st.divider()
m1, m2, m3, m4 = st.columns(4)
m1.metric("Tipo", sel["tipo"] or "—")
m2.metric("Anno", sel["anno"] if sel["anno"] is not None else "—")
m3.metric("Stato", sel["stato"] or "—")
m4.metric("Materia", sel["materia"] or "—")
st.markdown(f"**{sel['title'] or '(senza titolo)'}**")
st.code(sel["id"], language=None)

node_id = sel["id"].replace("'", "''")

try:
    counts = mart_sql(f"""
        SELECT relation,
               SUM(CASE WHEN source_id = '{node_id}' THEN 1 ELSE 0 END) AS n_out,
               SUM(CASE WHEN target_id = '{node_id}' THEN 1 ELSE 0 END) AS n_in
        FROM edges
        WHERE source_id = '{node_id}' OR target_id = '{node_id}'
        GROUP BY 1 ORDER BY (n_out + n_in) DESC LIMIT 15
    """)
    out_partners = mart_sql(f"""
        SELECT e.target_id AS other_id, n.tipo, LEFT(n.title, 90) AS title,
               e.relation, e.weight, e.evidence
        FROM edges e LEFT JOIN nodes n ON n.id = e.target_id
        WHERE e.source_id = '{node_id}'
        ORDER BY e.weight DESC LIMIT 12
    """)
    in_partners = mart_sql(f"""
        SELECT e.source_id AS other_id, n.tipo, LEFT(n.title, 90) AS title,
               e.relation, e.weight, e.evidence
        FROM edges e LEFT JOIN nodes n ON n.id = e.source_id
        WHERE e.target_id = '{node_id}'
        ORDER BY e.weight DESC LIMIT 12
    """)
except Exception as e:  # noqa: BLE001
    st.error(f"Relazioni non caricate: {e}")
    st.stop()

col_a, col_b = st.columns(2)
with col_a:
    st.subheader("Conteggi per relazione")
    st.dataframe(counts, hide_index=True, width="stretch")
with col_b:
    st.subheader("Intelligenza (metrics)")
    try:
        met = mart_sql(
            f"SELECT referenced_by, impact_score, impact_level, references "
            f"FROM metrics WHERE id = '{node_id}'"
        )
        if not met.empty:
            st.dataframe(met, hide_index=True, width="stretch")
        else:
            st.caption("Nessuna metrica per questo nodo.")
    except Exception:  # noqa: BLE001
        st.caption("metrics non disponibile.")

st.subheader("Partner (top per peso)")
tab_out, tab_in = st.tabs(["Out (cita/modifica)", "In (citato da)"])
with tab_out:
    st.dataframe(out_partners, hide_index=True, width="stretch")
with tab_in:
    st.dataframe(in_partners, hide_index=True, width="stretch")

with st.expander("🕸️ Ego-network 1-hop (top partner, layout forza)"):
    if out_partners.empty and in_partners.empty:
        st.info("Nessun arco: nodo isolato nel corpus.")
    else:
        G = nx.Graph()
        G.add_node(node_id)
        for r in out_partners.itertuples():
            G.add_edge(node_id, r.other_id, relation=r.relation)
        for r in in_partners.itertuples():
            G.add_edge(r.other_id, node_id, relation=r.relation)
        pos = nx.spring_layout(G, k=1.1, seed=42)
        x_e, y_e = [], []
        for u, v in G.edges():
            x_e += [pos[u][0], pos[v][0], None]
            y_e += [pos[u][1], pos[v][1], None]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=x_e, y=y_e, mode="lines", line={"width": 0.6, "color": "#94a3b8"}, hoverinfo="none"))
        fig.add_trace(go.Scatter(
            x=[pos[n][0] for n in G.nodes()],
            y=[pos[n][1] for n in G.nodes()],
            mode="markers+text",
            text=[("★ " + n[:28]) if n == node_id else n[:28] for n in G.nodes()],
            textposition="top center",
            marker={"size": [22 if n == node_id else 10 for n in G.nodes()], "color": "#6366f1"},
            hovertext=[f"<b>{n}</b>" for n in G.nodes()],
            hoverinfo="text",
        ))
        fig.update_layout(height=520, showlegend=False, margin={"t": 10, "b": 10, "l": 10, "r": 10})
        st.plotly_chart(fig, width="stretch")

st.caption("Disclaimer: dal mart legal-graph (non Normattiva live).")
