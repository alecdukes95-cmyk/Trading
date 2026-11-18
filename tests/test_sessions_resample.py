import pandas as pd

from edgelab.data.resample import resample_ohlcv
from edgelab.data.sessions import CRYPTO_SESSION, FOREX_SESSION, SessionConfig, in_window, market_open_mask, trading_day

from conftest import make_bars


def test_forex_trading_day_rolls_at_5pm_new_york_across_dst():
    # Winter: 17:00 NY = 22:00 UTC. Summer: 17:00 NY = 21:00 UTC.
    ts = pd.to_datetime(["2026-01-13 21:59", "2026-01-13 22:00", "2026-07-14 20:59", "2026-07-14 21:00"], utc=True)
    d = trading_day(ts, FOREX_SESSION)
    assert [x.strftime("%Y-%m-%d") for x in d] == ["2026-01-13", "2026-01-14", "2026-07-14", "2026-07-15"]


def test_sunday_open_belongs_to_monday():
    ts = pd.to_datetime(["2026-01-11 22:00"], utc=True)  # Sunday 17:00 NY
    assert trading_day(ts, FOREX_SESSION)[0].strftime("%A") == "Monday"


def test_forex_weekend_mask():
    ts = pd.to_datetime(["2026-01-16 21:55", "2026-01-16 22:00", "2026-01-17 12:00", "2026-01-18 21:55",
                         "2026-01-18 22:00"], utc=True)
    assert market_open_mask(ts, FOREX_SESSION).tolist() == [True, False, False, False, True]
    assert market_open_mask(ts, CRYPTO_SESSION).all()


def test_window_crossing_midnight():
    cfg = SessionConfig(window_tz="UTC", window_start="22:00", window_end="02:00")
    ts = pd.to_datetime(["2026-01-13 21:00", "2026-01-13 23:00", "2026-01-14 01:30", "2026-01-14 02:00"], utc=True)
    assert in_window(ts, cfg).tolist() == [False, True, True, False]


def test_resample_15m():
    df = make_bars([1, 2, 3, 4, 5, 6], start="2026-01-14 08:00", spread=0.0, volume=[1, 2, 3, 4, 5, 6])
    r = resample_ohlcv(df, 15, FOREX_SESSION)
    assert len(r) == 2
    first = r.iloc[0]
    assert first["open"] == 1 and first["close"] == 3 and first["high"] == 3 and first["low"] == 1
    assert first["volume"] == 6
    assert r.index[1] == pd.Timestamp("2026-01-14 08:15", tz="UTC")


def test_resample_daily_uses_trading_day():
    ts = pd.to_datetime(["2026-01-13 21:55", "2026-01-13 22:00", "2026-01-14 10:00"], utc=True)
    df = pd.DataFrame({"open": [1, 2, 3.], "high": [1, 5, 3.], "low": [1, 2, 0.5], "close": [1, 2, 3.],
                       "volume": [1, 1, 1.]}, index=ts)
    r = resample_ohlcv(df, 1440, FOREX_SESSION)
    assert len(r) == 2
    assert r.iloc[1]["open"] == 2 and r.iloc[1]["high"] == 5 and r.iloc[1]["low"] == 0.5
