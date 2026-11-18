import io

import numpy as np
import pandas as pd
import pytest

from edgelab.data.loader import build_ohlcv, guess_mapping, guess_symbol, read_table
from edgelab.data.sample import generate_bars
from edgelab.data.sessions import FOREX_SESSION, market_open_mask
from edgelab.data.store import Store
from edgelab.data.timeparse import parse_timestamps
from edgelab.data.validate import clean_ohlcv, detect_weekly_open_offset, validate_ohlcv

from conftest import make_bars


# ------------------------------------------------------------------ loader
def test_metatrader_headerless_with_separate_date_time():
    text = "2026.01.14,08:00,1.10000,1.10050,1.09950,1.10020,120\n2026.01.14,08:05,1.10020,1.10080,1.10000,1.10060,98\n"
    raw = read_table(text.encode(), "EURUSD5.csv")
    m = guess_mapping(raw.columns)
    assert m["date"] == "date" and m["time"] == "time" and m["timestamp"] is None
    res = build_ohlcv(raw, m)
    assert res.df.index[1] == pd.Timestamp("2026-01-14 08:05", tz="UTC")
    assert res.df["volume"].tolist() == [120, 98]


def test_tradingview_style_epoch_seconds():
    text = "time,open,high,low,close,Volume\n1768377600,1.1,1.2,1.0,1.15,10\n1768377900,1.15,1.2,1.1,1.12,11\n"
    raw = read_table(text.encode())
    m = guess_mapping(raw.columns)
    assert m["timestamp"] == "time" and m["volume"] == "Volume"
    res = build_ohlcv(raw, m)
    assert res.df.index[0] == pd.Timestamp(1768377600, unit="s", tz="UTC")


def test_epoch_milliseconds_detected():
    pr = parse_timestamps(pd.Series([1768377600000, 1768377900000]))
    assert pr.ts[0] == pd.Timestamp(1768377600, unit="s", tz="UTC")


def test_semicolon_delimiter_and_offsets():
    text = "Date;Open;High;Low;Close\n2026-01-14T08:00:00+02:00;1;2;0.5;1.5\n"
    raw = read_table(text.encode())
    res = build_ohlcv(raw, guess_mapping(raw.columns), source_tz="America/New_York")
    assert res.df.index[0] == pd.Timestamp("2026-01-14 06:00", tz="UTC")  # offset wins over the tz setting


def test_dayfirst_auto_detected_from_unambiguous_day():
    s = pd.Series(["13/01/2026 08:00", "14/01/2026 08:00"])
    pr = parse_timestamps(s)
    assert pr.ts[0] == pd.Timestamp("2026-01-13 08:00", tz="UTC")


def test_local_timezone_converted_and_dst_gap_flagged():
    s = pd.Series(["2026-03-08 01:55", "2026-03-08 02:30", "2026-03-08 03:05"])  # 02:30 doesn't exist in NY
    pr = parse_timestamps(s, "America/New_York")
    assert pr.n_dst_problems == 1
    assert pr.ts[0] == pd.Timestamp("2026-03-08 06:55", tz="UTC")


def test_guess_symbol():
    assert guess_symbol("EURUSD_M5_2026.csv") == "EURUSD"
    assert guess_symbol("BTC-USD 5m.csv") == "BTCUSD"


def test_missing_price_mapping_raises():
    raw = pd.DataFrame({"t": ["2026-01-01"], "x": [1]})
    with pytest.raises(ValueError):
        build_ohlcv(raw, {"timestamp": "t", "open": None, "high": None, "low": None, "close": "x"})


# ------------------------------------------------------------------ validator
def _forex_sample():
    return generate_bars("EURUSD", start="2026-01-05", end="2026-01-24")


def test_clean_sample_has_no_errors_or_warnings():
    r = validate_ohlcv(_forex_sample(), "forex", FOREX_SESSION)
    assert r.ok
    assert not [i for i in r.issues if i.severity == "warning"]


