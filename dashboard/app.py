"""Legal Knowledge Graph — Dashboard Streamlit.

Esplora il sistema giuridico italiano come grafo: ogni legge, sentenza,
emendamento e dibattito è collegato. Trova connessioni, scopri impugnazioni,
segui l'iter legislativo.
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
    """Get node metadata and all connected edges."""
    node = con.execute("""
        SELECT id, tipo, title, data, anno, source, length_chars, length_words, celex, vigente
        FROM nodes WHERE id = ? LIMIT 1
    """, [node_id]).fetchone()
    if not node:
        return None

    out_edges = con.execute("""
        SELECT relation, target_id, weight, source_year, target_year, evidence
        FROM edges WHERE source_id = ? ORDER BY weight DESC LIMIT 20
    """, [node_id]).fetchall()

    in_edges = con.execute("""
        SELECT relation, source_id, weight, source_year, target_year, evidence
        FROM edges WHERE target_id = ? ORDER BY weight DESC LIMIT 20
    """, [node_id]).fetchall()

    temporal = con.execute("""
        SELECT relation, target_id, weight, evidence
        FROM temporal WHERE source_id = ? LIMIT 10
    """, [node_id]).fetchall()

    return {
        "node": {
            "id": node[0], "tipo": node[1], "title": node[2],
            "data": node[3], "anno": node[4], "source": node[5],
            "length_chars": node[6], "length_words": node[7],
            "celex": node[8], "vigente": node[9],
        },
        "outgoing": [{"rel": e[0], "target": e[1], "weight": e[2], "year": e[3]} for e in out_edges],
        "incoming": [{"rel": e[0], "source": e[1], "weight": e[2], "year": e[3]} for e in in_edges],
        "temporal": [{"rel": e[0], "target": e[1], "evidence": e[3]} for e in temporal],
    }


def render_node_card(info):
    """Render a node detail card."""
    n = info["node"]
    vigente = "Vigente" if n["vigente"] else "Non vigente" if n["vigente"] is not None else "Sconosciuto"
    st.markdown(f"**{n['tipo']}** — `{n['id']}`")
    st.markdown(f"**{n['title'][:200] if n['title'] else 'Senza titolo'}**")
    cols = st.columns(4)
    cols[0].caption(f"Data: {n['data'] or '—'}")
    cols[1].caption(f"Anno: {n['anno'] or '—'}")
    cols[2].caption(f"Fonte: {n['source']}")
    cols[3].caption(f"Stato: {vigente}")

    if info["outgoing"]:
        st.markdown("**Collegamenti uscenti:**")
        for e in info["outgoing"][:10]:
            target_label = e["target"][:60]
            st.caption(f"→ `{e['rel']}` → {target_label} (peso {e['weight']:.0f})")

    if info["incoming"]:
        st.markdown("**Collegamenti entranti:**")
        for e in info["incoming"][:10]:
            source_label = e["source"][:60]
            st.caption(f"← `{e['rel']}` ← {source_label} (peso {e['weight']:.0f})")

    if info["temporal"]:
        st.markdown("**Archi temporali:**")
        for e in info["temporal"]:
            st.caption(f"⏰ `{e['rel']}` → {e['target'][:60]}")


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
    st.sidebar.markdown("**Fonti collegate**")
    sources = con.execute("""
        SELECT source, COUNT(*) as n FROM nodes
        GROUP BY source ORDER BY n DESC
    """).fetchall()
    for src, cnt in sources:
        st.sidebar.caption(f"• {src}: {cnt:,}")
    st.sidebar.markdown("---")
    st.sidebar.markdown("[GitHub](https://github.com/dataciviclab)")

    # ─── Tabs ─────────────────────────────────────────────────
    tab_intro, tab_cerca, tab_corte, tab_senato, tab_ue, tab_timeline, tab_overview = st.tabs([
        "🏠 Cos'è", "🔍 Cerca", "⚖️ Corte Costituzionale", "🏛️ Senato",
        "🇪🇺 UE", "📈 Timeline", "📊 Panoramica"
    ])

    with tab_intro:
        render_intro(con)
    with tab_cerca:
        render_search(con)
    with tab_corte:
        render_corte(con)
    with tab_senato:
        render_senato(con)
    with tab_ue:
        render_ue(con)
    with tab_timeline:
        render_timeline(con)
    with tab_overview:
        render_overview(con)


def render_intro(con):
    st.title("Legal Knowledge Graph")
    st.markdown("""
    **Il sistema giuridico italiano come grafo interrogabile.**

    Ogni legge, sentenza, emendamento e dibattito è collegato a tutto il resto.
    Questo grafo non è un chatbot che "sa le leggi" — è l'infrastruttura dati
    che permette di **ricostruire le connessioni** tra fonti del diritto.
    """)

    # Fatti interessanti casuali
    st.subheader("Cosa puoi scoprire")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("**Articolo più invocato in Corte**")
        r = con.execute("""
            SELECT target_id, COUNT(*) as n FROM edges
            WHERE relation = 'invoca_parametro'
            GROUP BY target_id ORDER BY n DESC LIMIT 1
        """).fetchone()
        if r:
            st.metric("Art. " + r[0].replace("costituzione:art:", ""), f"{r[1]:,} invocazioni")

    with col2:
        st.markdown("**Legge più citata**")
        r = con.execute("""
            SELECT source_id, COUNT(*) as n FROM edges
            WHERE relation = 'riferimento' AND source_id LIKE 'urn:nir:stato:legge%'
            GROUP BY source_id ORDER BY n DESC LIMIT 1
        """).fetchone()
        if r:
            short = r[0].split(";")[0].split(":")[-1] if ";" in r[0] else r[0][:30]
            st.metric(short, f"{r[1]:,.0f} riferimenti")

    with col3:
        st.markdown("**DDL con più emendamenti**")
        r = con.execute("""
            SELECT e.target_id, COUNT(*) as n FROM edges e
            WHERE e.relation = 'emendamento'
            GROUP BY e.target_id ORDER BY n DESC LIMIT 1
        """).fetchone()
        if r:
            st.metric(r[0][:30], f"{r[1]:,} emendamenti")

    st.divider()

    st.subheader("Come funziona")
    st.markdown("""
    | Tipo di dato | Fonte | Cosa collega |
    |---|---|---|
    | Leggi e decreti | italia-corpus | Riferimenti incrociati tra atti |
    | Costituzione | costituzione-italiana | Articoli citati nelle leggi e invocati in Corte |
    | Corte Costituzionale | costituzione-italiana | Sentenze che dichiarano incostituzionale |
    | Senato DDL | open-politica | Iter parlamentare → legge vigente |
    | Emendamenti | senato-akn | Emendamenti → DDL → legge |
    | Dibattito | senato-akn | Interventi in aula → senatore |
    | UE | EUR-Lex | Direttive e regolamenti recepiti |
    """)

    st.divider()
    st.subheader("Prova tu")
    st.markdown("Vai al tab **🔍 Cerca** per trovare un atto e vedere tutti i suoi collegamenti.")

    # Esempio rapido
    with st.expander("Esempio: legge 234/2012 (Partecipazione Italia UE)"):
        info = get_node_info(con, "urn:nir:stato:legge:2012-12-24;234")
        if info:
            render_node_card(info)


def render_search(con):
    st.subheader("🔍 Cerca nel grafo")

    query = st.text_input("Cerca per titolo o ID", placeholder="es. Codice Penale, art. 3, legge 234/2012")

    col1, col2, col3 = st.columns(3)
    with col1:
        tipo = st.selectbox("Tipo", ["", "LEGGE", "DECRETO-LEGGE", "DECRETO LEGISLATIVO",
                                      "DECRETO", "COSTITUZIONE", "DIRETTIVA", "REGOLAMENTO",
                                      "SENTENZA", "SENATORE"])
    with col2:
        anno_min = st.number_input("Da anno", value=0, min_value=0, max_value=2026)
    with col3:
        anno_max = st.number_input("A anno", value=0, min_value=0, max_value=2026)

    if query or tipo or anno_min > 0 or anno_max > 0:
        conditions = []
        params = []
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
            ORDER BY anno DESC NULLS LAST
            LIMIT 50
        """, params).fetchdf()

        st.write(f"**{len(result)} risultati**")
        st.dataframe(result, use_container_width=True)

        # Dettaglio nodo selezionato
        if len(result) > 0:
            st.divider()
            selected = st.selectbox(
                "Seleziona un nodo per vedere i collegamenti",
                result["id"].tolist(),
                format_func=lambda x: f"{x[:60]} — {result[result['id']==x]['title'].iloc[0][:40] if len(result[result['id']==x]) > 0 else ''}"
            )
            if selected:
                info = get_node_info(con, selected)
                if info:
                    render_node_card(info)


