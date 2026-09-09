"""Legal Knowledge Graph — Dashboard Streamlit.

Esplora il sistema giuridico italiano come grafo: ogni legge, sentenza,
emendamento e dibattito è collegato a tutto il resto.
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
METRICS_FILE = os.path.join(DEFAULT_DATA, "graph_metrics.parquet")


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
        WHERE source_id = ? ORDER BY weight DESC LIMIT 15
    """, [node_id]).fetchall()

    in_edges = con.execute("""
        SELECT relation, source_id, weight, source_year FROM edges
        WHERE target_id = ? ORDER BY weight DESC LIMIT 15
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


def render_node_card(info):
    n = info["node"]
    vigente = "Vigente" if n["vigente"] else "Non vigente" if n["vigente"] is not None else "Sconosciuto"
    st.markdown(f"**{n['tipo']}** — `{n['id']}`")
    st.markdown(f"**{n['title'][:200] if n['title'] else 'Senza titolo'}**")
    c1, c2, c3, c4 = st.columns(4)
    c1.caption(f"Data: {n['data'] or '—'}")
    c2.caption(f"Anno: {n['anno'] or '—'}")
    c3.caption(f"Fonte: {n['source']}")
    c4.caption(f"Stato: {vigente}")

    if info["outgoing"]:
        st.markdown("**→ Collegamenti uscenti:**")
        for e in info["outgoing"][:10]:
            st.caption(f"  `{e['rel']}` → {e['target'][:60]} (peso {e['weight']:.0f})")
    if info["incoming"]:
        st.markdown("**← Collegamenti entranti:**")
        for e in info["incoming"][:10]:
            st.caption(f"  `{e['rel']}` ← {e['source'][:60]} (peso {e['weight']:.0f})")
    if info["temporal"]:
        st.markdown("**⏰ Temporale:**")
        for e in info["temporal"]:
            st.caption(f"  `{e['rel']}` → {e['target'][:60]}")


def main():
    con = load_data()

    # ─── Sidebar ──────────────────────────────────────────────
    st.sidebar.title("⚖️ Legal Graph")
    n_nodes = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    n_edges = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    n_temporal = con.execute("SELECT COUNT(*) FROM temporal").fetchone()[0]
    st.sidebar.metric("Nodi", f"{n_nodes:,}")
    st.sidebar.metric("Archi", f"{n_edges:,}")
    st.sidebar.metric("Temporali", f"{n_temporal:,}")
    st.sidebar.markdown("---")
    st.sidebar.markdown("[GitHub](https://github.com/dataciviclab)")

    # ─── Tabs ─────────────────────────────────────────────────
    tab_home, tab_explore, tab_corte, tab_senato, tab_intel, tab_raw = st.tabs([
        "🏠 Cos'è", "🔍 Esplora", "⚖️ Corte", "🏛️ Senato", "🧠 Intelligence", "⚙️ Raw"
    ])

    with tab_home:
        render_home(con)
    with tab_explore:
        render_explore(con)
    with tab_corte:
        render_corte(con)
    with tab_senato:
        render_senato(con)
    with tab_intel:
        render_intelligence(con)
    with tab_raw:
        render_raw(con)


# ─── TAB: Cos'è ─────────────────────────────────────────────


def render_home(con):
    st.title("Legal Knowledge Graph")
    st.markdown("""
    **Il sistema giuridico italiano come grafo interrogabile.**

    Ogni legge, sentenza, emendamento e dibattito è collegato a tutto il resto.
    Questo grafo non è un chatbot che "sa le leggi" — è l'infrastruttura dati
    che permette di **ricostruire le connessioni** tra fonti del diritto.
    """)

    # Stats
    c1, c2, c3 = st.columns(3)
    c1.metric("Nodi", f"{n_nodes:,}")
    c2.metric("Archi", f"{n_edges:,}")
    c3.metric("Relazioni", "12 tipi")

    st.divider()

    # Famous cases
    st.subheader("Casistiche celebri")
    st.markdown("Cosa puoi scoprire con il grafo:")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**⚖️ Fecondazione assistita (Legge 40/2004)**")
        info = get_node_info(con, "sentenza:2009-0151")
        if info:
            st.caption("Sentenza 151/2009 — la Corte dichiara incostituzionale la Legge 40/2004")
            for e in info["outgoing"][:3]:
                if e["rel"] == "impugna":
                    st.caption(f"  → Impugna: {e['target']}")
                elif e["rel"] == "invoca_parametro":
                    st.caption(f"  → Invoca: {e['target']}")
        st.caption("12 sentenze hanno impugnato la Legge 40/2004 dal 2006 al 2025.")

    with col2:
        st.markdown("**🏛️ DDL più contestata")
        result = con.execute("""
            SELECT n.title, COUNT(DISTINCT e.source_id) as emend
            FROM edges e JOIN nodes n ON e.target_id = n.id
            WHERE e.relation = 'emendamento'
            GROUP BY n.id, n.title ORDER BY emend DESC LIMIT 1
        """).fetchone()
        if result:
            st.caption(f"{result[0][:60] if result[0] else '?'}")
            st.caption(f"  → {result[1]:,} emendamenti")

    st.divider()

    # How it works
    st.subheader("Come funziona")
    st.markdown("""
    | Fonte | Cosa | Connessione |
    |---|---|---|
    | Normativa | Leggi e decreti | Riferimenti incrociati |
    | Corte Costituzionale | 22K sentenze | Impugna + invoca parametri |
    | Senato | 400K emendamenti | Emendamento → DDL → legge |
    | Costituzione | 139 articoli | Citazioni + parametri |
    | EUR-Lex | Direttive UE | Recepimento |
    | GU Monitor | Gazzetta Ufficiale | Pubblicazione |
    """)

    st.divider()
    st.subheader("Prova tu")
    st.markdown("Vai al tab **🔍 Esplora** per trovare un atto e vedere tutti i suoi collegamenti.")


# ─── TAB: Esplora ───────────────────────────────────────────


def render_explore(con):
    st.subheader("🔍 Esplora nel grafo")

    query = st.text_input("Cerca per titolo o ID", placeholder="es. Codice Penale, art. 3, legge 234/2012")

    c1, c2, c3 = st.columns(3)
    with c1:
        tipo = st.selectbox("Tipo", ["", "LEGGE", "DECRETO-LEGGE", "DECRETO LEGISLATIVO",
                                      "DECRETO", "COSTITUZIONE", "DIRETTIVA", "REGOLAMENTO",
                                      "SENTENZA", "SENATORE"])
    with c2:
        anno_min = st.number_input("Da anno", value=0, min_value=0, max_value=2026)
    with c3:
        anno_max = st.number_input("A anno", value=0, min_value=0, max_value=2026)

    if query or tipo or anno_min > 0 or anno_max > 0:
        conditions, params = [], []
        if query:
            conditions.append("(LOWER(title) LIKE ? OR LOWER(id) LIKE ?)")
            params.extend([f"%{query.lower()}%", f"%{query.lower()}%"])
        if tipo:
            conditions.append("UPPER(tipo) = ?")
            params.append(tipo.upper())
        if anno_min > 0:
            conditions.append("anno >= ?")
            params.append(anno_min)
        if anno_max > 0:
            conditions.append("anno <= ?")
            params.append(anno_max)

        where = f"WHERE {' AND '.join(conditions)}"
        result = con.execute(f"""
            SELECT id, tipo, title, data, anno, source
            FROM nodes {where}
            ORDER BY anno DESC NULLS LAST LIMIT 50
        """, params).fetchdf()

        st.write(f"**{len(result)} risultati**")
        st.dataframe(result, use_container_width=True)

        if len(result) > 0:
            st.divider()
            selected = st.selectbox(
                "Seleziona un nodo per esplorare i collegamenti",
                result["id"].tolist(),
                format_func=lambda x: f"{x[:60]}"
            )
            if selected:
                info = get_node_info(con, selected)
                if info:
                    render_node_card(info)

                    # Legislative path
                    if info["outgoing"]:
                        st.divider()
                        st.markdown("**Percorso legislativo:**")
                        for e in info["outgoing"]:
                            if e["rel"] == "diventa_legge":
                                st.success(f"DDL → {e['target'][:60]}")
                            elif e["rel"] == "riferimento":
                                st.info(f"Referenzia: {e['target'][:60]} (peso {e['weight']:.0f})")
                            elif e["rel"] == "impugna":
                                st.error(f"Impugna: {e['target'][:60]}")
                            elif e["rel"] == "cita_costituzione":
                                st.warning(f"Cita Costituzione: {e['target'][:60]}")


# ─── TAB: Corte ─────────────────────────────────────────────


def render_corte(con):
    st.subheader("⚖️ Corte Costituzionale")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Articoli più invocati**")
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
        st.markdown("**Norme più impugnate**")
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
    st.subheader("Doppio binario: Corte vs Legislazione")

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
    fig.add_trace(go.Bar(name="Corte", x=result["articolo"], y=result["invocazioni_corte"], marker_color="#e74c3c"))
    fig.add_trace(go.Bar(name="Legislazione", x=result["articolo"], y=result["citazioni_leggi"], marker_color="#3498db"))
    fig.update_layout(barmode="group", height=500, title="Articoli: Corte vs Legislazione")
    st.plotly_chart(fig, use_container_width=True)


# ─── TAB: Senato ────────────────────────────────────────────


def render_senato(con):
    st.subheader("🏛️ Senato")

    col1, col2 = st.columns(2)

    with col1:
        result = con.execute("""
            SELECT relation, COUNT(*) as n FROM edges
            WHERE relation IN ('diventa_legge', 'testo_atto', 'emendamento', 'intervento')
            GROUP BY relation ORDER BY n DESC
        """).fetchdf()
        fig = px.bar(result, x="n", y="relation", orientation="h",
                     color="n", color_continuous_scale="Greens")
        fig.update_layout(yaxis={"autorange": "reversed"}, height=300)
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        result = con.execute("""
            SELECT source, COUNT(*) as n FROM nodes
            WHERE source IN ('senato', 'senato_corpus', 'senato_emend', 'senato_dib')
            GROUP BY source ORDER BY n DESC
        """).fetchdf()
        fig = px.pie(result, values="n", names="source",
                     color_discrete_sequence=px.colors.qualitative.Set3)
        st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.subheader("DDL più contestate")
    result = con.execute("""
        SELECT n.title as provvedimento, n.id,
               COUNT(DISTINCT e.source_id) as emendamenti,
               CASE WHEN bridge.target_id IS NOT NULL THEN 'Legge' ELSE 'In attesa' END as stato
        FROM edges e JOIN nodes n ON e.target_id = n.id
        LEFT JOIN edges bridge ON bridge.source_id = n.id AND bridge.relation = 'diventa_legge'
        WHERE e.relation = 'emendamento'
        GROUP BY n.id, n.title, bridge.target_id
        ORDER BY emendamenti DESC LIMIT 15
    """).fetchdf()
    st.dataframe(result, use_container_width=True)

    # Bridge stats
    st.divider()
    bridge = con.execute("""
        SELECT COUNT(DISTINCT e1.source_id) as totali,
               COUNT(DISTINCT CASE WHEN e2.target_id IS NOT NULL THEN e1.source_id END) as con_legge
        FROM edges e1
        LEFT JOIN edges e2 ON e1.target_id = e2.source_id AND e2.relation = 'diventa_legge'
        WHERE e1.relation = 'emendamento'
    """).fetchone()
    if bridge[0] > 0:
        pct = bridge[1] * 100 // bridge[0]
        st.metric("Emendamenti → Legge", f"{bridge[1]:,} / {bridge[0]:,}", f"{pct}%")


# ─── TAB: Intelligence ──────────────────────────────────────


def render_intelligence(con):
    st.subheader("🧠 Intelligence")

    if not os.path.exists(METRICS_FILE):
        st.warning("Esegui: `python -m legal_graph.graph_intelligence`")
        return

    metrics = con.execute(f"SELECT * FROM read_parquet('{METRICS_FILE}')").fetchdf()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Critici", f"{len(metrics[metrics['impact_level'] == 'critical']):,}")
    c2.metric("Obsoleti", f"{len(metrics[metrics['age_risk'] == 'obsolete_candidate']):,}")
    c3.metric("Complessi", f"{len(metrics[metrics['complexity_level'].isin(['very_complex', 'complex'])]):,}")
    c4.metric("Dormienti", f"{len(metrics[(metrics['activity_level'] == 'dormant') & (metrics['referenced_by'] > 5)]):,}")

    st.divider()

    tab1, tab2, tab3, tab4 = st.tabs(["🔴 Critici", "🟡 Obsoleti", "🟠 Complessi", "⚪ Dormienti"])

    with tab1:
        crit = metrics[metrics['impact_level'] == 'critical'].sort_values('referenced_by', ascending=False).head(20)
        st.dataframe(crit[['id', 'title', 'referenced_by', 'impact_score', 'age_years']].rename(columns={
            'id': 'ID', 'title': 'Titolo', 'referenced_by': 'Riferimenti', 'impact_score': 'Score', 'age_years': 'Età'
        }), use_container_width=True)

    with tab2:
        obs = metrics[metrics['age_risk'] == 'obsolete_candidate'].sort_values('age_years', ascending=False).head(20)
        st.dataframe(obs[['id', 'title', 'age_years', 'referenced_by']].rename(columns={
            'id': 'ID', 'title': 'Titolo', 'age_years': 'Età', 'referenced_by': 'Riferimenti'
        }), use_container_width=True)

    with tab3:
        comp = metrics[metrics['complexity_level'].isin(['very_complex', 'complex'])].sort_values('references', ascending=False).head(20)
        st.dataframe(comp[['id', 'title', 'references', 'referenced_by', 'age_years']].rename(columns={
            'id': 'ID', 'title': 'Titolo', 'references': 'Dipendenze', 'referenced_by': 'Riferimenti', 'age_years': 'Età'
        }), use_container_width=True)

    with tab4:
        dorm = metrics[(metrics['activity_level'] == 'dormant') & (metrics['referenced_by'] > 5)].sort_values('referenced_by', ascending=False).head(20)
        st.dataframe(dorm[['id', 'title', 'last_referenced_year', 'referenced_by']].rename(columns={
            'id': 'ID', 'title': 'Titolo', 'last_referenced_year': 'Ultimo riferimento', 'referenced_by': 'Riferimenti'
        }), use_container_width=True)

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        fig = px.pie(values=metrics['impact_level'].value_counts().values,
                     names=metrics['impact_level'].value_counts().index,
                     title="Impatto", color_discrete_sequence=px.colors.qualitative.Set2)
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        fig = px.pie(values=metrics['complexity_level'].fillna('simple').value_counts().values,
                     names=metrics['complexity_level'].fillna('simple').value_counts().index,
                     title="Complessità", color_discrete_sequence=px.colors.qualitative.Set3)
        st.plotly_chart(fig, use_container_width=True)


# ─── TAB: Raw ───────────────────────────────────────────────


def render_raw(con):
    st.subheader("⚙️ Dati grezzi")

    tab1, tab2, tab3 = st.tabs(["Nodi per sorgente", "Archi per relazione", "Top hub"])

    with tab1:
        result = con.execute("SELECT source, COUNT(*) as n FROM nodes GROUP BY source ORDER BY n DESC").fetchdf()
        fig = px.pie(result, values="n", names="source", color_discrete_sequence=px.colors.qualitative.Set2)
        fig.update_traces(textposition="inside", textinfo="percent+label")
        st.plotly_chart(fig, use_container_width=True)

    with tab2:
        result = con.execute("SELECT relation, COUNT(*) as n FROM edges GROUP BY relation ORDER BY n DESC").fetchdf()
        fig = px.bar(result, x="n", y="relation", orientation="h", color="n", color_continuous_scale="Blues")
        fig.update_layout(yaxis={"autorange": "reversed"})
        st.plotly_chart(fig, use_container_width=True)

    with tab3:
        result = con.execute("""
            SELECT source_id, COUNT(DISTINCT target_id) as degree, SUM(weight) as peso
            FROM edges
            WHERE source_id NOT LIKE 'senato:atto:%' AND source_id NOT LIKE 'senato:emend:%'
              AND source_id NOT LIKE 'senato:dib:%'
            GROUP BY source_id ORDER BY degree DESC LIMIT 15
        """).fetchdf()
        enriched = []
        for _, row in result.iterrows():
            sid = row["source_id"]
            node = con.execute("SELECT title FROM nodes WHERE id = ? LIMIT 1", [sid]).fetchone()
            enriched.append({"label": (node[0] or sid)[:50] if node else sid[:50],
                           "connections": row["degree"], "weight": row["peso"]})
        if enriched:
            df = pd.DataFrame(enriched)
            fig = px.bar(df, x="connections", y="label", orientation="h",
                        color="weight", color_continuous_scale="Oranges")
            fig.update_layout(yaxis={"autorange": "reversed"}, height=500)
            st.plotly_chart(fig, use_container_width=True)


if __name__ == "__main__":
    main()
