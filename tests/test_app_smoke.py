"""Start the real Streamlit app headlessly and check every page renders without an exception."""
import pytest

from edgelab.data.sample import generate_bars
from edgelab.data.store import Store

PAGES = ["page_home", "page_signals", "page_chart", "page_backtest", "page_learn", "page_position", "page_data", "page_glossary", "page_settings"]


@pytest.fixture
def small_home(tmp_home, monkeypatch):
    import streamlit as stl

    stl.cache_resource.clear()   # cached Store/scanner from a previous test point at another folder
    stl.cache_data.clear()
    monkeypatch.setenv("EDGELAB_SAMPLE_SYMBOLS", "EURUSD,BTCUSD")
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


def test_position_page_interactions(small_home):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_string("from edgelab.ui import page_position\npage_position.render()", default_timeout=60)
    at.run()
    assert not at.exception
    assert any("Required margin" in m.label for m in at.metric)
    for sym in ("USDJPY", "GBPUSD", "USDCAD", "EURGBP"):
        at.selectbox[0].set_value(sym).run()
        assert not at.exception, sym


def test_settings_broker_switch(small_home):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_string("from edgelab.ui import page_settings\npage_settings.render()", default_timeout=60)
    at.run()
    broker_box = next(sb for sb in at.selectbox if sb.label == "Active broker")
    broker_box.set_value("IG").run()
    assert not at.exception
    from edgelab.config import load_settings

    assert load_settings().active_broker == "IG"


def test_signals_page_shows_simulated_alerts(small_home):
    import time

    from streamlit.testing.v1 import AppTest

    from edgelab.config import Settings, save_settings
    from edgelab.live.scanner import ScannerService

    save_settings(Settings(watchlist=["EURUSD"], scan_timeframes=["15m"], notify_desktop=False, alert_quality="all"))
    sc = ScannerService()
    sc.start("simulation", sim_days=3, sim_seconds=0.0)
    while sc.status.running:
        time.sleep(0.2)
    assert sc.status.alerts > 0
    at = AppTest.from_string("from edgelab.ui import page_signals\npage_signals.render()", default_timeout=90)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("SIMULATION" in e.label for e in at.expander)


def test_backtest_page_and_monte_carlo_button(small_home):
    from streamlit.testing.v1 import AppTest

    from edgelab.config import Settings, save_settings

    save_settings(Settings(watchlist=["EURUSD"]))
    at = AppTest.from_string("from edgelab.ui import page_backtest\npage_backtest.render()", default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any(m.label == "Trades" for m in at.metric)
    btn = next(b for b in at.button if "Run Monte Carlo simulation" in b.label)
    btn.click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("Chance of being up" in m.label for m in at.metric)


def test_monte_carlo_button_on_a_chart_pattern(small_home):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_string("from edgelab.ui import page_chart\npage_chart.render()", default_timeout=120)
    at.run()
    assert not at.exception
    btns = [b for b in at.button if "Monte Carlo for this setup" in b.label]
    assert btns, "the explain panel should offer a Monte Carlo button"
    btns[0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("Chance of being up" in m.label for m in at.metric)


def test_replay_trainer_full_round(small_home):
    from streamlit.testing.v1 import AppTest

    from edgelab.config import Settings, save_settings

    save_settings(Settings(watchlist=["EURUSD"]))
    at = AppTest.from_string("from edgelab.ui import page_learn\npage_learn.render()", default_timeout=120)
    at.run()
    next(b for b in at.button if b.label == "New replay").click().run()
    assert not at.exception, [e.value for e in at.exception]
    next(b for b in at.button if b.label.startswith("Next bar")).click().run()
    next(b for b in at.button if "Hint" in b.label).click().run()
    action = next(r for r in at.radio if r.label == "Action")
    action.set_value("long")
    next(b for b in at.button if "Lock in decision" in b.label).click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert any(m.label == "Your read" for m in at.metric)
    from edgelab.config import db_path
    from edgelab.data.store import Store

    assert len(Store(db_path()).load_attempts()) == 1
