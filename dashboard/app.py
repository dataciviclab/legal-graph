"""Legal Knowledge Graph — Dashboard Streamlit.

3 viste che specchiano i 3 tool MCP:
- 📜 Catena del Diritto (legal_chain)
- ⚖️ Giurisprudenza (legal_jurisprudence)
- 🏛️ Parlamento (legal_parliament)
"""

import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(
    page_title="Legal Knowledge Graph",
    page_icon="⚖️",
    layout="wide",
)

import os

DEFAULT_DATA = os.path.join(os.path.dirname(__file__), "..", "data")


@st.cache_resource
def load_data():
    data_dir = DEFAULT_DATA
    con = duckdb.connect(":memory:")
    con.execute(f"CREATE TABLE nodes AS SELECT * FROM read_parquet('{data_dir}/legal_nodes.parquet')")
    con.execute(f"CREATE TABLE edges AS SELECT * FROM read_parquet('{data_dir}/legal_edges.parquet')")
    con.execute(f"CREATE TABLE temporal AS SELECT * FROM read_parquet('{data_dir}/legal_edges_temporal.parquet')")
    return con


def find_node(con, node_id):
    """Trova un nodo per ID o pattern parziale."""
    return con.execute("""
        SELECT id, tipo, title, CAST(data AS VARCHAR) as data, anno, source
        FROM nodes WHERE id = ? OR id LIKE ?
        LIMIT 1
    """, [node_id, f"%{node_id}%"]).fetchone()


def main():
    con = load_data()

    # ─── Sidebar ──────────────────────────────────────────────
    st.sidebar.title("⚖️ Legal Graph")
    n_nodes = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    n_edges = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    st.sidebar.metric("Nodi", f"{n_nodes:,}")
    st.sidebar.metric("Archi", f"{n_edges:,}")
    st.sidebar.markdown("---")
    st.sidebar.markdown("[GitHub](https://github.com/dataciviclab)")

    # ─── Tabs ─────────────────────────────────────────────────
    tab_chain, tab_juris, tab_parl = st.tabs([
        "📜 Catena del Diritto", "⚖️ Giurisprudenza", "🏛️ Parlamento"
    ])

    with tab_chain:
        render_chain(con)
    with tab_juris:
        render_jurisprudence(con)
    with tab_parl:
        render_parliament(con)


# ─── TAB: Catena del Diritto ─────────────────────────────────


def render_chain(con):
    st.header("📜 Catena del Diritto")
    st.markdown("DDL → Legge → D.Lgs → EU — deleghe, recepimenti, collegamenti Costituzionali.")

    node_id = st.text_input("ID atto", placeholder="es. urn:nir:stato:decreto.legislativo:2017-07-03;117")

    if node_id:
        node = find_node(con, node_id)
        if not node:
            st.error(f"Nodo non trovato: {node_id}")
            return

        actual_id = node[0]
        st.markdown(f"### {node[1]} — `{actual_id}`")
        st.markdown(f"**{(node[2] or '')[:200]}**")
        c1, c2, c3 = st.columns(3)
        c1.caption(f"Data: {node[3] or '—'}")
        c2.caption(f"Anno: {node[4] or '—'}")
        c3.caption(f"Fonte: {node[5]}")

        st.divider()

        # Chain forward
        chain_rels = ['diventa_legge', 'attua_delega', 'recepisce_direttiva',
                      'attua_regolamento', 'collega_ue', 'cita_costituzione']

        col1, col2 = st.columns(2)

        with col1:
            st.subheader("→ Dove porta questo atto")
            forward = con.execute(f"""
                SELECT e.relation, e.target_id, n.tipo, LEFT(n.title, 80) as title, n.anno, e.weight
                FROM edges e JOIN nodes n ON e.target_id = n.id
                WHERE e.source_id = ? AND e.relation IN ({','.join(['?']*len(chain_rels))})
                ORDER BY e.weight DESC LIMIT 15
            """, [actual_id] + chain_rels).fetchdf()

            if len(forward) > 0:
                for _, row in forward.iterrows():
                    rel = row['relation']
                    icon = {'diventa_legge': '✅', 'attua_delega': '📋',
                            'recepisce_direttiva': '🇪🇺', 'cita_costituzione': '📜'}.get(rel, '→')
                    st.caption(f"{icon} `{rel}` → {row['tipo']} {row['target_id'][-40:]}")
                    st.caption(f"   {row['title'][:70]}")
            else:
                st.info("Nessun collegamento in uscita.")

        with col2:
            st.subheader("← Da dove viene")
            backward = con.execute(f"""
                SELECT e.relation, e.source_id, n.tipo, LEFT(n.title, 80) as title, n.anno, e.weight
                FROM edges e JOIN nodes n ON e.source_id = n.id
                WHERE e.target_id = ? AND e.relation IN ({','.join(['?']*len(chain_rels))})
                ORDER BY e.weight DESC LIMIT 15
            """, [actual_id] + chain_rels).fetchdf()

            if len(backward) > 0:
                for _, row in backward.iterrows():
                    rel = row['relation']
                    icon = {'diventa_legge': '✅', 'attua_delega': '📋',
                            'recepisce_direttiva': '🇪🇺', 'cita_costituzione': '📜'}.get(rel, '←')
                    st.caption(f"{icon} `{rel}` ← {row['tipo']} {row['source_id'][-40:]}")
                    st.caption(f"   {row['title'][:70]}")
            else:
                st.info("Nessun collegamento in entrata.")

        # Temporal
        temporal = con.execute("""
            SELECT relation, evidence FROM temporal WHERE source_id = ? LIMIT 5
        """, [actual_id]).fetchall()
        if temporal:
            st.divider()
            st.subheader("📅 Timeline")
            for rel, ev in temporal:
                st.caption(f"  `{rel}` — {ev}")


