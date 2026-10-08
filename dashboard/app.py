#!/usr/bin/env python3
"""Legal Graph · Dashboard Streamlit.

La vista umana del Legal Knowledge Graph: esplora atti, processi e
salute del sistema normativo. Per gli agenti c'è il MCP (legal_search /
legal_node); qui ci sono occhiere, grafici ed export.
"""

import streamlit as st
from lab_connectors.branding import apply_branding

st.set_page_config(
    page_title="Legal Graph · Dashboard",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_branding()

pages = {
    "": [
        st.Page("pages/01_Panoramica.py", title="Panoramica", icon="📊", default=True),
    ],
    "Analisi": [
        st.Page("pages/02_Esplora_atto.py", title="Esplora atto", icon="🔍"),
        st.Page("pages/03_Decretazione.py", title="Decretazione d'urgenza", icon="⚡"),
        st.Page("pages/04_Corte_Costituzionale.py", title="Corte Costituzionale", icon="🏛️"),
    ],
    "Sistema": [
        st.Page("pages/05_Sanita_sistema.py", title="Salute del sistema", icon="🩺"),
    ],
    "Strumenti": [
        st.Page("pages/06_SQL.py", title="Query SQL", icon="🧪"),
    ],
}

pg = st.navigation(pages, position="sidebar")

st.sidebar.markdown("---")
st.sidebar.caption(
    "Fonte: mart GCS `legal-graph/legal_graph/2026` — 6 tabelle, build lunedì + on push"
)
st.sidebar.caption(
    "Per agenti: MCP `legal-search` · [repo](https://github.com/dataciviclab/legal-graph)"
)
st.sidebar.caption("[DataCivicLab](https://dataciviclab.org/) · Dati pubblico dominio · Codice MIT")

pg.run()
