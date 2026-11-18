"""EdgeLab - start with:  streamlit run app.py   (or double-click run.bat on Windows)."""
import streamlit as st

from edgelab.ui import page_chart, page_data, page_glossary, page_home, page_settings
from edgelab.ui.state import ensure_samples

st.set_page_config(page_title="EdgeLab", page_icon="📈", layout="wide")
ensure_samples()

pages = [
    st.Page(page_home.render, title="Start here", icon="🏠", url_path="home", default=True),
    st.Page(page_chart.render, title="Chart", icon="📈", url_path="chart"),
    st.Page(page_data.render, title="Data", icon="📥", url_path="data"),
    st.Page(page_glossary.render, title="Glossary", icon="📖", url_path="glossary"),
    st.Page(page_settings.render, title="Settings", icon="⚙️", url_path="settings"),
]
st.navigation(pages).run()
st.sidebar.caption("Educational tool. No live trading or order execution.")
