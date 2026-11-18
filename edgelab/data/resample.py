"""Build bigger timeframes from smaller ones (e.g. 5m -> 15m -> 1h -> daily)."""
from __future__ import annotations

import pandas as pd

from edgelab.data.sessions import SessionConfig, trading_day

AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def resample_ohlcv(df: pd.DataFrame, minutes: int, cfg: SessionConfig) -> pd.DataFrame:
    """Resample OHLCV bars.

    Intraday: buckets aligned to the clock (a 15m bar covers :00-:15, labelled by its open time).
    Daily (minutes >= 1440): one bar per trading day as defined by the session config
    (17:00 New York rollover for forex), labelled with that day's date at 00:00 UTC.
    """
    if df.empty:
        return df.copy()
    if minutes >= 1440:
        day = trading_day(df.index, cfg)
        out = df.groupby(day).agg(AGG)
        out.index = pd.DatetimeIndex(out.index).tz_localize("UTC")
    else:
        out = df.resample(pd.Timedelta(minutes=minutes), label="left", closed="left", origin="epoch").agg(AGG)
        out = out.dropna(subset=["open"])
    out.index.name = "ts"
    return out
