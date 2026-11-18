"""Classic indicators, matching the definitions used by most charting platforms.

* SMA: simple average of the last n closes.
* EMA: exponential average, alpha = 2/(n+1), seeded with the SMA of the first n values.
* RMA (Wilder's smoothing): alpha = 1/n, seeded with the SMA of the first n values. Used by ATR and RSI.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def _seeded_ewm(s: pd.Series, n: int, alpha: float) -> pd.Series:
    s = pd.Series(s, dtype=float)
    out = pd.Series(np.nan, index=s.index)
    valid = np.flatnonzero(s.notna().to_numpy())
    if len(valid) < n:
        return out
    i0 = valid[0]
    vals = s.to_numpy(dtype=float).copy()
    seed_pos = i0 + n - 1
    seed = np.nanmean(vals[i0:seed_pos + 1])
    x = vals[seed_pos:].copy()
    x[0] = seed
    res = pd.Series(x).ewm(alpha=alpha, adjust=False).mean().to_numpy()
    out.iloc[seed_pos:] = res
    return out


def ema(s: pd.Series, n: int) -> pd.Series:
    return _seeded_ewm(s, n, 2.0 / (n + 1))


def rma(s: pd.Series, n: int) -> pd.Series:
    return _seeded_ewm(s, n, 1.0 / n)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1)
    return tr.max(axis=1, skipna=True)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Average True Range: how far price typically travels per bar (in price units)."""
    return rma(true_range(df), n)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's Relative Strength Index (0-100)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = rma(gain.iloc[1:], n).reindex(close.index)
    avg_loss = rma(loss.iloc[1:], n).reindex(close.index)
    rs = avg_gain / avg_loss
    out = 100 - 100 / (1 + rs)
    out = out.where(avg_loss != 0, 100.0)
    out = out.where(~((avg_loss == 0) & (avg_gain == 0)), 50.0)
    return out.where(avg_gain.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig}, index=close.index)