def render_corte(con):
    st.subheader("⚖️ Corte Costituzionale")
    st.markdown("La Corte dichiara incostituzionali norme che violano la Costituzione. Questo tab mostra quali articoli vanno più sotto processo.")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Articoli più invocati come parametro**")
        result = con.execute("""
            SELECT
                REPLACE(target_id, 'costituzione:art:', 'Art. ') as articolo,
                COUNT(*) as invocazioni,
                COUNT(DISTINCT source_id) as sentenze
            FROM edges WHERE relation = 'invoca_parametro'
            GROUP BY target_id ORDER BY invocazioni DESC LIMIT 15
        """).fetchdf()
        fig = px.bar(result, x="invocazioni", y="articolo", orientation="h",
                     color="sentenze", color_continuous_scale="Reds",
                     title="Top 15 articoli invocati")
        fig.update_layout(yaxis={"autorange": "reversed"}, height=400)
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.markdown("**Norme più impugnate**")
        result = con.execute("""
            SELECT
                target_id as norma,
                COUNT(*) as impugnazioni,
                COUNT(DISTINCT source_id) as sentenze
            FROM edges WHERE relation = 'impugna'
            GROUP BY target_id ORDER BY impugnazioni DESC LIMIT 15
        """).fetchdf()
        fig = px.bar(result, x="impugnazioni", y="norma", orientation="h",
                     color="sentenze", color_continuous_scale="Oranges",
                     title="Top 15 norme impugnate")
        fig.update_layout(yaxis={"autorange": "reversed"}, height=400)
        st.plotly_chart(fig, use_container_width=True)

    # Doppio binario: Corte vs Legislazione
    st.divider()
    st.subheader("Doppio binario: Corte vs Legislazione")
    st.markdown("Alcuni articoli sono citati sia nella legislazione sia nei giudizi della Corte. Quale pesa di più?")

    result = con.execute("""
        WITH leg AS (
            SELECT target_id, COUNT(*) as cit, COUNT(DISTINCT source_id) as leggi
            FROM edges WHERE relation = 'cita_costituzione'
            GROUP BY target_id
        ),
        corte AS (
            SELECT target_id, COUNT(*) as inv, COUNT(DISTINCT source_id) as sent
            FROM edges WHERE relation = 'invoca_parametro'
            GROUP BY target_id
        )
        SELECT
            COALESCE(c.target_id, l.target_id) as id,
            REPLACE(COALESCE(c.target_id, l.target_id), 'costituzione:art:', 'Art. ') as articolo,
            COALESCE(c.inv, 0) as invocazioni_corte,
            COALESCE(l.cit, 0) as citazioni_leggi,
            COALESCE(l.leggi, 0) as leggi_diverse
        FROM corte c
        FULL OUTER JOIN leg l ON c.target_id = l.target_id
        WHERE COALESCE(c.inv, 0) > 0 OR COALESCE(l.cit, 0) > 0
        ORDER BY (COALESCE(c.inv, 0) + COALESCE(l.cit, 0)) DESC
        LIMIT 20
    """).fetchdf()

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Invocato in Corte",
        x=result["articolo"], y=result["invocazioni_corte"],
        marker_color="#e74c3c"
    ))
    fig.add_trace(go.Bar(
        name="Citato in leggi",
        x=result["articolo"], y=result["citazioni_leggi"],
        marker_color="#3498db"
    ))
    fig.update_layout(
        barmode="group",
        title="Articoli: Corte (parametro) vs Legislazione (citazione)",
        xaxis_title="Articolo",
        yaxis_title="Conteggi",
        height=500,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(result.drop(columns=["id"]), use_container_width=True)


def render_senato(con):
    st.subheader("🏛️ Senato — Iter legislativo")
    st.markdown("Come un DDL diventa legge: dal progetto al Parlamento al testo vigente.")

    col1, col2 = st.columns(2)

    with col1:
        result = con.execute("""
            SELECT relation, COUNT(*) as n FROM edges
            WHERE relation IN ('diventa_legge', 'testo_atto', 'emendamento', 'intervento')
            GROUP BY relation ORDER BY n DESC
        """).fetchdf()
        fig = px.bar(result, x="n", y="relation", orientation="h",
                     title="Relazioni parlamentari", color="n",
                     color_continuous_scale="Greens")
        fig.update_layout(yaxis={"autorange": "reversed"}, height=300)
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        result = con.execute("""
            SELECT source, COUNT(*) as n FROM nodes
            WHERE source IN ('senato', 'senato_corpus', 'senato_emend', 'senato_dib')
            GROUP BY source ORDER BY n DESC
        """).fetchdf()
        fig = px.pie(result, values="n", names="source",
                     title="Nodi per fonte Senato",
                     color_discrete_sequence=px.colors.qualitative.Set3)
        st.plotly_chart(fig, use_container_width=True)

    # DDL più emendate
    st.divider()
    st.subheader("DDL più contestate")
    result = con.execute("""
        SELECT
            n.title as provvedimento,
            n.id as id,
            COUNT(DISTINCT e.source_id) as emendamenti,
            CASE WHEN bridge.target_id IS NOT NULL THEN 'Diventata legge' ELSE 'In attesa' END as stato
        FROM edges e
        JOIN nodes n ON e.target_id = n.id
        LEFT JOIN edges bridge ON bridge.source_id = n.id AND bridge.relation = 'diventa_legge'
        WHERE e.relation = 'emendamento'
        GROUP BY n.id, n.title, bridge.target_id
        ORDER BY emendamenti DESC
        LIMIT 15
    """).fetchdf()
    st.dataframe(result, use_container_width=True)

    # Esempio DDL → legge
    st.divider()
    st.subheader("Esempio: da DDL a legge vigente")
    result = con.execute("""
        SELECT
            ddl.title as ddl,
            ddl.id as ddl_id,
            leg.title as legge,
            leg.id as legge_id,
            bridge.evidence as titolo
        FROM edges bridge
        JOIN nodes ddl ON bridge.source_id = ddl.id
        JOIN nodes leg ON bridge.target_id = leg.id
        WHERE bridge.relation = 'diventa_legge'
        ORDER BY bridge.source_year DESC
        LIMIT 10
    """).fetchdf()
    st.dataframe(result, use_container_width=True)


def render_ue(con):
    st.subheader("🇪🇺 Diritto UE → Legislazione italiana")
    st.markdown("L'Italia recepisce direttive e attua regolamenti UE. Questo tab mostra come.")

    col1, col2 = st.columns(2)

    with col1:
        result = con.execute("""
            SELECT
                CASE
                    WHEN relation = 'recepisce_direttiva' THEN 'Recepisce direttiva'
                    WHEN relation = 'attua_regolamento' THEN 'Attua regolamento'
                    ELSE 'Collega UE'
                END as tipo,
                COUNT(*) as n
            FROM edges
            WHERE relation IN ('recepisce_direttiva', 'attua_regolamento', 'collega_ue')
            GROUP BY tipo
        """).fetchdf()
        fig = px.pie(result, values="n", names="tipo",
                     title="Tipi di collegamento UE",
                     color_discrete_sequence=["#2ecc71", "#3498db", "#9b59b6"])
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        result = con.execute("""
            SELECT source_year as anno, COUNT(*) as n
            FROM edges
            WHERE relation IN ('recepisce_direttiva', 'attua_regolamento')
            GROUP BY source_year ORDER BY source_year
        """).fetchdf()
        if not result.empty:
            fig = px.bar(result, x="anno", y="n",
                         title="Recepimenti per anno", color="n",
                         color_continuous_scale="Blues")
            st.plotly_chart(fig, use_container_width=True)

    # Top direttive ricepute
    st.divider()
    st.subheader("Direttive UE più recepite dall'Italia")
    result = con.execute("""
        SELECT
            en.title as direttiva,
            en.celex,
            COUNT(DISTINCT e.source_id) as atti_italiani,
            MIN(e.source_year) as primo_recepimento,
            MAX(e.source_year) as ultimo_recepimento
        FROM edges e
        JOIN nodes en ON e.target_id = en.id
        WHERE e.relation = 'recepisce_direttiva'
        GROUP BY en.title, en.celex
        ORDER BY atti_italiani DESC
        LIMIT 15
    """).fetchdf()
    st.dataframe(result, use_container_width=True)


def render_timeline(con):
    st.subheader("📈 Timeline legislativa")
    st.markdown("L'attività legislativa italiana nel tempo: quante leggi vengono modificate ogni anno.")

    result = con.execute("""
        SELECT source_year, COUNT(DISTINCT source_id) as atti,
               COUNT(DISTINCT target_id) as norme_modificate
        FROM temporal WHERE relation = 'modifica' AND source_year > 1990
        GROUP BY source_year ORDER BY source_year
    """).fetchdf()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=result["source_year"], y=result["norme_modificate"],
        mode="lines+markers", name="Norme modificate",
        line={"color": "#e74c3c", "width": 2},
        fill="tozeroy", fillcolor="rgba(231,76,60,0.1)"
    ))
    fig.add_trace(go.Scatter(
        x=result["source_year"], y=result["atti"],
        mode="lines+markers", name="Atti modificatori",
        line={"color": "#3498db", "width": 2},
        yaxis="y2"
    ))
    fig.update_layout(
        title="Attività legislativa 1991-2026",
        xaxis_title="Anno",
        yaxis={"title": "Norme modificate", "side": "left"},
        yaxis2={"title": "Atti modificatori", "side": "right", "overlaying": "y"},
        height=500,
    )
    st.plotly_chart(fig, use_container_width=True)

    # Entrata in vigore
    st.divider()
    st.subheader("Entrate in vigore per anno")
    result = con.execute("""
        SELECT CAST(regexp_extract(target_id, '(\d{4})-\d{2}-\d{2}', 1) AS INTEGER) as anno,
               COUNT(*) as atti
        FROM temporal
        WHERE relation = 'entra_in_vigore'
        GROUP BY anno ORDER BY anno
    """).fetchdf()
    if not result.empty:
        fig = px.bar(result, x="anno", y="atti",
                     title="Atti entrati in vigore per anno",
                     color="atti", color_continuous_scale="Greens")
        st.plotly_chart(fig, use_container_width=True)


