"""Esplora atto — ricerca su search_keys + scheda, catena, Corte e Parlamento.

Centro di gravità della dashboard: la traduzione umana di
legal_search → legal_node(view=chain|jurisprudence|parliament).
"""

import plotly.graph_objects as go
import streamlit as st
from lab_connectors.formatters import fmt_num
from sources import (
    chain_edges,
    corte_panel,
    node_metrics,
    parliament_panel,
    partners,
    relation_counts,
    search_acts,
)

try:
    import networkx as nx
except ImportError:  # ego-network degradabile
    nx = None

st.title("🔍 Esplora atto")

q = st.text_input(
    "Cerca per URN, numero, id o titolo",
    placeholder="es. 231/2001 · 117/2017 · urn:nir:... · whistleblowing · art. 3",
)

if not q.strip():
    st.info(
        "Digita almeno 3 caratteri. Ranking su `search_keys` "
        "(stessa logica del MCP `legal_search`). "
        "Esempi: `231/2001`, `whistleblowing`, `art. 3`."
    )
    st.stop()

try:
    results = search_acts(q, limit=40)
except Exception as e:  # noqa: BLE001
    st.error(f"Ricerca fallita: {e}")
    st.stop()

if results.empty:
    st.warning("Nessun risultato nel mart.")
    st.stop()

labels = [
    f"{r.id} — {(r.title or '')[:80]}" for r in results.itertuples()
]
choice = st.selectbox(f"{len(results)} risultati (ranking search_keys)", labels)
sel = results.iloc[labels.index(choice)]
node_id = str(sel["id"])

st.divider()
m1, m2, m3, m4 = st.columns(4)
m1.metric("Tipo", sel["tipo"] or "—")
m2.metric("Anno", sel["anno"] if sel["anno"] is not None else "—")
m3.metric("Stato", sel["stato"] or "—")
m4.metric("Materia", sel["materia"] or "—")
st.markdown(f"**{sel['title'] or '(senza titolo)'}**")
st.code(node_id, language=None)

# ── Catena legislativa ─────────────────────────────────────────────
st.subheader("🔗 Catena legislativa")
st.caption(
    "Relazioni tipizzate (`attua_delega`, `diventa_legge`, `abroga`, "
    "`sostituisce`, …) — la stessa del MCP `legal_node(view=\"chain\")`."
)

try:
    chain_fwd = chain_edges(node_id, "forward", depth=2)
    chain_bwd = chain_edges(node_id, "backward", depth=2)
except Exception as e:  # noqa: BLE001
    st.warning(f"Catena non calcolabile: {e}")
    chain_fwd = chain_bwd = None

if chain_fwd is not None and chain_bwd is not None and chain_fwd.empty and chain_bwd.empty:
    st.info(
        "Nessuna relazione di catena tipizzata per questo nodo. "
        "Prova con un D.Lgs (es. `231/2001`, `117/2017`) o una legge-base."
    )
else:
    tab_out_c, tab_in_c, tab_tl = st.tabs(
        ["➡️ Catena in uscita", "⬅️ Catena in entrata", "🗓️ Timeline"]
    )
    with tab_out_c:
        st.caption(
            "Arco uscente: es. `attua_delega` = questo atto attua la delega "
            "(la delega è il target); `abroga` = questo atto abroga il target."
        )
        if chain_fwd.empty:
            st.caption("Nessuna relazione di catena in uscita.")
        else:
            st.dataframe(chain_fwd, hide_index=True, width="stretch")
    with tab_in_c:
        st.caption(
            "Arco entrante: es. `diventa_legge` = questa DDL è diventata "
            "l'atto selezionato; `abroga` = un altro atto abroga questo."
        )
        if chain_bwd.empty:
            st.caption("Nessuna relazione di catena in entrata.")
        else:
            st.dataframe(chain_bwd, hide_index=True, width="stretch")
    with tab_tl:
        # Timeline: x = anno, y = direzione; nodo centrale al centro
        fig = go.Figure()
        center_anno = sel["anno"] if sel["anno"] is not None else 0
        fig.add_trace(
            go.Scatter(
                x=[center_anno],
                y=[0],
                mode="markers+text",
                marker={"size": 18, "color": "#6366f1"},
                text=[(sel["title"] or node_id)[:40]],
                textposition="top center",
                name="Atto",
            )
        )
        if not chain_bwd.empty:
            fb = chain_bwd.dropna(subset=["anno"])
            if not fb.empty:
                fig.add_trace(
                    go.Scatter(
                        x=fb["anno"],
                        y=[-1] * len(fb),
                        mode="markers+text",
                        marker={"size": 10, "color": "#d97706"},
                        text=[(t or "")[:28] for t in fb["title"]],
                        textposition="bottom center",
                        customdata=fb[["relation", "other_id"]],
                        hovertemplate="%{text}<br>%{customdata[0]}<br>%{customdata[1]}<extra></extra>",
                        name="Entranti",
                    )
                )
        if not chain_fwd.empty:
            fn = chain_fwd.dropna(subset=["anno"])
            if not fn.empty:
                fig.add_trace(
                    go.Scatter(
                        x=fn["anno"],
                        y=[1] * len(fn),
                        mode="markers+text",
                        marker={"size": 10, "color": "#059669"},
                        text=[(t or "")[:28] for t in fn["title"]],
                        textposition="top center",
                        customdata=fn[["relation", "other_id"]],
                        hovertemplate="%{text}<br>%{customdata[0]}<br>%{customdata[1]}<extra></extra>",
                        name="Uscenti",
                    )
                )
        fig.update_layout(
            height=360,
            showlegend=True,
            margin={"t": 10, "b": 10, "l": 10, "r": 10},
            yaxis={"visible": False, "range": [-1.6, 1.6]},
            xaxis_title="Anno",
        )
        st.plotly_chart(fig, width="stretch")
        st.caption("Nodi senza anno nel mart non compaiono sulla timeline.")