# ─── TAB: Giurisprudenza ─────────────────────────────────────


def render_jurisprudence(con):
    st.header("⚖️ Giurisprudenza")
    st.markdown("Sentenze, impugnazioni, parametri Costituzionali.")

    node_id = st.text_input("ID atto o articolo", placeholder="es. sentenza:2009-0151 o costituzione:art:3")

    if node_id:
        node = find_node(con, node_id)
        if not node:
            st.error(f"Nodo non trovato: {node_id}")
            return

        actual_id = node[0]
        st.markdown(f"### {node[1]} — `{actual_id}`")
        st.markdown(f"**{(node[2] or '')[:200]}**")

        st.divider()

        col1, col2 = st.columns(2)

        with col1:
            # What does this node impugn?
            impugna = con.execute("""
                SELECT e.target_id, n.tipo, LEFT(n.title, 80) as title, n.anno, e.weight
                FROM edges e JOIN nodes n ON e.target_id = n.id
                WHERE e.source_id = ? AND e.relation = 'impugna'
                ORDER BY e.weight DESC LIMIT 15
            """, [actual_id]).fetchdf()

            if len(impugna) > 0:
                st.subheader("🔴 Impugna")
                for _, row in impugna.iterrows():
                    st.caption(f"  → {row['tipo']} {row['target_id'][-40:]}")
                    st.caption(f"    {row['title'][:70]}")

            # What impugns this node?
            impugnata_da = con.execute("""
                SELECT e.source_id, n.tipo, LEFT(n.title, 80) as title, n.anno, e.weight
                FROM edges e JOIN nodes n ON e.source_id = n.id
                WHERE e.target_id = ? AND e.relation = 'impugna'
                ORDER BY e.weight DESC LIMIT 15
            """, [actual_id]).fetchdf()

            if len(impugnata_da) > 0:
                st.subheader("🔴 Impugnata da")
                for _, row in impugnata_da.iterrows():
                    st.caption(f"  ← {row['tipo']} {row['source_id'][-40:]}")
                    st.caption(f"    {row['title'][:70]}")

        with col2:
            # Parameters invoked
            parametri = con.execute("""
                SELECT e.target_id, LEFT(n.title, 80) as title, e.weight
                FROM edges e JOIN nodes n ON e.target_id = n.id
                WHERE e.source_id = ? AND e.relation = 'invoca_parametro'
                ORDER BY e.weight DESC LIMIT 15
            """, [actual_id]).fetchdf()

            if len(parametri) > 0:
                st.subheader("📜 Parametri invocati")
                for _, row in parametri.iterrows():
                    st.caption(f"  → {row['target_id']}")
                    st.caption(f"    {row['title'][:70]}")

            # Cited by
            citata_da = con.execute("""
                SELECT e.source_id, n.tipo, LEFT(n.title, 80) as title, n.anno, e.weight
                FROM edges e JOIN nodes n ON e.source_id = n.id
                WHERE e.target_id = ? AND e.relation = 'cita_costituzione'
                ORDER BY e.weight DESC LIMIT 15
            """, [actual_id]).fetchdf()

            if len(citata_da) > 0:
                st.subheader("📜 Citata da")
                for _, row in citata_da.iterrows():
                    st.caption(f"  ← {row['tipo']} {row['source_id'][-40:]}")
                    st.caption(f"    {row['title'][:70]}")


