"""Legal Knowledge Graph — Dashboard Streamlit.

Esplora il sistema giuridico italiano come grafo: ogni legge, sentenza,
emendamento e dibattito è collegato a tutto il resto.

3 tab: Panoramica, Corte Costituzionale, Cerca.
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


def get_node_info(con, node_id):
    node = con.execute("""
        SELECT id, tipo, title, data, anno, source, vigente
        FROM nodes WHERE id = ? LIMIT 1
    """, [node_id]).fetchone()
    if not node:
        return None

    out_edges = con.execute("""
        SELECT relation, target_id, weight, source_year FROM edges
        WHERE source_id = ? ORDER BY weight DESC LIMIT 20
    """, [node_id]).fetchall()

    in_edges = con.execute("""
        SELECT relation, source_id, weight, source_year FROM edges
        WHERE target_id = ? ORDER BY weight DESC LIMIT 20
    """, [node_id]).fetchall()

    temporal = con.execute("""
        SELECT relation, target_id, evidence FROM temporal WHERE source_id = ? LIMIT 10
    """, [node_id]).fetchall()

    return {
        "node": {
            "id": node[0], "tipo": node[1], "title": node[2],
            "data": node[3], "anno": node[4], "source": node[5], "vigente": node[6],
        },
        "outgoing": [{"rel": e[0], "target": e[1], "weight": e[2], "year": e[3]} for e in out_edges],
        "incoming": [{"rel": e[0], "source": e[1], "weight": e[2], "year": e[3]} for e in in_edges],
        "temporal": [{"rel": e[0], "target": e[1]} for e in temporal],
    }


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
    tab_overview, tab_corte, tab_search = st.tabs([
        "📊 Panoramica", "⚖️ Corte Costituzionale", "🔍 Cerca"
    ])

    with tab_overview:
        render_overview(con)
    with tab_corte:
        render_corte(con)
    with tab_search:
        render_search(con)


# ─── TAB: Panoramica ────────────────────────────────────────


def render_overview(con):
    st.title("Legal Knowledge Graph")
    st.markdown("**Il sistema giuridico italiano come grafo interrogabile.**")

    # Stats in 2 colonne
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Nodi", f"{n_nodes(con):,}")
    c2.metric("Archi", f"{n_edges(con):,}")
    c3.metric("Relazioni", "12 tipi")
    c4.metric("Fonti", "9")

    st.divider()

    # Grafo per sorgente
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Nodi per fonte")
        result = con.execute("""
            SELECT source, COUNT(*) as n FROM nodes
            GROUP BY source ORDER BY n DESC
        """).fetchdf()
        fig = px.treemap(result, path=["source"], values="n",
                         color="n", color_continuous_scale="Blues")
        fig.update_layout(height=400, margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("Archi per relazione")
        result = con.execute("""
            SELECT relation, COUNT(*) as n FROM edges
            GROUP BY relation ORDER BY n DESC
        """).fetchdf()
        fig = px.bar(result, x="n", y="relation", orientation="h",
                     color="n", color_continuous_scale="Greens")
        fig.update_layout(yaxis={"autorange": "reversed"}, height=400,
                          margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # Timeline
    st.subheader("Attività legislativa per anno")
    result = con.execute("""
        SELECT anno, COUNT(*) as n FROM nodes
        WHERE anno IS NOT NULL AND anno > 1940
        GROUP BY anno ORDER BY anno
    """).fetchdf()
    fig = px.area(result, x="anno", y="n",
                  labels={"n": "Nodi", "anno": "Anno"},
                  color_discrete_sequence=["#3498db"])
    fig.update_layout(height=300, margin=dict(t=10, b=10, l=10, r=10))
    st.plotly_chart(fig, use_container_width=True)


def n_nodes(con):
    return con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]


def n_edges(con):
    return con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]


# ─── TAB: Corte Costituzionale ──────────────────────────────


def render_corte(con):
    st.header("⚖️ Corte Costituzionale")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Articoli più invocati")
        result = con.execute("""
            SELECT REPLACE(target_id, 'costituzione:art:', 'Art. ') as articolo,
                   COUNT(*) as invocazioni, COUNT(DISTINCT source_id) as sentenze
            FROM edges WHERE relation = 'invoca_parametro'
            GROUP BY target_id ORDER BY invocazioni DESC LIMIT 15
        """).fetchdf()
        fig = px.bar(result, x="invocazioni", y="articolo", orientation="h",
                     color="sentenze", color_continuous_scale="Reds")
        fig.update_layout(yaxis={"autorange": "reversed"}, height=400)
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("Norme più impugnate")
        result = con.execute("""
            SELECT target_id as norma, COUNT(*) as impugnazioni,
                   COUNT(DISTINCT source_id) as sentenze
            FROM edges WHERE relation = 'impugna'
            GROUP BY target_id ORDER BY impugnazioni DESC LIMIT 15
        """).fetchdf()
        fig = px.bar(result, x="impugnazioni", y="norma", orientation="h",
                     color="sentenze", color_continuous_scale="Oranges")
        fig.update_layout(yaxis={"autorange": "reversed"}, height=400)
        st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # Doppio binario
    st.subheader("Corte vs Legislazione — Articoli Costituzionali")
    result = con.execute("""
        WITH leg AS (
            SELECT target_id, COUNT(*) as cit FROM edges
            WHERE relation = 'cita_costituzione' GROUP BY target_id
        ),
        corte AS (
            SELECT target_id, COUNT(*) as inv FROM edges
            WHERE relation = 'invoca_parametro' GROUP BY target_id
        )
        SELECT
            REPLACE(COALESCE(c.target_id, l.target_id), 'costituzione:art:', 'Art. ') as articolo,
            COALESCE(c.inv, 0) as invocazioni_corte,
            COALESCE(l.cit, 0) as citazioni_leggi
        FROM corte c FULL OUTER JOIN leg l ON c.target_id = l.target_id
        WHERE COALESCE(c.inv, 0) > 0 OR COALESCE(l.cit, 0) > 0
        ORDER BY (COALESCE(c.inv, 0) + COALESCE(l.cit, 0)) DESC LIMIT 20
    """).fetchdf()

    fig = go.Figure()
    fig.add_trace(go.Bar(name="Corte", x=result["articolo"], y=result["invocazioni_corte"],
                         marker_color="#e74c3c"))
    fig.add_trace(go.Bar(name="Legislazione", x=result["articolo"], y=result["citazioni_leggi"],
                         marker_color="#3498db"))
    fig.update_layout(barmode="group", height=500,
                      title="Articoli Costituzionali: chi li usa?")
    st.plotly_chart(fig, use_container_width=True)

    # DDL più contestate
    st.divider()
    st.subheader("DDL più contestate (per emendamenti)")
    result = con.execute("""
        SELECT n.title as provvedimento, n.id,
               COUNT(DISTINCT e.source_id) as emendamenti,
               CASE WHEN bridge.target_id IS NOT NULL THEN '✓ Legge' ELSE 'In attesa' END as stato
        FROM edges e JOIN nodes n ON e.target_id = n.id
        LEFT JOIN edges bridge ON bridge.source_id = n.id AND bridge.relation = 'diventa_legge'
        WHERE e.relation = 'emendamento'
        GROUP BY n.id, n.title, bridge.target_id
        ORDER BY emendamenti DESC LIMIT 15
    """).fetchdf()
    st.dataframe(result, use_container_width=True, hide_index=True)


# ─── TAB: Cerca ─────────────────────────────────────────────


def render_search(con):
    st.header("🔍 Cerca nel grafo")

    query = st.text_input("Cerca per titolo o ID", placeholder="es. Codice Penale, art. 3, legge 234/2012")

    c1, c2 = st.columns(2)
    with c1:
        tipo = st.selectbox("Tipo", ["", "LEGGE", "DECRETO-LEGGE", "DECRETO LEGISLATIVO",
                                      "SENTENZA", "Progetto di Legge", "DIRETTIVA", "REGOLAMENTO"])
    with c2:
        source = st.selectbox("Fonte", ["", "normativa", "senato", "camera_ddl",
                                         "costituzione", "eu", "gu"])

    if query or tipo or source:
        conditions, params = [], []
        if query:
            conditions.append("(LOWER(title) LIKE ? OR LOWER(id) LIKE ?)")
            params.extend([f"%{query.lower()}%", f"%{query.lower()}%"])
        if tipo:
            conditions.append("UPPER(tipo) = ?")
            params.append(tipo.upper())
        if source:
            conditions.append("source = ?")
            params.append(source)

        where = f"WHERE {' AND '.join(conditions)}"
        result = con.execute(f"""
            SELECT id, tipo, title, data, anno, source
            FROM nodes {where}
            ORDER BY anno DESC NULLS LAST LIMIT 50
        """, params).fetchdf()

        st.write(f"**{len(result)} risultati**")
        st.dataframe(result, use_container_width=True, hide_index=True)

        if len(result) > 0:
            st.divider()
            selected = st.selectbox(
                "Seleziona un nodo per esplorare i collegamenti",
                result["id"].tolist(),
                format_func=lambda x: f"{x[:80]}"
            )
            if selected:
                info = get_node_info(con, selected)
                if info:
                    n = info["node"]
                    st.markdown(f"### {n['tipo']} — `{n['id']}`")
                    st.markdown(f"**{n['title'][:200] if n['title'] else 'Senza titolo'}**")

                    mc1, mc2, mc3, mc4 = st.columns(4)
                    mc1.caption(f"Data: {n['data'] or '—'}")
                    mc2.caption(f"Anno: {n['anno'] or '—'}")
                    mc3.caption(f"Fonte: {n['source']}")

                    if info["outgoing"]:
                        st.markdown("**→ Collegamenti uscenti:**")
                        for e in info["outgoing"][:10]:
                            st.caption(f"  `{e['rel']}` → {e['target'][:80]} (peso {e['weight']:.0f})")

                    if info["incoming"]:
                        st.markdown("**← Collegamenti entranti:**")
                        for e in info["incoming"][:10]:
                            st.caption(f"  `{e['rel']}` ← {e['source'][:80]} (peso {e['weight']:.0f})")

                    if info["temporal"]:
                        st.markdown("**⏱ Temporale:**")
                        for e in info["temporal"]:
                            st.caption(f"  `{e['rel']}` → {e['target'][:80]}")


if __name__ == "__main__":
    main()
