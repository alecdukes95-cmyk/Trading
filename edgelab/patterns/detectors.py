"""Pattern detectors. Each returns Signals whose bar `i` is the bar that completes the pattern.

All conditions use bars <= i only, so a detector run on data truncated at bar i gives the same
signals up to i as one run on the full history (tested in tests/test_patterns.py).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from edgelab.analysis import add_indicators
from edgelab.data.resample import resample_ohlcv
from edgelab.data.sessions import SESSION_CODE_LABELS, SessionConfig, in_window, session_codes
from edgelab.indicators.core import ema
from edgelab.instruments import Instrument
from edgelab.patterns.base import Signal


@dataclass
class Context:
    df: pd.DataFrame
    inst: Instrument
    cfg: SessionConfig
    intraday: bool
    bar_minutes: float
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    atr: np.ndarray
    vol_ratio: np.ndarray
    in_window: np.ndarray
    htf_trend: np.ndarray       # +1 up, -1 down, 0 unclear (higher timeframe, completed bars only)
    htf_label: str
    has_volume: bool
    session: np.ndarray          # session bitmask per bar (see sessions.session_codes)

    def fmt_move(self, d: float) -> str:
        return self.inst.fmt_move(d, self.c[-1]).lstrip("+")


def _htf_trend(df: pd.DataFrame, cfg: SessionConfig, bar_minutes: float) -> tuple[np.ndarray, str]:
    """Trend of the next timeframe up (1h for intraday, weekly-ish for daily) using COMPLETED bars only."""
    if bar_minutes >= 1440:
        htf_min, label = 1440 * 5, "weekly"
        htf = df[["open", "high", "low", "close", "volume"]].resample("W-FRI", label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    else:
        htf_min = 60 if bar_minutes < 60 else 240
        label = "1-hour" if htf_min == 60 else "4-hour"
        htf = resample_ohlcv(df[["open", "high", "low", "close", "volume"]], htf_min, cfg)
    if len(htf) < 25:
        return np.zeros(len(df), dtype=int), label
    e = ema(htf["close"], 20)
    slope = e.diff()
    trend = np.where((htf["close"] > e) & (slope > 0), 1, np.where((htf["close"] < e) & (slope < 0), -1, 0))
    close_time = htf.index + pd.Timedelta(minutes=htf_min)
    t = pd.Series(trend, index=close_time)
    bar_close = df.index + pd.Timedelta(minutes=bar_minutes)
    pos = t.index.searchsorted(bar_close, side="right") - 1
    out = np.where(pos >= 0, t.to_numpy()[np.clip(pos, 0, None)], 0)
    return out.astype(int), label


def make_context(df: pd.DataFrame, cfg: SessionConfig, inst: Instrument, bar_minutes: float,
                 with_indicators: bool = True) -> Context:
    intraday = bar_minutes < 1440
    if with_indicators or "atr" not in df:
        df = add_indicators(df[["open", "high", "low", "close", "volume"]], cfg, intraday=intraday)
    atr = df["atr"].to_numpy(float)
    rng = (df["high"] - df["low"]).rolling(14, min_periods=1).mean().to_numpy(float)
    atr = np.where(np.isfinite(atr), atr, rng)
    v = df["volume"].fillna(0).to_numpy(float)
    has_vol = bool((v > 0).mean() > 0.5)
    med = pd.Series(v).rolling(50, min_periods=10).median().shift(1).to_numpy()
    vr = np.where(med > 0, v / np.where(med > 0, med, 1), np.nan)
    trend, label = _htf_trend(df, cfg, bar_minutes)
    return Context(df, inst, cfg, intraday, bar_minutes, df["open"].to_numpy(float), df["high"].to_numpy(float),
                   df["low"].to_numpy(float), df["close"].to_numpy(float), v, atr, vr,
                   in_window(df.index, cfg) if intraday else np.ones(len(df), bool), trend, label, has_vol,
                   session_codes(df.index) if intraday else np.zeros(len(df), int))


def _sig(ctx: Context, pattern: str, direction: str, i: int, stop_ref, **kw) -> Signal:
    return Signal(pattern, direction, int(i), ctx.df.index[i], float(ctx.c[i]),
                  None if stop_ref is None else float(stop_ref), **kw)


def _dedupe(idx: np.ndarray, gap: int) -> list[int]:
    out, last = [], -10 ** 9
    for i in idx:
        if i - last > gap:
            out.append(int(i))
            last = i
    return out


def _shift(a: np.ndarray, k: int, fill=np.nan) -> np.ndarray:
    out = np.full_like(a, fill, dtype=float)
    if k > 0:
        out[k:] = a[:-k]
    elif k < 0:
        out[:k] = a[-k:]
    else:
        out[:] = a
    return out


# ----------------------------------------------------------------------------------- candles
def engulfing(ctx: Context) -> list[Signal]:
    o, h, l, c, atr = ctx.o, ctx.h, ctx.l, ctx.c, ctx.atr
    o1, c1 = _shift(o, 1), _shift(c, 1)
    c6 = _shift(c, 7)
    body, body1 = np.abs(c - o), np.abs(c1 - o1)
    big = (body >= 0.8 * atr) & (body > body1)
    bull = (c1 < o1) & (c > o) & (o <= c1) & (c >= o1) & big & (c6 - c1 >= 1.5 * atr)
    bear = (c1 > o1) & (c < o) & (o >= c1) & (c <= o1) & big & (c1 - c6 >= 1.5 * atr)
    out = []
    for i in np.flatnonzero(bull):
        out.append(_sig(ctx, "engulfing", "long", i, min(l[i], l[i - 1]), zone=(i - 1, i, min(l[i], l[i - 1]), max(h[i], h[i - 1])),
                        reasons=[f"This green body ({ctx.fmt_move(body[i])}) completely covers the previous red body",
                                 f"It came after a decline of {ctx.fmt_move(c6[i] - c1[i])} over the prior 6 bars - "
                                 "buyers stepped in after sellers had been in control"]))
    for i in np.flatnonzero(bear):
        out.append(_sig(ctx, "engulfing", "short", i, max(h[i], h[i - 1]), zone=(i - 1, i, min(l[i], l[i - 1]), max(h[i], h[i - 1])),
                        reasons=[f"This red body ({ctx.fmt_move(body[i])}) completely covers the previous green body",
                                 f"It came after a rise of {ctx.fmt_move(c1[i] - c6[i])} over the prior 6 bars - "
                                 "sellers stepped in after buyers had been in control"]))
    return out


def pin_bar(ctx: Context, lookback: int = 10) -> list[Signal]:
    o, h, l, c, atr = ctx.o, ctx.h, ctx.l, ctx.c, ctx.atr
    rng = h - l
    body = np.abs(c - o)
    lower = np.minimum(o, c) - l
    upper = h - np.maximum(o, c)
    lo_prev = pd.Series(l).rolling(lookback).min().shift(1).to_numpy()
    hi_prev = pd.Series(h).rolling(lookback).max().shift(1).to_numpy()
    ok = (rng >= 0.8 * atr) & (body <= 0.3 * rng)
    bull = ok & (lower >= 0.6 * rng) & (l < lo_prev)
    bear = ok & (upper >= 0.6 * rng) & (h > hi_prev)
    out = []
    for i in np.flatnonzero(bull):
        out.append(_sig(ctx, "pin_bar", "long", i, l[i], entry_ref=h[i], levels={"Rejected low": float(l[i])},
                        reasons=[f"Lower wick is {lower[i] / rng[i] * 100:.0f}% of the candle - lower prices were rejected",
                                 f"It made a new {lookback}-bar low and then closed back near the top"]))
    for i in np.flatnonzero(bear):
        out.append(_sig(ctx, "pin_bar", "short", i, h[i], entry_ref=l[i], levels={"Rejected high": float(h[i])},
                        reasons=[f"Upper wick is {upper[i] / rng[i] * 100:.0f}% of the candle - higher prices were rejected",
                                 f"It made a new {lookback}-bar high and then closed back near the bottom"]))
    return out


# ----------------------------------------------------------------------------------- levels
def _first_per_day(mask: np.ndarray, day: np.ndarray) -> np.ndarray:
    idx = np.flatnonzero(mask)
    if len(idx) == 0:
        return idx
    d = day[idx]
    keep = np.r_[True, d[1:] != d[:-1]]
    return idx[keep]


def breakout_prior_day(ctx: Context) -> list[Signal]:
    if not ctx.intraday or "pdh" not in ctx.df:
        return []
    c, l, h = ctx.c, ctx.l, ctx.h
    pdh, pdl = ctx.df["pdh"].to_numpy(float), ctx.df["pdl"].to_numpy(float)
    day = ctx.df["day"].to_numpy()
    same_day = np.r_[False, day[1:] == day[:-1]]
    up = same_day & (c > pdh) & (_shift(c, 1) <= _shift(pdh, 1))
    dn = same_day & (c < pdl) & (_shift(c, 1) >= _shift(pdl, 1))
    out = []
    for i in _first_per_day(up, day):
        out.append(_sig(ctx, "breakout_pd", "long", i, l[i], levels={"Prior-day high": float(pdh[i])},
                        reasons=[f"Closed {ctx.fmt_move(c[i] - pdh[i])} above yesterday's high ({ctx.inst.fmt_price(pdh[i])})",
                                 "First close above it today"]))
    for i in _first_per_day(dn, day):
        out.append(_sig(ctx, "breakout_pd", "short", i, h[i], levels={"Prior-day low": float(pdl[i])},
                        reasons=[f"Closed {ctx.fmt_move(pdl[i] - c[i])} below yesterday's low ({ctx.inst.fmt_price(pdl[i])})",
                                 "First close below it today"]))
    return out


def range_breakout(ctx: Context, n: int | None = None, tight_atr: float = 3.5) -> list[Signal]:
    n = n or (24 if ctx.intraday else 20)
    c, h, l, atr = ctx.c, ctx.h, ctx.l, ctx.atr
    bh = pd.Series(h).rolling(n).max().shift(1).to_numpy()
    bl = pd.Series(l).rolling(n).min().shift(1).to_numpy()
    tight = (bh - bl) <= tight_atr * _shift(atr, 1)
    up = tight & (c > bh) & (_shift(c, 1) <= bh)
    dn = tight & (c < bl) & (_shift(c, 1) >= bl)
    out = []
    for direction, mask in (("long", up), ("short", dn)):
        for i in _dedupe(np.flatnonzero(mask), n):
            mid = (bh[i] + bl[i]) / 2
            stop = min(l[i], mid) if direction == "long" else max(h[i], mid)
            edge = bh[i] if direction == "long" else bl[i]
            out.append(_sig(ctx, "range_breakout", direction, i, stop, zone=(i - n, i - 1, float(bl[i]), float(bh[i])),
                            levels={"Box top" if direction == "long" else "Box bottom": float(edge)},
                            reasons=[f"The previous {n} bars were stuck in a tight {ctx.fmt_move(bh[i] - bl[i])} box "
                                     f"(only {(bh[i] - bl[i]) / atr[i - 1]:.1f}x the average bar range)",
                                     f"This bar closed {'above the top' if direction == 'long' else 'below the bottom'} of the box"]))
    return out


def opening_range_breakout(ctx: Context) -> list[Signal]:
    if not ctx.intraday or "or_high" not in ctx.df:
        return []
    c, l, h = ctx.c, ctx.l, ctx.h
    orh, orl = ctx.df["or_high"].to_numpy(float), ctx.df["or_low"].to_numpy(float)
    day = ctx.df["day"].to_numpy()
    ok = np.isfinite(orh) & ctx.in_window
    up = ok & (c > orh)
    dn = ok & (c < orl)
    out = []
    for direction, mask in (("long", up), ("short", dn)):
        for i in _first_per_day(mask, day):
            mid = (orh[i] + orl[i]) / 2
            stop = min(l[i], mid) if direction == "long" else max(h[i], mid)
            out.append(_sig(ctx, "orb", direction, i, stop, levels={"Opening range high": float(orh[i]),
                                                                    "Opening range low": float(orl[i])},
                            reasons=[f"First close {'above' if direction == 'long' else 'below'} today's opening range "
                                     f"({ctx.inst.fmt_price(orl[i])} - {ctx.inst.fmt_price(orh[i])}, "
                                     f"{ctx.fmt_move(orh[i] - orl[i])} wide)"]))
    return out


def failed_breakout(ctx: Context, k: int = 6) -> list[Signal]:
    if not ctx.intraday:
        return []
    c, h, l = ctx.c, ctx.h, ctx.l
    day = ctx.df["day"].to_numpy()
    out = []
    pairs = [("pdh", "Prior-day high", "short"), ("pdl", "Prior-day low", "long"),
             ("or_high", "Opening range high", "short"), ("or_low", "Opening range low", "long")]
    for col, label, direction in pairs:
        if col not in ctx.df:
            continue
        L = ctx.df[col].to_numpy(float)
        Lk = _shift(L, k + 1)
        ck = _shift(c, k + 1)
        if direction == "short":   # broke above, now back below
            ev = (c < L) & (_shift(c, 1) > _shift(L, 1)) & (ck <= Lk)
        else:
            ev = (c > L) & (_shift(c, 1) < _shift(L, 1)) & (ck >= Lk)
        same_day = np.r_[np.zeros(k + 1, bool), day[k + 1:] == day[:-(k + 1)]]
        ev &= same_day & np.isfinite(L)
        for i in _first_per_day(ev, day):
            seg = slice(i - k, i + 1)
            extreme = h[seg].max() if direction == "short" else l[seg].min()
            out.append(_sig(ctx, "failed_breakout", direction, i, extreme, levels={label: float(L[i])},
                            points=[(int(i - k + (np.argmax(h[seg]) if direction == 'short' else np.argmin(l[seg]))),
                                     float(extreme), "trap")],
                            reasons=[f"Price closed {'above' if direction == 'short' else 'below'} the {label.lower()} "
                                     f"({ctx.inst.fmt_price(L[i])}) but this bar closed back "
                                     f"{'below' if direction == 'short' else 'above'} it",
                                     f"Breakout traders who {'bought' if direction == 'short' else 'sold'} up to "
                                     f"{ctx.fmt_move(abs(extreme - L[i]))} beyond the level are now trapped"]))
    return out


# ----------------------------------------------------------------------------------- VWAP
def vwap_patterns(ctx: Context, m: int = 6) -> list[Signal]:
    if not ctx.intraday or "vwap" not in ctx.df:
        return []
    o, h, l, c, atr = ctx.o, ctx.h, ctx.l, ctx.c, ctx.atr
    vw = ctx.df["vwap"].to_numpy(float)
    day = ctx.df["day"].to_numpy()
    bars_in_day = pd.Series(1, index=range(len(c))).groupby(day).cumsum().to_numpy()
    diff = pd.Series(c - vw)
    all_below = (diff.rolling(m).max().shift(1) < 0).to_numpy()
    all_above = (diff.rolling(m).min().shift(1) > 0).to_numpy()
    mature = bars_in_day > 2 * m
    rng = h - l
    out = []
    reclaim = mature & all_below & (c > vw + 0.1 * atr)
    loss = mature & all_above & (c < vw - 0.1 * atr)
    for i in _dedupe(np.flatnonzero(reclaim), m):
        out.append(_sig(ctx, "vwap_reclaim", "long", i, l[i], levels={"VWAP": float(vw[i])},
                        reasons=[f"The previous {m} bars all closed below VWAP; this one closed "
                                 f"{ctx.fmt_move(c[i] - vw[i])} above it", "Buyers regained the day's average price"]))
    for i in _dedupe(np.flatnonzero(loss), m):
        out.append(_sig(ctx, "vwap_reclaim", "short", i, h[i], levels={"VWAP": float(vw[i])},
                        reasons=[f"The previous {m} bars all closed above VWAP; this one closed "
                                 f"{ctx.fmt_move(vw[i] - c[i])} below it", "Sellers took back the day's average price"]))
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    rej_s = mature & all_below & (h >= vw) & (c < vw) & (upper >= 0.5 * rng) & (rng >= 0.5 * atr)
    rej_l = mature & all_above & (l <= vw) & (c > vw) & (lower >= 0.5 * rng) & (rng >= 0.5 * atr)
    for i in _dedupe(np.flatnonzero(rej_s), m):
        out.append(_sig(ctx, "vwap_rejection", "short", i, h[i], entry_ref=l[i], levels={"VWAP": float(vw[i])},
                        reasons=["Price rallied up into VWAP from below, touched it, and was pushed back down",
                                 f"Upper wick is {upper[i] / rng[i] * 100:.0f}% of the candle"]))
    for i in _dedupe(np.flatnonzero(rej_l), m):
        out.append(_sig(ctx, "vwap_rejection", "long", i, l[i], entry_ref=h[i], levels={"VWAP": float(vw[i])},
                        reasons=["Price dipped into VWAP from above, touched it, and was pushed back up",
                                 f"Lower wick is {lower[i] / rng[i] * 100:.0f}% of the candle"]))
    return out


# ----------------------------------------------------------------------------------- flags
def flags(ctx: Context, pole: int = 6, flag_lengths=range(10, 2, -1), pole_atr: float = 3.0) -> list[Signal]:
    h, l, c, atr = ctx.h, ctx.l, ctx.c, ctx.atr
    hs, ls = pd.Series(h), pd.Series(l)
    n = len(c)
    found_bull = np.full(n, -1)
    found_bear = np.full(n, -1)
    for f in flag_lengths:
        fh = hs.rolling(f).max().shift(1).to_numpy()
        fl = ls.rolling(f).min().shift(1).to_numpy()
        c_pe, c_ps = _shift(c, f + 1), _shift(c, f + 1 + pole)
        a_pe = _shift(atr, f + 1)
        ph = hs.rolling(pole + 1).max().shift(f + 1).to_numpy()
        pl = ls.rolling(pole + 1).min().shift(f + 1).to_numpy()
        move_up = c_pe - c_ps
        move_dn = c_ps - c_pe
        bull = (move_up >= pole_atr * a_pe) & (ph - fl <= 0.5 * move_up) & (fh - fl <= 0.5 * move_up) & \
               (fh <= ph + 0.1 * a_pe) & (c > fh) & (_shift(c, 1) <= fh)
        bear = (move_dn >= pole_atr * a_pe) & (fh - pl <= 0.5 * move_dn) & (fh - fl <= 0.5 * move_dn) & \
               (fl >= pl - 0.1 * a_pe) & (c < fl) & (_shift(c, 1) >= fl)
        found_bull = np.where((found_bull < 0) & bull, f, found_bull)
        found_bear = np.where((found_bear < 0) & bear, f, found_bear)
    out = []
    for direction, found in (("long", found_bull), ("short", found_bear)):
        for i in _dedupe(np.flatnonzero(found > 0), 10):
            f = int(found[i])
            pe, ps = i - f - 1, i - f - 1 - pole
            fh, fl = float(h[pe + 1:i].max()), float(l[pe + 1:i].min())
            mv = abs(c[pe] - c[ps])
            key = "bull_flag" if direction == "long" else "bear_flag"
            measured = fh + mv if direction == "long" else fl - mv
            out.append(_sig(ctx, key, direction, i, fl if direction == "long" else fh,
                            zone=(pe + 1, i - 1, fl, fh),
                            levels={"Flag top" if direction == "long" else "Flag bottom": fh if direction == "long" else fl,
                                    "Measured move": float(measured)},
                            points=[(ps, float(c[ps]), "pole start"), (pe, float(c[pe]), "pole end")],
                            reasons=[f"Pole: a {ctx.fmt_move(mv)} {'rise' if direction == 'long' else 'drop'} in {pole} bars "
                                     f"({mv / atr[pe]:.1f}x the average bar range)",
                                     f"Flag: {f} bars of quiet {'pullback' if direction == 'long' else 'bounce'} "
                                     f"retracing only {((h[ps:pe + 1].max() - fl) if direction == 'long' else (fh - l[ps:pe + 1].min())) / mv * 100:.0f}% of the pole",
                                     f"This bar closed {'above the top' if direction == 'long' else 'below the bottom'} of the flag"]))
    return out


# ----------------------------------------------------------------------------------- structure
def structure(ctx: Context, k: int | None = None) -> tuple[list[Signal], list[tuple]]:
    """Swing labels and break-of-structure / change-of-character signals.

    Returns (signals, swings) where swings = [(pivot_i, confirm_i, price, 'HH'|'HL'|'LH'|'LL'|'H'|'L')].
    """
    k = k or (5 if ctx.intraday else 3)
    h, l, c = ctx.h, ctx.l, ctx.c
    n = len(c)
    w = 2 * k + 1
    hmax = pd.Series(h).rolling(w).max().shift(-k).to_numpy()
    lmin = pd.Series(l).rolling(w).min().shift(-k).to_numpy()
    is_ph = (h >= hmax) & np.isfinite(hmax)
    is_pl = (l <= lmin) & np.isfinite(lmin)
    swings, sigs = [], []
    last_h = prev_h = last_l = prev_l = None   # (i, price)
    broken_h = broken_l = True
    ph_idx, pl_idx = set(np.flatnonzero(is_ph).tolist()), set(np.flatnonzero(is_pl).tolist())
    for t in range(n):
        p = t - k
        if p >= 0 and p in ph_idx and (last_h is None or p != last_h[0]):
            lab = "H" if last_h is None else ("HH" if h[p] > last_h[1] else "LH")
            prev_h, last_h, broken_h = last_h, (p, float(h[p])), False
            swings.append((p, t, float(h[p]), lab))
        if p >= 0 and p in pl_idx and (last_l is None or p != last_l[0]):
            lab = "L" if last_l is None else ("HL" if l[p] > last_l[1] else "LL")
            prev_l, last_l, broken_l = last_l, (p, float(l[p])), False
            swings.append((p, t, float(l[p]), lab))
        if prev_h is None or prev_l is None:
            continue
        up = last_h[1] > prev_h[1] and last_l[1] > prev_l[1]
        down = last_h[1] < prev_h[1] and last_l[1] < prev_l[1]
        if not broken_h and c[t] > last_h[1]:
            broken_h = True
            if up or down:
                key = "bos" if up else "choch"
                sigs.append(_sig(ctx, key, "long", t, last_l[1], levels={"Swing high broken": last_h[1],
                                                                         "Last swing low": last_l[1]},
                                 points=[(last_h[0], last_h[1], "HH" if up else "LH"), (last_l[0], last_l[1], "HL" if up else "LL")],
                                 reasons=[("Uptrend: higher highs and higher lows" if up else
                                           "Downtrend so far: lower highs and lower lows"),
                                          f"This bar closed above the last swing high ({ctx.inst.fmt_price(last_h[1])})"]))
        if not broken_l and c[t] < last_l[1]:
            broken_l = True
            if up or down:
                key = "bos" if down else "choch"
                sigs.append(_sig(ctx, key, "short", t, last_h[1], levels={"Swing low broken": last_l[1],
                                                                          "Last swing high": last_h[1]},
                                 points=[(last_l[0], last_l[1], "LL" if down else "HL"), (last_h[0], last_h[1], "LH" if down else "HH")],
                                 reasons=[("Downtrend: lower highs and lower lows" if down else
                                           "Uptrend so far: higher highs and higher lows"),
                                          f"This bar closed below the last swing low ({ctx.inst.fmt_price(last_l[1])})"]))
    return sigs, swings


# ----------------------------------------------------------------------------------- volume / gaps
def volume_climax(ctx: Context, lookback: int = 12) -> list[Signal]:
    if not ctx.has_volume:
        return []
    o, h, l, c, atr, vr = ctx.o, ctx.h, ctx.l, ctx.c, ctx.atr, ctx.vol_ratio
    rng = h - l
    move = c - _shift(c, lookback)
    big = (vr >= 3) & (rng >= 1.5 * atr)
    buy_clx = big & (c > o) & (move >= 4 * atr)
    sell_clx = big & (c < o) & (-move >= 4 * atr)
    out = []
    for i in _dedupe(np.flatnonzero(buy_clx), lookback):
        out.append(_sig(ctx, "volume_climax", "short", i, h[i], entry_ref=l[i],
                        reasons=[f"Volume {vr[i]:.1f}x normal on a wide up-bar ({ctx.fmt_move(rng[i])})",
                                 f"After a {ctx.fmt_move(move[i])} rise over {lookback} bars - possible buying exhaustion"],
                        cautions=["Counter-trend idea: only valid if price now fails to go higher"]))
    for i in _dedupe(np.flatnonzero(sell_clx), lookback):
        out.append(_sig(ctx, "volume_climax", "long", i, l[i], entry_ref=h[i],
                        reasons=[f"Volume {vr[i]:.1f}x normal on a wide down-bar ({ctx.fmt_move(rng[i])})",
                                 f"After a {ctx.fmt_move(-move[i])} fall over {lookback} bars - possible selling exhaustion"],
                        cautions=["Counter-trend idea: only valid if price now fails to go lower"]))
    return out


def gap_and_go(ctx: Context, minutes_after: int = 30) -> list[Signal]:
    if not ctx.intraday:
        return []
    o, h, l, c = ctx.o, ctx.h, ctx.l, ctx.c
    idx = ctx.df.index
    gaps = np.flatnonzero((pd.Series(idx).diff() > pd.Timedelta(hours=12)).to_numpy())
    if len(gaps) == 0:
        return []
    daily = ctx.df.groupby("day").agg(hi=("high", "max"), lo=("low", "min"))
    adr = (daily["hi"] - daily["lo"]).rolling(10, min_periods=3).mean().shift(1)
    n_after = max(1, int(minutes_after / ctx.bar_minutes))
    out = []
    for g in gaps:
        t = g + n_after
        if t >= len(c):
            continue
        a = adr.get(ctx.df["day"].iloc[g], np.nan)
        gap = o[g] - c[g - 1]
        if not np.isfinite(a) or abs(gap) < 0.15 * a:
            continue
        seg = slice(g, t + 1)
        if gap > 0 and l[seg].min() > c[g - 1] and c[t] > o[g]:
            out.append(_sig(ctx, "gap_and_go", "long", t, l[seg].min(), entry_ref=h[seg].max(),
                            levels={"Friday close": float(c[g - 1]), "Weekly open": float(o[g])},
                            reasons=[f"Opened the week {ctx.fmt_move(gap)} above Friday's close",
                                     f"The first {minutes_after} minutes held above the open without filling the gap"]))
        elif gap < 0 and h[seg].max() < c[g - 1] and c[t] < o[g]:
            out.append(_sig(ctx, "gap_and_go", "short", t, h[seg].max(), entry_ref=l[seg].min(),
                            levels={"Friday close": float(c[g - 1]), "Weekly open": float(o[g])},
                            reasons=[f"Opened the week {ctx.fmt_move(-gap)} below Friday's close",
                                     f"The first {minutes_after} minutes held below the open without filling the gap"]))
    return out


DETECTORS = [engulfing, pin_bar, breakout_prior_day, range_breakout, opening_range_breakout, failed_breakout,
             vwap_patterns, flags, volume_climax, gap_and_go]


def add_context_notes(sig: Signal, ctx: Context) -> None:
    """Visual cues and cautions shared by all patterns."""
    i = sig.i
    if sig.direction in ("long", "short"):
        d = 1 if sig.direction == "long" else -1
        t = ctx.htf_trend[i]
        if t == d:
            sig.reasons.append(f"With the {ctx.htf_label} trend (price above a {'rising' if d > 0 else 'falling'} "
                               f"20-period average on the {ctx.htf_label} chart)".replace("above a falling", "below a falling"))
        elif t == -d:
            sig.cautions.append(f"Against the {ctx.htf_label} trend - counter-trend trades need more confirmation")
    vr = ctx.vol_ratio[i]
    if ctx.has_volume and np.isfinite(vr):
        if vr >= 1.5:
            sig.reasons.append(f"Volume {vr:.1f}x the recent average - real participation behind the bar")
        elif vr < 0.8:
            sig.cautions.append(f"Volume only {vr:.1f}x the recent average - weak participation")
    if ctx.intraday:
        lab = SESSION_CODE_LABELS[ctx.session[i]]
        if "overlap" in lab:
            sig.reasons.append(f"Formed during the {lab} - the most active part of the day")
        elif lab.startswith("Asia") or lab.startswith("outside"):
            sig.cautions.append(f"Formed {('during the ' + lab) if lab.startswith('Asia') else lab} - "
                                "thinner trading, more false moves")
        if not ctx.in_window[i]:
            sig.cautions.append("Outside your trading window")


def detect_all(df: pd.DataFrame, cfg: SessionConfig, inst: Instrument, bar_minutes: float,
               enabled: list | None = None) -> tuple[list[Signal], list[tuple], Context]:
    ctx = make_context(df, cfg, inst, bar_minutes)
    sigs: list[Signal] = []
    for det in DETECTORS:
        sigs.extend(det(ctx))
    s_sigs, swings = structure(ctx)
    sigs.extend(s_sigs)
    if enabled:
        sigs = [s for s in sigs if s.pattern in enabled or
                (s.pattern in ("bull_flag", "bear_flag") and "flags" in enabled)]
    for s in sigs:
        add_context_notes(s, ctx)
    sigs.sort(key=lambda s: (s.i, s.pattern))
    return sigs, swings, ctx
