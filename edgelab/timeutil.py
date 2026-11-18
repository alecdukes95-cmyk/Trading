"""Timestamp helpers that work the same on pandas 2.x and 3.x.

All bars are stored as UTC epoch seconds of the bar's OPEN time.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_EPOCH = pd.Timestamp("1970-01-01", tz="UTC")


def ensure_utc_index(idx) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(idx)
    if idx.tz is None:
        return idx.tz_localize("UTC")
    return idx.tz_convert("UTC")


def to_epoch_seconds(idx) -> np.ndarray:
    """Convert a datetime index/series to int64 epoch seconds (resolution independent)."""
    idx = ensure_utc_index(idx)
    return np.asarray((idx - _EPOCH) // pd.Timedelta(seconds=1), dtype="int64")


def from_epoch_seconds(values) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(np.asarray(values, dtype="int64"), unit="s", utc=True))


def bar_minutes(idx) -> float | None:
    """Most common spacing between bars, in minutes (None if fewer than 2 bars)."""
    idx = pd.DatetimeIndex(idx)
    if len(idx) < 2:
        return None
    diffs = pd.Series(idx).diff().dropna()
    diffs = diffs[diffs > pd.Timedelta(0)]
    if diffs.empty:
        return None
    return diffs.mode().iloc[0] / pd.Timedelta(minutes=1)


def timeframe_label(minutes: float | None) -> str:
    if minutes is None:
        return "?"
    if minutes >= 1440:
        return "1d" if round(minutes / 1440) == 1 else f"{round(minutes / 1440)}d"
    if minutes >= 60 and minutes % 60 == 0:
        return f"{int(minutes // 60)}h"
    return f"{int(round(minutes))}m"


def timeframe_minutes(label: str) -> int:
    label = label.strip().lower()
    unit = label[-1]
    n = int(label[:-1])
    return n * {"m": 1, "h": 60, "d": 1440}[unit]
