"""Swing pivots and support/resistance levels.

A swing high at bar i is a high that is the highest of `left` bars before and `right` bars after.
It can only be *known* `right` bars later, so levels built "as of" bar t only use pivots
confirmed by bar t. That is what keeps these levels free of look-ahead.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Level:
    price: float
    kind: str          # "support" or "resistance" relative to the last close
    touches: int
    first_ts: pd.Timestamp
    last_ts: pd.Timestamp
    low: float         # zone bottom
    high: float        # zone top


def find_pivots(df: pd.DataFrame, left: int = 5, right: int = 5) -> pd.DataFrame:
    """Columns pivot_high / pivot_low hold the pivot price at the pivot bar (NaN elsewhere),
    and confirmed_at holds the index position where the pivot becomes known."""
    w = left + right + 1
    hmax = df["high"].rolling(w, min_periods=w).max().shift(-right)
    lmin = df["low"].rolling(w, min_periods=w).min().shift(-right)
    ph = df["high"].where(df["high"] >= hmax)
    pl = df["low"].where(df["low"] <= lmin)
    # avoid flagging consecutive equal highs/lows twice: keep the first of a run
    ph = ph.where(~(ph.notna() & ph.shift(1).notna() & (ph == ph.shift(1))))
    pl = pl.where(~(pl.notna() & pl.shift(1).notna() & (pl == pl.shift(1))))
    out = pd.DataFrame({"pivot_high": ph, "pivot_low": pl}, index=df.index)
    out["confirmed_at"] = np.arange(len(df)) + right
    return out


def support_resistance(df: pd.DataFrame, atr_value: float, as_of: int | None = None, left: int = 5,
                       right: int = 5, tol_atr: float = 0.5, max_levels: int = 6,
                       min_touches: int = 2, min_sep_atr: float = 1.5) -> list[Level]:
    """Cluster confirmed swing points into horizontal levels, using data up to position `as_of`."""
    if df.empty or not np.isfinite(atr_value) or atr_value <= 0:
        return []
    as_of = len(df) - 1 if as_of is None else as_of
    piv = find_pivots(df.iloc[: as_of + 1], left, right)
    known = piv["confirmed_at"] <= as_of
    pts = []
    for col in ("pivot_high", "pivot_low"):
        s = piv.loc[known, col].dropna()
        pts.extend(zip(s.to_numpy(), s.index))
    if not pts:
        return []
    pts.sort(key=lambda x: x[0])
    tol = tol_atr * atr_value
    clusters: list[list] = []
    for price, ts in pts:
        if clusters and price - np.mean([p for p, _ in clusters[-1]]) <= tol:
            clusters[-1].append((price, ts))
        else:
            clusters.append([(price, ts)])
    last_close = float(df["close"].iloc[as_of])
    last_ts = df.index[as_of]
    span = (last_ts - df.index[0]) / pd.Timedelta(seconds=1) or 1.0
    levels = []
    for c in clusters:
        if len(c) < min_touches:
            continue
        prices = [p for p, _ in c]
        times = [t for _, t in c]
        lp = float(np.mean(prices))
        levels.append(Level(lp, "support" if lp < last_close else "resistance", len(c), min(times), max(times),
                            float(min(prices)), float(max(prices))))
    # rank: more touches and more recent first, then keep the closest to price

    def score(lv: Level) -> float:
        recency = 1 - (last_ts - lv.last_ts) / pd.Timedelta(seconds=1) / span
        distance = abs(lv.price - last_close) / atr_value
        return lv.touches + 2 * recency - 0.05 * distance

    levels.sort(key=score, reverse=True)
    chosen: list[Level] = []
    for lv in levels:  # keep levels meaningfully apart so the chart isn't a wall of lines
        if all(abs(lv.price - c.price) >= min_sep_atr * atr_value for c in chosen):
            chosen.append(lv)
        if len(chosen) == max_levels:
            break
    return sorted(chosen, key=lambda lv: lv.price)