def test_detects_injected_problems():
    df = _forex_sample()
    bad = df.copy()
    bad.iloc[100, bad.columns.get_loc("high")] = bad.iloc[100]["low"] - 0.01        # high below low
    bad.iloc[200, bad.columns.get_loc("close")] = np.nan                             # missing
    bad.iloc[300, bad.columns.get_loc("open")] = -1                                  # impossible
    bad = pd.concat([bad, bad.iloc[[400]]])                                          # duplicate
    bad = bad.drop(bad.index[1000:1100])                                             # gap
    spike_pos = 2000
    bad.iloc[spike_pos, bad.columns.get_loc("close")] *= 1.05                        # bad tick
    bad.iloc[spike_pos, bad.columns.get_loc("high")] = bad.iloc[spike_pos]["close"]
    r = validate_ohlcv(bad, "forex", FOREX_SESSION)
    codes = r.codes()
    for code in ("bad_ohlc", "nan_prices", "nonpositive", "duplicates", "unsorted", "gaps", "spikes"):
        assert code in codes, code
    assert not r.ok
    gap = next(i for i in r.issues if i.code == "gaps")
    assert gap.count == 100

    cleaned, log = clean_ohlcv(bad)
    assert cleaned.index.is_monotonic_increasing and not cleaned.index.duplicated().any()
    assert validate_ohlcv(cleaned, "forex", FOREX_SESSION).ok
    assert log


def test_detects_shifted_timezone_like_metatrader_server_time():
    df = _forex_sample()
    shifted = df.copy()
    shifted.index = shifted.index + pd.Timedelta(hours=7)  # GMT+2 broker time read as UTC
    off = detect_weekly_open_offset(shifted.index)
    assert off == pytest.approx(7, abs=0.5)
    assert "tz_offset" in validate_ohlcv(shifted, "forex", FOREX_SESSION).codes()


def test_split_like_jump_and_no_volume():
    df = make_bars(np.r_[np.linspace(100, 101, 60), np.linspace(50.5, 51, 60)], volume=0.0, spread=0.01)
    r = validate_ohlcv(df, "crypto")
    assert "split_like" in r.codes()
    assert "no_volume" in r.codes()


def test_empty_is_error():
    r = validate_ohlcv(pd.DataFrame(columns=["open", "high", "low", "close", "volume"]), "crypto")
    assert not r.ok


# ------------------------------------------------------------------ sample data + store
def test_sample_data_is_realistic_and_deterministic():
    a = generate_bars("EURUSD", start="2026-01-05", end="2026-01-31")
    b = generate_bars("EURUSD", start="2026-01-05", end="2026-01-31")
    pd.testing.assert_frame_equal(a, b)
    assert market_open_mask(a.index, FOREX_SESSION).all()  # no weekend bars
    assert (a["high"] >= a[["open", "close"]].max(axis=1)).all()
    assert (a["low"] <= a[["open", "close"]].min(axis=1)).all()
    assert (a["volume"] > 0).all()
    c = generate_bars("BTCUSD", start="2026-01-05", end="2026-01-12")
    assert len(c) == 7 * 288  # crypto trades every day


def test_store_roundtrip(tmp_path):
    st = Store(tmp_path / "t.db")
    df = generate_bars("USDJPY", start="2026-01-05", end="2026-01-07")
    ds = st.save_dataset(df, name="x", symbol="USDJPY", asset_class="forex", timeframe="5m")
    back = st.load_bars(ds)
    pd.testing.assert_frame_equal(df, back, check_freq=False, check_index_type=False, check_dtype=False)
    assert st.list_datasets().iloc[0]["n_bars"] == len(df)
    part = st.load_bars(ds, start=df.index[10], end=df.index[19])
    assert len(part) == 10
    st.delete_dataset(ds)
    assert st.list_datasets().empty


def test_bundled_messy_file_end_to_end():
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "sample_data" / "EURUSD_M5_messy_broker_time.csv"
    raw = read_table(path)
    m = guess_mapping(raw.columns)
    wrong_tz = validate_ohlcv(build_ohlcv(raw, m, "UTC").df, "forex", FOREX_SESSION)
    right_tz = validate_ohlcv(build_ohlcv(raw, m, "Europe/Athens").df, "forex", FOREX_SESSION)
    assert {"tz_offset", "duplicates", "bad_ohlc", "gaps", "spikes"} <= wrong_tz.codes()
    assert "tz_offset" not in right_tz.codes()
    cleaned, _ = clean_ohlcv(build_ohlcv(raw, m, "Europe/Athens").df)
    assert validate_ohlcv(cleaned, "forex", FOREX_SESSION).ok
