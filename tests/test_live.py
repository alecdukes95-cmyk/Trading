import time

import numpy as np
import pandas as pd
import pytest

from edgelab.brokers import BUILTIN_BROKERS
from edgelab.config import Settings, save_settings
from edgelab.data.sessions import FOREX_SESSION
from edgelab.fx import FxConverter
from edgelab.instruments import get_instrument
from edgelab.live import alerts as al
from edgelab.live.scanner import ScannerService
from edgelab.patterns import analyse

EURUSD = get_instrument("EURUSD")


def _pin_frame(n=80, end=None, with_pin=True):
    end = end or pd.Timestamp("2026-01-14 10:00", tz="UTC")
    idx = pd.date_range(end=end, periods=n, freq="5min", tz="UTC")
    r = np.random.default_rng(1)
    c = 1.1 + np.cumsum(r.normal(0, 0.0001, n))
    o = np.r_[c[0], c[:-1]]
    h, l = np.maximum(o, c) + 0.0003, np.minimum(o, c) - 0.0003
    df = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 100.0}, index=idx)
    if with_pin:   # last bar: long upper wick to a new high, tiny body -> bearish pin bar
        last = df.index[-1]
        cc = df["close"].iloc[-2]
        df.loc[last, ["open", "high", "low", "close"]] = [cc, cc + 0.0040, cc - 0.0002, cc - 0.0001]
    return df


def test_payload_roundtrip_and_texts():
    df = _pin_frame()
    a = analyse(df, FOREX_SESSION, EURUSD, 5, BUILTIN_BROKERS["OANDA"])
    sig = [s for s in a.signals if s.pattern == "pin_bar" and s.i == len(df) - 1][0]
    s = Settings()
    pl = al.build_payload(sig, a.ctx, EURUSD, "5m", s, 3_000, FxConverter(), None, "test", "simulation")
    assert pl["plan"]["order_type"] == "SELL STOP" and pl["position"]["units"] == 10_000
    assert pl["position"]["currency"] == "GBP" and pl["position"]["risk"] > 0
    df2, sig2 = al.payload_frame_and_signal(pl)
    assert sig2.i == len(df2) - 1 and sig2.plan.entry == pl["plan"]["entry"]
    assert len(df2) <= al.CHART_BARS + 1
    txt = al.desktop_text(pl)
    assert txt.startswith("[SIMULATION] SELL STOP") and len(txt) < 400
    assert "EURUSD 5m" in al.headline(pl)


def test_filters():
    df = _pin_frame()
    a = analyse(df, FOREX_SESSION, EURUSD, 5, BUILTIN_BROKERS["OANDA"])
    sig = [s for s in a.signals if s.pattern == "pin_bar"][-1]
    s = Settings(alert_quality="all", notify_only_in_window=False)
    assert al.passes_filters(sig, a.ctx, s)
    sig.cautions = ["x"]
    assert not al.passes_filters(sig, a.ctx, Settings(alert_quality="strict", notify_only_in_window=False))
    assert not al.passes_filters(sig, a.ctx, Settings(enabled_patterns=["orb"], alert_quality="all",
                                                      notify_only_in_window=False))


def test_simulation_emits_alerts_without_look_ahead(tmp_home, monkeypatch):
    monkeypatch.setenv("EDGELAB_SAMPLE_SYMBOLS", "EURUSD")
    from edgelab.config import db_path
    from edgelab.data.sample import ensure_sample_data
    from edgelab.data.store import Store

    ensure_sample_data(Store(db_path()))
    save_settings(Settings(watchlist=["EURUSD"], scan_timeframes=["15m"], notify_desktop=False, alert_quality="all"))
    sc = ScannerService()
    sc.start("simulation", sim_days=4, sim_seconds=0.0)
    t = time.time()
    while sc.status.running and time.time() - t < 60:
        time.sleep(0.2)
    assert not sc.status.running and sc.status.alerts > 0, sc.status.errors
    alerts = sc.store.list_alerts(200, "simulation")
    assert len(alerts) == sc.status.alerts
    end = sc.status.sim_end
    for a in alerts:
        assert a["bar_time"] >= end - pd.Timedelta(days=4, minutes=15)   # only bars inside the replay window
        st = a["payload"]["stats"]
        if st:  # history counts only trades that had finished before the alert
            assert st["n_signals"] >= st["n_filled"]
    # replaying again doesn't duplicate alerts
    sc.start("simulation", sim_days=4, sim_seconds=0.0)
    while sc.status.running:
        time.sleep(0.2)
    assert len(sc.store.list_alerts(500, "simulation")) == len(alerts)


class FakeClient:
    def __init__(self, frames):
        self.frames = frames
        self.calls = 0

    def candles(self, sym, tf, start, end, price="M"):
        f = self.frames[min(self.calls, len(self.frames) - 1)]
        self.calls += 1
        return f


def test_live_scan_alerts_only_on_new_candles(tmp_home, monkeypatch):
    sent = []
    monkeypatch.setattr("edgelab.live.scanner.desktop_notify", lambda t, m: sent.append((t, m)) or True)
    s = Settings(watchlist=["EURUSD"], scan_timeframes=["5m"], alert_quality="all", notify_only_in_window=False)
    before = _pin_frame(n=80, with_pin=False).iloc[:-1]
    after = _pin_frame(n=80, with_pin=True)
    sc = ScannerService()
    client = FakeClient([before, after, after])
    sc._scan_live_one(client, "EURUSD", "5m", s, 3_000)   # first look: just remembers the newest candle
    assert sc.store.list_alerts(10) == []
    sc._scan_live_one(client, "EURUSD", "5m", s, 3_000)   # a new candle closed with a pin bar
    got = sc.store.list_alerts(10, "live")
    assert any(a["pattern"] == "pin_bar" for a in got)
    assert sent and "SELL STOP" in sent[0][1]
    sc._scan_live_one(client, "EURUSD", "5m", s, 3_000)   # same candle again: nothing new
    assert len(sc.store.list_alerts(10, "live")) == len(got)


def test_desktop_notify_never_raises(monkeypatch):
    from edgelab.live import notify

    calls = []

    class N:
        @staticmethod
        def notify(**kw):
            calls.append(kw)

    import plyer

    monkeypatch.setattr(plyer, "notification", N)
    assert notify.desktop_notify("t" * 100, "m" * 500)
    assert len(calls[0]["title"]) <= 63 and len(calls[0]["message"]) <= 250

    class Broken:
        @staticmethod
        def notify(**kw):
            raise RuntimeError("no notification backend")

    monkeypatch.setattr(plyer, "notification", Broken)
    assert notify.desktop_notify("t", "m") is False
    assert notify.beep_wav()[:4] == b"RIFF"
