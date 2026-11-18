"""Start the real Streamlit app headlessly and check every page renders without an exception."""
import pytest

from edgelab.data.sample import generate_bars
from edgelab.data.store import Store

PAGES = ["page_home", "page_chart", "page_data", "page_glossary", "page_settings"]


@pytest.fixture
def small_home(tmp_home):
    # Pre-seed a small sample so the test doesn't generate a full year of data
    from edgelab.config import db_path

    st = Store(db_path())
    for sym, ac in (("EURUSD", "forex"), ("BTCUSD", "crypto")):
        st.save_dataset(generate_bars(sym, start="2026-01-05", end="2026-02-07"), name=f"{sym} 5m (SAMPLE)",
                        symbol=sym, asset_class=ac, timeframe="5m", source="synthetic", is_sample=True)
    return tmp_home


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(small_home, page):
    from streamlit.testing.v1 import AppTest

    script = f"""
import streamlit as st
from edgelab.ui import {page}
from edgelab.ui.state import ensure_samples
ensure_samples()
{page}.render()
"""
    at = AppTest.from_string(script, default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]


def test_chart_page_daily_and_navigation(small_home):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_string("from edgelab.ui import page_chart\npage_chart.render()", default_timeout=60)
    at.run()
    at.button[0].click().run()  # previous day
    assert not at.exception
    at.sidebar.selectbox[1].set_value("1d").run()
    assert not at.exception
    at.sidebar.selectbox[1].set_value("15m").run()
    at.sidebar.multiselect[1].set_value(["volume", "rsi", "macd", "atr"]).run()
    assert not at.exception
