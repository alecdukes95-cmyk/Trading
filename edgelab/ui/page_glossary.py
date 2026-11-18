import streamlit as st

from edgelab.learning.glossary import CATEGORIES, GLOSSARY, search


def render():
    st.title("Glossary")
    st.caption("Plain-English definitions. Each entry says what it is, why traders care, and the common trap.")
    q = st.text_input("Search", placeholder="e.g. VWAP, spread, drawdown")
    hits = search(q)
    if not hits:
        st.info("No matching terms.")
        return
    cats = [c for c in CATEGORIES if any(t.category == c for t in hits)]
    tabs = st.tabs(cats)
    for tab, cat in zip(tabs, cats):
        with tab:
            for t in hits:
                if t.category == cat:
                    with st.expander(t.name, expanded=bool(q)):
                        st.markdown(t.markdown())


def term_expanders(names, title="Words used above"):
    names = [n for n in names if n in GLOSSARY]
    if not names:
        return
    st.markdown(f"**{title}**")
    cols = st.columns(min(3, len(names)))
    for i, n in enumerate(names):
        with cols[i % len(cols)].expander(n):
            st.markdown(GLOSSARY[n].markdown())
