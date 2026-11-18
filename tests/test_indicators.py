import numpy as np
import pandas as pd
import pytest

from edgelab.analysis import add_indicators
from edgelab.data.sample import generate_bars
from edgelab.data.sessions import FOREX_SESSION, CRYPTO_SESSION
from edgelab.indicators import (atr, ema, find_pivots, macd, opening_range, prior_day_levels, rma, rsi, sma,
                                support_resistance, true_range, vwap)

from conftest import make_bars


def test_sma_basic():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    out = sma(s, 3)
    assert out.isna().sum() == 2
    assert out.tolist()[2:] == [2.0, 3.0, 4.0]


def test_ema_seeded_with_sma_then_recursive():
    s = pd.Series([1, 2, 3, 4, 5, 6], dtype=float)
    out = ema(s, 3)
    alpha = 0.5
    assert out.iloc[:2].isna().all()
    assert out.iloc[2] == pytest.approx(2.0)                      # SMA of 1,2,3
    assert out.iloc[3] == pytest.approx(alpha * 4 + (1 - alpha) * 2.0)
    assert out.iloc[4] == pytest.approx(alpha * 5 + (1 - alpha) * out.iloc[3])


def test_rma_wilder():
    s = pd.Series([2, 4, 6, 8], dtype=float)
    out = rma(s, 2)
    assert out.iloc[1] == pytest.approx(3.0)
    assert out.iloc[2] == pytest.approx((3.0 * 1 + 6) / 2)
    assert out.iloc[3] == pytest.approx((out.iloc[2] * 1 + 8) / 2)


def test_short_series_is_all_nan():
    assert ema(pd.Series([1.0, 2.0]), 5).isna().all()
    assert rsi(pd.Series([1.0, 2.0, 3.0]), 14).isna().all()


def test_true_range_uses_gaps():
    df = pd.DataFrame({"open": [10, 13], "high": [11, 14], "low": [9, 12.5], "close": [10, 13.5]}, dtype=float)
    tr = true_range(df)
    assert tr.iloc[0] == pytest.approx(2.0)        # high-low on first bar
    assert tr.iloc[1] == pytest.approx(4.0)        # |14 - prev close 10| beats 14-12.5


def test_atr_constant_range():
    df = make_bars([1.0] * 30, spread=0.001)
    assert atr(df, 14).iloc[-1] == pytest.approx(0.002)


def test_rsi_extremes_and_known_value():
    up = pd.Series(np.arange(1, 40, dtype=float))
    assert rsi(up, 14).iloc[-1] == pytest.approx(100.0)
    down = pd.Series(np.arange(40, 1, -1, dtype=float))
    assert rsi(down, 14).iloc[-1] == pytest.approx(0.0)
    flat = pd.Series([5.0] * 30)
    assert rsi(flat, 14).iloc[-1] == pytest.approx(50.0)
    # alternating +2 / -1: avg gain/avg loss -> RSI near 100*2/3 once seeded on equal counts
    alt = pd.Series(np.cumsum([0] + [2, -1] * 20), dtype=float)
    val = rsi(alt, 2).iloc[-1]
    assert 0 < val < 100


def test_macd_is_ema_difference():
    s = pd.Series(np.sin(np.arange(100) / 5) + np.arange(100) * 0.01)
    m = macd(s)
    np.testing.assert_allclose(m["macd"].dropna(), (ema(s, 12) - ema(s, 26)).dropna())
    np.testing.assert_allclose(m["hist"].dropna(), (m["macd"] - m["signal"]).dropna())


def test_vwap_hand_computed_and_resets_each_day():
    idx = pd.to_datetime(["2026-03-02 22:00", "2026-03-02 22:05", "2026-03-02 23:00", "2026-03-03 00:05"], utc=True)
    df = pd.DataFrame({"open": [1, 2, 3, 4.], "high": [1, 2, 3, 4.], "low": [1, 2, 3, 4.], "close": [1, 2, 3, 4.],
                       "volume": [1, 3, 1, 2.]}, index=idx)
    v = vwap(df, CRYPTO_SESSION)  # day rolls at 00:00 UTC
    assert v.iloc[0] == pytest.approx(1.0)
    assert v.iloc[1] == pytest.approx((1 * 1 + 2 * 3) / 4)
    assert v.iloc[2] == pytest.approx((1 + 6 + 3) / 5)
    assert v.iloc[3] == pytest.approx(4.0)  # new day -> reset


