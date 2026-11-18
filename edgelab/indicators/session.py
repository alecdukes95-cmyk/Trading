"""Session-based levels: VWAP, opening range, prior-day high/low/close.

Look-ahead safety:
* VWAP at bar t uses bars from the start of the trading day up to and including t.
* The opening range only appears AFTER it has finished forming.
* Prior-day levels use the previous, completed trading day.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from edgelab.data.sessions import SessionConfig, in_window, minutes_into_window, trading_day, window_date


def vwap(df: pd.DataFrame, cfg: SessionConfig) -> pd.Series:
    """Volume-weighted average price, reset at the start of each trading day.

    If the data has no volume, falls back to an equal-weighted average of typical price.
    """
    day = trading_day(df.index, cfg)
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].fillna(0.0) if "volume" in df else pd.Series(0.0, index=df.index)
    if (vol <= 0).all():
        vol = pd.Series(1.0, index=df.index)
    pv = (tp * vol).groupby(day).cumsum()
    cv = vol.groupby(day).cumsum()
    out = (pv / cv.replace(0, np.nan)).ffill()
    out.name = "vwap"
    return out


def opening_range(df: pd.DataFrame, cfg: SessionConfig, minutes: int = 30) -> pd.DataFrame:
    """High and low of the first `minutes` of the session window.

    Values are NaN while the range is still forming and outside the window.
    """
    inw = in_window(df.index, cfg)
    miw = minutes_into_window(df.index, cfg)
    wd = window_date(df.index, cfg)
    in_or = inw & (miw < minutes)
    out = pd.DataFrame({"or_high": np.nan, "or_low": np.nan}, index=df.index)
    if not in_or.any():
        return out
    g = df.loc[in_or].groupby(wd[in_or])
    hi, lo = g["high"].max(), g["low"].min()
    # only sessions whose opening-range period was actually observed from the start
    first_min = pd.Series(miw[in_or], index=wd[in_or]).groupby(level=0).min()
    complete = first_min[first_min == 0].index
    after = inw & (miw >= minutes)
    keys = pd.Index(wd[after])
    ok = keys.isin(complete)
    rows = np.flatnonzero(after)[ok]
    out.iloc[rows, 0] = hi.reindex(keys[ok]).to_numpy()
    out.iloc[rows, 1] = lo.reindex(keys[ok]).to_numpy()
    return out


def prior_day_levels(df: pd.DataFrame, cfg: SessionConfig) -> pd.DataFrame:
    """Previous trading day's high, low and close, aligned to every bar of the current day."""
    day = trading_day(df.index, cfg)
    daily = df.groupby(day).agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
    prev = daily.shift(1)
    out = pd.DataFrame({"pdh": prev["high"].reindex(day).to_numpy(),
                        "pdl": prev["low"].reindex(day).to_numpy(),
                        "pdc": prev["close"].reindex(day).to_numpy()}, index=df.index)
    return out
