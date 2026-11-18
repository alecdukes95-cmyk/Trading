import lzma
import struct

import numpy as np
import pandas as pd
import pytest

from edgelab.data.providers import dukascopy, live_source_ready
from edgelab.data.providers.mt5 import MT5Client
from edgelab.data.sessions import FOREX_SESSION


def bi5(rows, order="OCLH"):
    """Build a Dukascopy-style candle file. rows: (sec, open, high, low, close, volume) in real prices."""
    out = b""
    for sec, o, h, l, c, v in rows:
        vals = {"O": o, "H": h, "L": l, "C": c}
        p = [int(round(vals[k] * 1e5)) for k in order]
        out += struct.pack(">5if", sec, *p, v)
    return lzma.compress(out, format=lzma.FORMAT_ALONE)


def day_rows(base=1.1000, n=60):
    rows, c = [], base
    for k in range(n):
        o = c
        c = o + (0.0002 if k % 3 else -0.0001)
        rows.append((k * 60, o, max(o, c) + 0.0001, min(o, c) - 0.0001, c, 10.0 + k))
    return rows


class Resp:
    def __init__(self, status, content=b""):
        self.status_code, self.content = status, content


class FakeDuka:
    def __init__(self, files):
        self.files, self.urls = files, []

    def get(self, url, timeout=None):
        self.urls.append(url)
        return Resp(200, self.files[url]) if url in self.files else Resp(404)


def test_url_uses_zero_based_month():
    assert dukascopy.day_url("EURUSD", pd.Timestamp("2026-01-14")).endswith("/EURUSD/2026/00/14/BID_candles_min_1.bi5")
    assert "/2026/11/31/" in dukascopy.day_url("EURUSD", pd.Timestamp("2026-12-31"))


@pytest.mark.parametrize("order", ["OCLH", "OHLC"])
def test_parse_day_any_price_order(order):
    rows = day_rows()
    df = dukascopy.parse_day(bi5(rows, order), pd.Timestamp("2026-01-14"), 1e5)
    assert len(df) == 60 and df.index[0] == pd.Timestamp("2026-01-14 00:00", tz="UTC")
    first = rows[0]
    assert df.iloc[0][["open", "high", "low", "close"]].tolist() == pytest.approx(list(first[1:5]))
    assert df["volume"].iloc[5] == pytest.approx(15.0)


def test_fetch_skips_missing_days_and_weekend_filler_and_builds_5m():
    wed = pd.Timestamp("2026-01-14")
    files = {dukascopy.day_url("EURUSD", wed): bi5([(8 * 3600 + s, o, h, l, c, v) for s, o, h, l, c, v in day_rows()])}
    sun = pd.Timestamp("2026-01-18")   # weekend filler: flat zero-volume candles
    files[dukascopy.day_url("EURUSD", sun)] = bi5([(k * 60, 1.1, 1.1, 1.1, 1.1, 0.0) for k in range(10)])
    sess = FakeDuka(files)
    m1 = dukascopy.fetch("EURUSD", "2026-01-13", "2026-01-19", session=sess, workers=2)
    assert len(m1) == 60 and m1.index.min() == pd.Timestamp("2026-01-14 08:00", tz="UTC")
    assert not any("/2026/00/17/" in u for u in sess.urls)        # Saturdays aren't requested
    m5 = dukascopy.fetch_bars("EURUSD", 5, "2026-01-13", "2026-01-19", FOREX_SESSION, session=FakeDuka(files))
    assert len(m5) == 12 and m5["volume"].iloc[0] == pytest.approx(sum(10.0 + k for k in range(5)))


def test_unsupported_pair():
    with pytest.raises(dukascopy.DukascopyError):
        dukascopy.fetch("XYZABC", "2026-01-01", "2026-01-02", session=FakeDuka({}))


class FakeMT5:
    TIMEFRAME_M5, TIMEFRAME_M15 = 5, 15

    def __init__(self, names=("EURUSD.a",)):
        self.names, self.selected, self.calls = set(names), [], []

    def initialize(self):
        return True

    def last_error(self):
        return (0, "ok")

    def symbol_info(self, name):
        return object() if name in self.names else None

    def symbol_select(self, name, enable):
        self.selected.append(name)
        return True

    def symbols_get(self, pattern):
        key = pattern.strip("*")
        return [type("S", (), {"name": n})() for n in self.names if key in n]

    def copy_rates_range(self, sym, tf, a, b):
        self.calls.append((sym, tf))
        # server time (GMT+2 in winter): 10:00, 10:05, 10:10 -> 08:00 ... UTC
        base = int(pd.Timestamp("2026-01-14 10:00").timestamp())
        dt = np.dtype([("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
                       ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")])
        return np.array([(base + 300 * k, 1.1, 1.1005, 1.0995, 1.1001, 50 + k, 1, 0) for k in range(3)], dtype=dt)


def test_mt5_client_converts_server_time_and_resolves_suffix():
    fake = FakeMT5()
    cl = MT5Client("Europe/Athens", "", mt5_module=fake)
    df = cl.candles("EURUSD", "5m", pd.Timestamp("2026-01-14", tz="UTC"), pd.Timestamp("2026-01-15", tz="UTC"))
    assert fake.calls[0][0] == "EURUSD.a"                      # found the broker's suffixed name
    assert df.index[0] == pd.Timestamp("2026-01-14 08:00", tz="UTC")
    assert df["volume"].tolist() == [50, 51, 52]
    assert "order" not in " ".join(dir(cl)).lower()            # read-only client


def test_live_source_readiness_messages(monkeypatch):
    from edgelab.config import Settings

    monkeypatch.delenv("OANDA_API_KEY", raising=False)
    ok, msg = live_source_ready(Settings(live_source="oanda"))
    assert not ok and "free" in msg
    ok, msg = live_source_ready(Settings(live_source="mt5"))
    assert not ok and "MetaTrader" in msg                         # package isn't installed on Linux


def test_histdata_csv_import():
    from edgelab.data.loader import build_ohlcv, guess_mapping, guess_symbol, read_table

    txt = b"20240102 170000;1.103630;1.103800;1.103550;1.103710;0\n20240102 170100;1.103710;1.103900;1.103700;1.103850;0\n"
    raw = read_table(txt)
    res = build_ohlcv(raw, guess_mapping(raw.columns), "Etc/GMT+5")
    assert res.df.index[0] == pd.Timestamp("2024-01-02 22:00", tz="UTC") and not res.notes
    assert guess_symbol("DAT_ASCII_EURUSD_M1_2024.csv") == "EURUSD"