def test_vwap_without_volume_falls_back_to_equal_weights():
    df = make_bars([1, 2, 3], volume=0.0, spread=0)
    v = vwap(df, CRYPTO_SESSION)
    # typical prices: 1, (2+1+2)/3, (3+2+3)/3 -> equal-weighted mean
    assert v.iloc[-1] == pytest.approx((1 + 5 / 3 + 8 / 3) / 3)


def test_opening_range_hidden_until_complete():
    # London window starts 08:00 Europe/London (= 08:00 UTC in winter). 30-min OR = six 5m bars.
    closes = [1.10, 1.11, 1.12, 1.09, 1.10, 1.105, 1.20, 1.21, 1.22]
    df = make_bars(closes, start="2026-01-14 08:00", spread=0.0)
    orr = opening_range(df, FOREX_SESSION, 30)
    assert orr.iloc[:6].isna().all().all()
    assert orr["or_high"].iloc[6] == pytest.approx(1.12)
    assert orr["or_low"].iloc[6] == pytest.approx(1.09)
    assert orr["or_high"].iloc[-1] == pytest.approx(1.12)  # later bars don't change it


def test_opening_range_requires_observing_the_start():
    df = make_bars([1.1] * 10, start="2026-01-14 08:10")  # first 10 minutes missing
    assert opening_range(df, FOREX_SESSION, 30).isna().all().all()


def test_prior_day_levels_use_previous_forex_day():
    # Two forex trading days separated by the 17:00 New York rollover (22:00 UTC in winter)
    idx = pd.to_datetime(["2026-01-13 21:00", "2026-01-13 21:55", "2026-01-13 22:00", "2026-01-13 22:05"], utc=True)
    df = pd.DataFrame({"open": [1, 1, 1, 1.], "high": [5, 6, 2, 3.], "low": [0.5, 0.4, 0.9, 0.8],
                       "close": [1, 1.5, 1, 1.], "volume": 1.0}, index=idx)
    p = prior_day_levels(df, FOREX_SESSION)
    assert p.iloc[:2].isna().all().all()
    assert p["pdh"].iloc[2] == 6 and p["pdl"].iloc[2] == 0.4 and p["pdc"].iloc[2] == 1.5


def test_pivots_and_confirmation_delay():
    highs = [1, 2, 3, 9, 3, 2, 1, 2, 3]
    df = pd.DataFrame({"open": highs, "high": highs, "low": [h - 1 for h in highs], "close": highs}, dtype=float)
    p = find_pivots(df, 2, 2)
    assert p["pivot_high"].iloc[3] == 9
    assert p["confirmed_at"].iloc[3] == 5
    assert p["pivot_low"].iloc[6] == 0  # low of 1 - 1


def test_support_resistance_clusters_repeated_touches():
    pattern = [1.00, 1.02, 1.05, 1.02, 1.00, 1.02, 1.05, 1.02, 1.00, 1.02, 1.05, 1.02, 1.00, 1.03]
    closes = np.repeat(pattern, 3)
    df = make_bars(closes, spread=0.0005)
    levels = support_resistance(df, atr_value=0.01, left=3, right=3, min_sep_atr=1.0)
    prices = [round(l.price, 2) for l in levels]
    assert any(abs(p - 1.05) < 0.01 for p in prices)
    assert any(abs(p - 1.00) < 0.01 for p in prices)
    assert all(l.touches >= 2 for l in levels)


@pytest.mark.parametrize("symbol,cfg", [("EURUSD", FOREX_SESSION), ("BTCUSD", CRYPTO_SESSION)])
def test_no_look_ahead_in_any_indicator(symbol, cfg):
    """Values computed with future bars present must equal values computed without them."""
    df = generate_bars(symbol, start="2026-01-05", end="2026-01-20")
    full = add_indicators(df, cfg)
    cut = len(df) * 2 // 3
    part = add_indicators(df.iloc[:cut], cfg)
    cols = [c for c in part.columns if c != "day"]
    pd.testing.assert_frame_equal(full.iloc[:cut][cols], part[cols], check_exact=False, rtol=1e-9)

    lv_full = support_resistance(full, 0.001, as_of=cut - 1)
    lv_part = support_resistance(part, 0.001)
    assert [round(l.price, 8) for l in lv_full] == [round(l.price, 8) for l in lv_part]