# ── Metrics + relazioni ────────────────────────────────────────────
st.subheader("📈 Metrics e relazioni")
col_a, col_b = st.columns(2)
with col_a:
    st.markdown("**Conteggi per relazione**")
    try:
        st.dataframe(relation_counts(node_id), hide_index=True, width="stretch")
    except Exception as e:  # noqa: BLE001
        st.caption(f"Conteggi non disponibili: {e}")
with col_b:
    st.markdown("**Intelligenza (metrics)**")
    try:
        met = node_metrics(node_id)
        if not met.empty:
            st.dataframe(met, hide_index=True, width="stretch")
        else:
            st.caption("Nessuna metrica per questo nodo.")
    except Exception:  # noqa: BLE001
        st.caption("metrics non disponibile.")

# ── Corte Costituzionale ───────────────────────────────────────────
st.subheader("🏛️ Corte Costituzionale")
try:
    corte = corte_panel(node_id)
    has_corte = any(not df.empty for df in corte.values())
    if not has_corte:
        st.caption("Nessun legame `impugna` / `relatore_sentenza` per questo nodo.")
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Impugnata da (Corte → atto)**")
            if corte["impugnata_da"].empty:
                st.caption("—")
            else:
                st.dataframe(corte["impugnata_da"], hide_index=True, width="stretch")
        with c2:
            st.markdown("**Impugna (atto/sentenza → altri)**")
            if corte["impugna"].empty:
                st.caption("—")
            else:
                st.dataframe(corte["impugna"], hide_index=True, width="stretch")
        if not corte["relatore"].empty:
            st.markdown("**Relatore della sentenza**")
            st.dataframe(corte["relatore"], hide_index=True, width="stretch")
except Exception as e:  # noqa: BLE001
    st.caption(f"Pannello Corte non disponibile: {e}")

# ── Parlamento ─────────────────────────────────────────────────────
st.subheader("🏛️ Parlamento")
try:
    parl = parliament_panel(node_id)
    p1, p2 = st.columns([1, 2])
    with p1:
        if parl["n_emend"] is not None:
            st.metric("Emendamenti (emend_leg)", fmt_num(parl["n_emend"]))
        else:
            st.caption("emend_leg non disponibile.")
    with p2:
        if parl["firmatari"].empty and parl["relatore"].empty:
            st.caption("Nessun firmatario/relatore tipizzato per questo nodo.")
        else:
            if not parl["relatore"].empty:
                st.markdown("**Relatore**")
                st.dataframe(parl["relatore"], hide_index=True, width="stretch")
            if not parl["firmatari"].empty:
                st.markdown("**Firmatari (top)**")
                st.dataframe(parl["firmatari"], hide_index=True, width="stretch")
except Exception as e:  # noqa: BLE001
    st.caption(f"Pannello Parlamento non disponibile: {e}")

# ── Partner + ego-network ──────────────────────────────────────────
st.subheader("Partner (top per peso)")
try:
    out_partners = partners(node_id, "out")
    in_partners = partners(node_id, "in")
except Exception as e:  # noqa: BLE001
    st.error(f"Relazioni non caricate: {e}")
    st.stop()

tab_out, tab_in = st.tabs(["Out (cita/modifica)", "In (citato da)"])
with tab_out:
    st.dataframe(out_partners, hide_index=True, width="stretch")
with tab_in:
    st.dataframe(in_partners, hide_index=True, width="stretch")

with st.expander("🕸️ Ego-network 1-hop (top partner, layout forza)"):
    if nx is None:
        st.info(
            "Grafico non disponibile: `networkx` assente. "
            "`pip install networkx` nel venv per attivarlo."
        )
    elif out_partners.empty and in_partners.empty:
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
        fig.add_trace(
            go.Scatter(
                x=x_e,
                y=y_e,
                mode="lines",
                line={"width": 0.6, "color": "#94a3b8"},
                hoverinfo="none",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=[pos[n][0] for n in G.nodes()],
                y=[pos[n][1] for n in G.nodes()],
                mode="markers+text",
                text=[("★ " + n[:28]) if n == node_id else n[:28] for n in G.nodes()],
                textposition="top center",
                marker={
                    "size": [22 if n == node_id else 10 for n in G.nodes()],
                    "color": "#6366f1",
                },
                hovertext=[f"<b>{n}</b>" for n in G.nodes()],
                hoverinfo="text",
            )
        )
        fig.update_layout(
            height=520,
            showlegend=False,
            margin={"t": 10, "b": 10, "l": 10, "r": 10},
        )
        st.plotly_chart(fig, width="stretch")

st.caption("Disclaimer: dal mart legal-graph (non Normattiva live).")
