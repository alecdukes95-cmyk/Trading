import inspect

import numpy as np
import pandas as pd
import pytest

from edgelab.account import balance_history, summarise
from edgelab.data.providers import oanda
from edgelab.data.store import Store


# ------------------------------------------------------------------ fake OANDA server
class Resp:
    def __init__(self, status, data):
        self.status_code, self._d, self.text = status, data, str(data)

    def json(self):
        return self._d


class FakeOanda:
    """Mimics the v20 endpoints EdgeLab uses. Records every call so we can check they are all GETs."""

    def __init__(self, n_candles=12, token="good"):
        self.calls, self.n, self.token = [], n_candles, token
        self.start = pd.Timestamp("2026-01-14 08:00", tz="UTC")

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("GET", url, dict(params or {})))
        if headers.get("Authorization") != f"Bearer {self.token}":
            return Resp(401, {"errorMessage": "Insufficient authorization to perform request."})
        if url.endswith("/v3/accounts"):
            return Resp(200, {"accounts": [{"id": "101-004-1-001", "tags": []}]})
        if url.endswith("/summary"):
            return Resp(200, {"account": {"balance": "2500.5000", "NAV": "2490.0000", "unrealizedPL": "-10.5",
                                          "marginUsed": "284.7800", "marginAvailable": "2205.22", "currency": "GBP",
                                          "openTradeCount": 1, "marginCloseoutPercent": "0.5"}})
        if "/candles" in url:
            frm = pd.Timestamp(params["from"])
            times = [self.start + pd.Timedelta(minutes=5 * i) for i in range(self.n)]
            times = [t for t in times if t >= frm][: params["count"]]
            candles = []
            for i, t in enumerate(times):
                base = 1.1 + 0.0001 * ((t - self.start) / pd.Timedelta(minutes=5))
                c = {"time": t.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"), "volume": 10 + i,
                     "complete": True,
                     "bid": {k: f"{base - 0.00005:.5f}" for k in "ohlc"},
                     "ask": {k: f"{base + 0.00005:.5f}" for k in "ohlc"}}
                if "M" in params["price"]:
                    c["mid"] = {"o": f"{base:.5f}", "h": f"{base + 0.0002:.5f}", "l": f"{base - 0.0002:.5f}",
                                "c": f"{base:.5f}"}
                candles.append(c)
            if candles and times[-1] == self.start + pd.Timedelta(minutes=5 * (self.n - 1)):
                candles[-1]["complete"] = False  # the still-forming candle
            return Resp(200, {"instrument": "EUR_USD", "granularity": params["granularity"], "candles": candles})
        return Resp(404, {"errorMessage": "not found"})


def test_candles_paginate_parse_and_drop_incomplete(monkeypatch):
    monkeypatch.setattr(oanda, "MAX_COUNT", 5)
    fake = FakeOanda(n_candles=12)
    cl = oanda.OandaClient("good", "practice", session=fake, pause=0)
    df = cl.candles("EURUSD", "5m", fake.start, fake.start + pd.Timedelta(hours=2))
    assert len(df) == 11                                    # 12 candles, last one incomplete
    assert df.index.is_monotonic_increasing and str(df.index.tz) == "UTC"
    assert df["close"].iloc[0] == pytest.approx(1.1)
    assert df["high"].iloc[0] == pytest.approx(1.1002)
    assert len([c for c in fake.calls if "/candles" in c[1]]) == 3   # 5 + 5 + 2
    first = fake.calls[0][2]
    assert first["granularity"] == "M5" and first["dailyAlignment"] == 17
    assert first["alignmentTimezone"] == "America/New_York"
    assert fake.calls[0][1].endswith("/v3/instruments/EUR_USD/candles")
    assert fake.calls[0][1].startswith("https://api-fxpractice.oanda.com")


def test_spread_measured_from_bid_ask():
    fake = FakeOanda(n_candles=6)
    df = oanda.OandaClient("good", session=fake, pause=0).candles("EURUSD", "5m", fake.start,
                                                                  fake.start + pd.Timedelta(hours=1))
    stats = oanda.spread_stats(df, 0.0001, np.ones(len(df), bool))
    assert stats["median_all"] == pytest.approx(1.0) and stats["median_window"] == pytest.approx(1.0)


def test_mid_built_from_bid_ask_when_missing():
    fake = FakeOanda(n_candles=3)
    df = oanda.OandaClient("good", session=fake, pause=0).candles("EURUSD", "5m", fake.start,
                                                                  fake.start + pd.Timedelta(hours=1), price="BA")
    assert df["close"].iloc[0] == pytest.approx(1.1)


def test_account_summary_and_bad_token():
    s = oanda.OandaClient("good", "live", session=FakeOanda(), pause=0).account_summary()
    assert s.account_id == "101-004-1-001" and s.currency == "GBP"
    assert s.balance == pytest.approx(2500.5) and s.margin_used == pytest.approx(284.78)
    assert s.margin_closeout_pct == pytest.approx(50)
    with pytest.raises(oanda.OandaError, match="401"):
        oanda.OandaClient("wrong", session=FakeOanda(), pause=0).account_summary()
    with pytest.raises(oanda.OandaError):
        oanda.OandaClient("", session=FakeOanda())


