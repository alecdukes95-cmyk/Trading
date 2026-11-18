"""Synthetic sample data so every feature works before you load real data.

IMPORTANT: this data is a random process with realistic *texture* (volatility clustering,
busier London/New York hours, trend and range days, weekend gaps, tick volume). It contains
NO real market edge. Any pattern that "works" on it is luck - which makes it a good place to
learn how easily backtests fool people.
"""
from __future__ import annotations

import zlib

import numpy as np
import pandas as pd

from edgelab.data.sessions import DEFAULT_SESSIONS, market_open_mask, trading_day
from edgelab.instruments import SAMPLE_SYMBOLS, get_instrument

SAMPLE_START = "2025-11-03"
SAMPLE_END = "2026-10-31"  # a Saturday: forex ends at the Friday close


def _bump(h: np.ndarray, centre: float, width: float) -> np.ndarray:
    d = np.minimum(np.abs(h - centre), 24 - np.abs(h - centre))
    return np.exp(-0.5 * (d / width) ** 2)


def seasonality(hours_utc: np.ndarray, asset_class: str) -> np.ndarray:
    """Relative volatility by hour of day (UTC). Mean ~1."""
    if asset_class == "forex":
        s = 0.45 + 0.9 * _bump(hours_utc, 8.0, 1.2) + 1.1 * _bump(hours_utc, 13.75, 1.3) \
            + 0.4 * _bump(hours_utc, 10.5, 2.0) + 0.25 * _bump(hours_utc, 0.5, 1.5)
    else:
        s = 0.7 + 0.6 * _bump(hours_utc, 14.5, 2.5) + 0.2 * _bump(hours_utc, 8.0, 2.0)
    return s / s.mean()


def generate_bars(symbol: str, start: str = SAMPLE_START, end: str = SAMPLE_END, freq_min: int = 5,
                  seed: int | None = None, substeps: int = 5) -> pd.DataFrame:
    inst = get_instrument(symbol)
    cfg = DEFAULT_SESSIONS[inst.asset_class]
    if seed is None:
        seed = zlib.crc32(symbol.encode())
    rng = np.random.default_rng(seed)

    idx = pd.date_range(start, end, freq=pd.Timedelta(minutes=freq_min), tz="UTC", inclusive="left")
    idx = idx[market_open_mask(idx, cfg)]
    n = len(idx)
    bars_per_day = 1440 / freq_min

    hours = np.asarray(idx.hour + idx.minute / 60.0)
    season = seasonality(hours, inst.asset_class)

    day = trading_day(idx, cfg)
    codes, uniques = pd.factorize(day)
    nd = len(uniques)
    # Day-level volatility regime (clustering) and occasional trend days
    log_vol = np.zeros(nd)
    shocks = rng.normal(0, 0.22, nd)
    for i in range(1, nd):
        log_vol[i] = 0.88 * log_vol[i - 1] + shocks[i]
    trend = rng.choice([-1.0, 0.0, 1.0], p=[0.15, 0.70, 0.15], size=nd) * rng.uniform(0.4, 1.2, nd) * inst.daily_vol

    base = inst.daily_vol / np.sqrt(bars_per_day)
    sigma = base * season * np.exp(log_vol[codes] - 0.5 * 0.22 ** 2 / (1 - 0.88 ** 2))
    drift = trend[codes] / bars_per_day

    z = rng.standard_t(4, size=(n, substeps)) / np.sqrt(2.0)  # fat tails; t(4) has variance 2
    steps = z * (sigma[:, None] / np.sqrt(substeps)) + (drift[:, None] / substeps)

    gaps = (pd.Series(idx).diff() > pd.Timedelta(hours=1)).to_numpy()  # e.g. the forex weekend
    jump = np.where(gaps, rng.normal(0, inst.daily_vol * 0.25, n), 0.0)

    rowsum = steps.sum(axis=1)
    close_log = np.log(inst.typical_price) + np.cumsum(jump + rowsum)
    open_log = close_log - rowsum
    inner = open_log[:, None] + np.cumsum(steps, axis=1)
    high_log = np.maximum(open_log, inner.max(axis=1))
    low_log = np.minimum(open_log, inner.min(axis=1))

    d = inst.decimals
    o, h, l, c = (np.round(np.exp(x), d) for x in (open_log, high_log, low_log, close_log))
    h = np.maximum.reduce([h, o, c])
    l = np.minimum.reduce([l, o, c])

    activity = season ** 1.2 * (0.6 + 0.5 * np.minimum(np.abs(rowsum) / np.maximum(sigma, 1e-12), 4.0))
    vol = inst.base_volume * activity * rng.lognormal(0, 0.3, n)
    vol = np.round(vol) if inst.asset_class == "forex" else np.round(vol, 4)

    df = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": vol}, index=idx)
    df.index.name = "ts"
    return df


def ensure_sample_data(store, symbols=None, progress=None) -> list[int]:
    """Create the bundled synthetic datasets once (deterministic, ~1 year of 5-minute bars each)."""
    if store.has_samples():
        return []
    ids = []
    symbols = symbols or SAMPLE_SYMBOLS
    for i, sym in enumerate(symbols):
        if progress:
            progress(i / len(symbols), f"Creating sample data for {sym}...")
        inst = get_instrument(sym)
        df = generate_bars(sym)
        ids.append(store.save_dataset(df, name=f"{sym} 5m (SAMPLE - synthetic)", symbol=sym,
                                      asset_class=inst.asset_class, timeframe="5m", source="synthetic",
                                      is_sample=True,
                                      notes="Synthetic random data with realistic texture. Contains no real edge."))
    if progress:
        progress(1.0, "Sample data ready.")
    return ids