def render_overview(con):
    st.subheader("📊 Panoramica completa")

    col1, col2 = st.columns(2)

    with col1:
        result = con.execute("""
            SELECT source, COUNT(*) as n FROM nodes GROUP BY source ORDER BY n DESC
        """).fetchdf()
        fig = px.pie(result, values="n", names="source", title="Nodi per sorgente",
                     color_discrete_sequence=px.colors.qualitative.Set2)
        fig.update_traces(textposition="inside", textinfo="percent+label")
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        result = con.execute("""
            SELECT relation, COUNT(*) as n FROM edges GROUP BY relation ORDER BY n DESC
        """).fetchdf()
        fig = px.bar(result, x="n", y="relation", orientation="h",
                     title="Archi per relazione", color="n",
                     color_continuous_scale="Blues")
        fig.update_layout(yaxis={"autorange": "reversed"})
        st.plotly_chart(fig, use_container_width=True)

    # Hub nodes
    st.subheader("Nodi più connessi (escluso Senato)")
    result = con.execute("""
        SELECT source_id, COUNT(DISTINCT target_id) as degree, SUM(weight) as peso
        FROM edges
        WHERE source_id NOT LIKE 'senato:atto:%'
          AND source_id NOT LIKE 'senato:emend:%'
          AND source_id NOT LIKE 'senato:dib:%'
        GROUP BY source_id ORDER BY degree DESC LIMIT 15
    """).fetchdf()

    enriched = []
    for _, row in result.iterrows():
        sid = row["source_id"]
        node = con.execute("SELECT tipo, title FROM nodes WHERE id = ? LIMIT 1", [sid]).fetchone()
        label = (node[1] or sid)[:50] if node else sid[:50]
        enriched.append({"label": label, "connections": row["degree"], "weight": row["peso"]})

    if enriched:
        df = pd.DataFrame(enriched)
        fig = px.bar(df, x="connections", y="label", orientation="h",
                     title="Top 15 nodi per connessioni uscenti",
                     color="weight", color_continuous_scale="Oranges")
        fig.update_layout(yaxis={"autorange": "reversed"}, height=500)
        st.plotly_chart(fig, use_container_width=True)


if __name__ == "__main__":
    main()