def test_client_is_read_only():
    """The OANDA client must never be able to send anything but GET requests."""
    src = inspect.getsource(oanda)
    for verb in (".post(", ".put(", ".patch(", ".delete(", ".request("):
        assert verb not in src, verb
    fake = FakeOanda()
    cl = oanda.OandaClient("good", session=fake, pause=0)
    cl.account_summary()
    cl.candles("GBPUSD", "15m", fake.start, fake.start + pd.Timedelta(hours=1))
    assert fake.calls and all(c[0] == "GET" for c in fake.calls)
    assert oanda.to_oanda("GBPJPY") == "GBP_JPY"


# ------------------------------------------------------------------ running balance
def _trades(rows):
    return pd.DataFrame(rows, columns=["exit_time", "pnl", "pnl_currency"]).assign(
        exit_time=lambda d: pd.to_datetime(d["exit_time"], utc=True))


def _events(store):
    return store.load_balance_events()


def test_balance_updates_with_trades_and_cash_flows(tmp_path):
    st = Store(tmp_path / "a.db")
    st.add_balance_event(pd.Timestamp("2026-01-10 12:00", tz="UTC"), "set", 5_000, "GBP")
    st.add_balance_event(pd.Timestamp("2026-01-12 12:00", tz="UTC"), "deposit", 500, "GBP")
    st.add_balance_event(pd.Timestamp("2026-01-13 12:00", tz="UTC"), "withdrawal", 200, "GBP")
    st.add_balance_event(pd.Timestamp("2026-01-14 12:00", tz="UTC"), "adjustment", -3.5, "GBP")
    trades = _trades([["2026-01-09 10:00", 999.0, "GBP"],    # before the figure: already included
                      ["2026-01-11 10:00", 40.0, "GBP"],
                      ["2026-01-15 10:00", -16.85, "GBP"],
                      ["2026-01-16 10:00", 10.0, "USD"]])    # wrong currency: not counted, flagged
    s = summarise(_events(st), trades, "GBP", 10_000)
    assert s.baseline == 5_000
    assert s.flows == pytest.approx(500 - 200 - 3.5)
    assert s.realised_pnl == pytest.approx(40 - 16.85)
    assert s.balance == pytest.approx(5_000 + 296.5 + 23.15)
    assert s.n_trades == 2 and s.n_skipped == 1
    hist = balance_history(_events(st), trades, "GBP", 10_000)
    assert hist.iloc[0] == 5_000 and hist.iloc[-1] == pytest.approx(s.balance)


def test_new_balance_figure_or_sync_reanchors(tmp_path):
    st = Store(tmp_path / "a.db")
    st.add_balance_event(pd.Timestamp("2026-01-10", tz="UTC"), "set", 5_000, "GBP")
    trades = _trades([["2026-01-11", 100.0, "GBP"], ["2026-01-20", 50.0, "GBP"]])
    st.add_balance_event(pd.Timestamp("2026-01-15", tz="UTC"), "sync", 5_120, "GBP")   # OANDA says 5,120
    s = summarise(_events(st), trades, "GBP", 10_000)
    assert s.baseline_kind == "sync" and s.balance == pytest.approx(5_170)


def test_no_entries_falls_back_to_settings_figure():
    s = summarise(pd.DataFrame(columns=["id", "ts", "kind", "amount", "currency", "note", "time"]),
                  _trades([["2026-01-11", 25.0, "GBP"]]), "GBP", 10_000)
    assert s.balance == pytest.approx(10_025) and s.baseline_kind == "settings"


def test_balance_entered_now_beats_earlier_entry_in_same_minute(tmp_path):
    """Regression: a figure typed in 'now' must replace an automatic entry made seconds earlier."""
    from edgelab.ui.page_settings import _event_time

    st = Store(tmp_path / "a.db")
    st.add_balance_event(pd.Timestamp.now(tz="UTC") - pd.Timedelta(seconds=5), "set", 10_000, "GBP")
    now_uk = pd.Timestamp.now(tz="Europe/London")
    st.add_balance_event(_event_time(now_uk.date(), now_uk.time()), "set", 2_500, "GBP")
    st.add_balance_event(_event_time(now_uk.date(), None), "deposit", 500, "GBP")
    assert summarise(st.load_balance_events(), None, "GBP", 0).balance == pytest.approx(3_000)


class MultiAccount(FakeOanda):
    """Token that sees an empty sub-account first and the funded practice account second."""

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("GET", url, dict(params or {})))
        if url.endswith("/v3/accounts"):
            return Resp(200, {"accounts": [{"id": "101-004-1-002"}, {"id": "101-004-1-001"}]})
        if url.endswith("101-004-1-002/summary"):
            return Resp(200, {"account": {"balance": "0.0000", "NAV": "0", "currency": "GBP"}})
        return super().get(url, params, headers, timeout)


def test_sync_picks_the_funded_account_not_the_first():
    cl = oanda.OandaClient("good", session=MultiAccount(), pause=0)
    assert [a.balance for a in cl.account_summaries()] == [0.0, 2500.5]
    assert cl.account_summary().account_id == "101-004-1-001"
    assert cl.account_summary("101-004-1-002").balance == 0.0   # an explicit choice is respected


def test_clamp_keeps_widget_values_in_range():
    from edgelab.ui.page_settings import clamp

    assert clamp(0.0, 50.0, 1e7) == 50.0 and clamp(-20, 0, 10) == 0 and clamp(5, 0, 10) == 5
    assert clamp(None, 50.0, 100.0, 75.0) == 75.0 and clamp(float("nan"), 1, 2) == 1
