import json

import numpy as np
import pandas as pd
import pytest

from edgelab.analysis import add_indicators
from edgelab.data.providers import coinbase
from edgelab.data.sessions import FOREX_SESSION
from edgelab.data.store import Store
from edgelab.data.tradelog import build_trades, guess_trade_mapping, normalize_side
from edgelab.instruments import get_instrument
from edgelab.learning.narrative import session_story


# ------------------------------------------------------------------ trade log
def _raw_log():
    return pd.DataFrame({
        "Open Time": ["2026-01-14 08:05", "2026-01-14 09:00", "2026-01-15 10:00", "2026-01-15 11:00"],
        "Close Time": ["2026-01-14 08:45", "2026-01-14 09:30", "2026-01-15 10:20", "2026-01-15 10:00"],
        "Symbol": ["EURUSD", "USDJPY", "EURUSD", "EURUSD"],
        "Type": ["Buy", "Sell", "SELL", "buy"],
        "Volume": [10000, 10000, 10000, 10000],
        "Open Price": [1.1000, 150.00, 1.1000, 1.1],
        "Close Price": [1.1020, 149.50, 1.1010, 1.1],
        "Commission": [-1.0, 1.0, 0, 0],
        "S/L": [1.0990, 150.25, None, None],
    })


def test_trade_mapping_guess():
    m = guess_trade_mapping(_raw_log().columns)
    assert m["entry_time"] == "Open Time" and m["exit_time"] == "Close Time"
    assert m["side"] == "Type" and m["size"] == "Volume" and m["stop_price"] == "S/L"


def test_side_normalization():
    assert normalize_side("BUY") == "long" and normalize_side("sld") == "short" and normalize_side("??") is None


def test_pnl_conversion_r_multiple_and_bad_rows():
    res = build_trades(_raw_log(), guess_trade_mapping(_raw_log().columns))
    t = res.trades
    assert len(t) == 3  # the exit-before-entry row is rejected
    assert "exit_before_entry" in {i.code for i in res.issues}
    eur = t.iloc[0]
    assert eur["pnl"] == pytest.approx(0.0020 * 10000 - 1.0)        # $20 gross, $1 fee
    assert eur["r_multiple"] == pytest.approx(19.0 / 10.0)           # risked 10 pips = $10
    jpy = t.iloc[1]
    assert jpy["pnl"] == pytest.approx(0.50 * 10000 / 149.50 - 1.0)  # yen profit converted to USD
    short_eur = t.iloc[2]
    assert short_eur["pnl"] == pytest.approx(-10.0)
    assert np.isnan(short_eur["r_multiple"])


def test_trades_store_roundtrip(tmp_path):
    res = build_trades(_raw_log(), guess_trade_mapping(_raw_log().columns))
    st = Store(tmp_path / "t.db")
    assert st.save_trades(res.trades, "test") == 3
    back = st.load_trades("EURUSD")
    assert len(back) == 2 and back["entry_time"].iloc[0] == pd.Timestamp("2026-01-14 08:05", tz="UTC")


# ------------------------------------------------------------------ coinbase provider (offline)
class FakeResp:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.text = data, status, json.dumps(data)

    def json(self):
        return self._data


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append(params)
        start = pd.Timestamp(params["start"])
        end = pd.Timestamp(params["end"])
        g = params["granularity"]
        times = pd.date_range(start, end, freq=pd.Timedelta(seconds=g), inclusive="left")
        rows = [[int(t.timestamp()), 1.0, 3.0, 2.0, 2.5, 10.0] for t in times]
        return FakeResp(rows[::-1])  # Coinbase returns newest first


def test_coinbase_pagination_and_column_order():
    s = FakeSession()
    start = pd.Timestamp("2026-01-01", tz="UTC")
    df = coinbase.fetch_candles("BTC-USD", "5m", start, start + pd.Timedelta(hours=30), session=s, pause=0)
    assert len(s.calls) == 2                      # 360 candles / 300 per request
    assert len(df) == 360 and df.index.is_monotonic_increasing
    row = df.iloc[0]
    assert (row["open"], row["high"], row["low"], row["close"]) == (2.0, 3.0, 1.0, 2.5)


def test_coinbase_error_raises():
    class Bad(FakeSession):
        def get(self, url, params, timeout):
            return FakeResp({"message": "NotFound"}, 404)

    with pytest.raises(coinbase.ProviderError):
        coinbase.fetch_candles("XXX-USD", "5m", pd.Timestamp("2026-01-01", tz="UTC"),
                               pd.Timestamp("2026-01-02", tz="UTC"), session=Bad(), pause=0)


# ------------------------------------------------------------------ narrative
def _day_frame(path_prev, path_today):
    """Two forex trading days of 5m bars (winter: day starts 22:00 UTC)."""
    idx1 = pd.date_range("2026-01-12 22:00", periods=len(path_prev), freq="5min", tz="UTC")
    idx2 = pd.date_range("2026-01-13 22:00", periods=len(path_today), freq="5min", tz="UTC")
    closes = np.r_[path_prev, path_today]
    idx = idx1.append(idx2)
    opens = np.r_[closes[0], closes[:-1]]
    df = pd.DataFrame({"open": opens, "high": np.maximum(opens, closes) + 0.0001,
                       "low": np.minimum(opens, closes) - 0.0001, "close": closes, "volume": 100.0}, index=idx)
    return add_indicators(df, FOREX_SESSION)


def test_story_trend_day_and_breakout():
    prev = 1.10 + 0.002 * np.sin(np.linspace(0, 6, 288))
    today = np.linspace(1.1000, 1.1100, 288)
    df = _day_frame(prev, today)
    story = session_story(df, pd.Timestamp("2026-01-14"), get_instrument("EURUSD"), "UTC")
    text = " ".join(story.bullets)
    assert "trend day" in text
    assert "held above it" in text
    assert story.headline.endswith("up 105.6 pips")  # from the prior close 1.09944 to 1.11000


def test_story_failed_breakout_and_partial_day():
    prev = 1.10 + 0.002 * np.sin(np.linspace(0, 6, 288))
    today = np.r_[np.linspace(1.1000, 1.1040, 50), np.linspace(1.1040, 1.0995, 50)]
    df = _day_frame(prev, today)
    story = session_story(df, pd.Timestamp("2026-01-14"), get_instrument("EURUSD"), "UTC")
    text = " ".join(story.bullets)
    assert "failed breakout" in text
    assert "Incomplete day" in text and "incomplete" in story.headline