# ─── TAB: Parlamento ─────────────────────────────────────────


def render_parliament(con):
    st.header("🏛️ Parlamento")
    st.markdown("Emendamenti, interventi, stato dell'iter legislativo.")

    node_id = st.text_input("ID DDL", placeholder="es. senato:40754 o camera:3053")

    if node_id:
        node = find_node(con, node_id)
        if not node:
            st.error(f"Nodo non trovato: {node_id}")
            return

        actual_id = node[0]
        st.markdown(f"### {node[1]} — `{actual_id}`")
        st.markdown(f"**{(node[2] or '')[:200]}**")
        c1, c2, c3 = st.columns(3)
        c1.caption(f"Data: {node[3] or '—'}")
        c2.caption(f"Anno: {node[4] or '—'}")
        c3.caption(f"Fonte: {node[5]}")

        st.divider()

        col1, col2 = st.columns(2)

        with col1:
            # Emendamenti
            n_emend = con.execute("""
                SELECT COUNT(*) FROM edges
                WHERE target_id = ? AND relation = 'emendamento'
            """, [actual_id]).fetchone()[0]
            st.metric("Emendamenti", f"{n_emend:,}")

            if n_emend > 0:
                top_emend = con.execute("""
                    SELECT n.title, COUNT(*) as cnt
                    FROM edges e JOIN nodes n ON e.source_id = n.id
                    WHERE e.target_id = ? AND e.relation = 'emendamento'
                    GROUP BY n.title ORDER BY cnt DESC LIMIT 10
                """, [actual_id]).fetchdf()
                st.subheader("Top emendamenti")
                fig = px.bar(top_emend.head(5), x="cnt", y="title", orientation="h",
                             color="cnt", color_continuous_scale="Blues")
                fig.update_layout(yaxis={"autorange": "reversed"}, height=250,
                                  margin=dict(t=10, b=10, l=10, r=10))
                st.plotly_chart(fig, use_container_width=True)

        with col2:
            # Interventi
            n_interventi = con.execute("""
                SELECT COUNT(*) FROM edges
                WHERE target_id = ? AND relation = 'intervento'
            """, [actual_id]).fetchone()[0]
            st.metric("Interventi", f"{n_interventi:,}")

            # Diventa legge?
            diventa = con.execute("""
                SELECT e.target_id, LEFT(n.title, 100) as title, n.anno
                FROM edges e JOIN nodes n ON e.target_id = n.id
                WHERE e.source_id = ? AND e.relation = 'diventa_legge'
                LIMIT 5
            """, [actual_id]).fetchall()

            if diventa:
                st.success("**✓ Diventato legge:**")
                for tid, title, anno in diventa:
                    st.caption(f"  {title[:80]} ({anno})")
            else:
                st.warning("**Non è diventato legge** (o ancora in iter)")

            # Testo
            testo = con.execute("""
                SELECT e.target_id, LEFT(n.title, 80) as title
                FROM edges e JOIN nodes n ON e.target_id = n.id
                WHERE e.source_id = ? AND e.relation = 'testo_atto'
                LIMIT 3
            """, [actual_id]).fetchall()

            if testo:
                st.caption("**Testo:**")
                for tid, title in testo:
                    st.caption(f"  {title[:70]}")


if __name__ == "__main__":
    main()
