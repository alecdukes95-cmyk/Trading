"""EdgeLab - start with:  streamlit run app.py   (or double-click run.bat on Windows)."""
import streamlit as st

from edgelab.ui import (page_backtest, page_chart, page_concepts, page_data, page_edge, page_glossary, page_home,
                        page_journal, page_learn, page_paper, page_position, page_settings, page_signals)
from edgelab.ui.state import ensure_samples

st.set_page_config(page_title="EdgeLab", page_icon="📈", layout="wide")
ensure_samples()

pages = {
    "Start": [st.Page(page_home.render, title="Start here", icon="🏠", default=True)],
    "Trade": [
        st.Page(page_signals.render, title="Live signals", icon="🔔", url_path="signals"),
        st.Page(page_paper.render, title="Paper trading", icon="🧾", url_path="paper"),
        st.Page(page_journal.render, title="Trade journal", icon="📒", url_path="journal"),
        st.Page(page_position.render, title="Position & margin", icon="🧮", url_path="position"),
    ],
    "Analyse": [
        st.Page(page_chart.render, title="Chart", icon="📈", url_path="chart"),
        st.Page(page_edge.render, title="Edge builder", icon="🛠️", url_path="edge"),
        st.Page(page_backtest.render, title="Backtests & Monte Carlo", icon="🎲", url_path="backtest"),
    ],
    "Learn": [
        st.Page(page_learn.render, title="Replay & quiz", icon="🎓", url_path="learn"),
        st.Page(page_concepts.render, title="Concepts library", icon="💡", url_path="concepts"),
        st.Page(page_glossary.render, title="Glossary", icon="📖", url_path="glossary"),
    ],
    "Setup": [
        st.Page(page_data.render, title="Data", icon="📥", url_path="data"),
        st.Page(page_settings.render, title="Settings", icon="⚙️", url_path="settings"),
    ],
}
st.navigation(pages, expanded=True).run()
st.sidebar.caption("Educational tool. No live trading or order execution.")
