"""No-code strategy rules: a Strategy is plain data (saved as JSON) that turns into entry signals.

Look-ahead rules
* Every condition is evaluated at the CLOSE of bar i using bars <= i only.
* The trade is entered at bar i+1 (its open, or a stop order beyond bar i) - never on bar i itself.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from edgelab.data.sessions import SESSION_CODE_LABELS
from edgelab.indicators.core import ema, rsi, sma

TRIGGERS = {
    "pattern": "A pattern from the pattern library completes",
    "cross": "Price closes across a line (VWAP, moving average, prior-day level, opening range)",
    "ema_cross": "A fast moving average crosses a slow one",
    "rsi": "RSI crosses a level",
    "breakout": "Close above the highest high / below the lowest low of the last N bars",
}
CROSS_LINES = {"vwap": "VWAP", "ema": "EMA (n)", "sma": "SMA (n)", "pd": "Prior-day high (long) / low (short)",
               "or": "Opening range high (long) / low (short)"}
STOP_TYPES = {"atr": "ATR multiple", "bar": "Beyond the signal bar", "pips": "Fixed pips",
              "pattern": "Pattern's own invalidation (pattern triggers only)"}
TARGET_TYPES = {"r": "Multiple of the risk (R)", "atr": "ATR multiple", "pips": "Fixed pips",
                "none": "No target (exit by time / session end)"}


@dataclass
class Strategy:
    name: str = "My strategy"
    direction: str = "both"                                   # long | short | both
    trigger: dict = field(default_factory=lambda: {"kind": "pattern", "patterns": ["orb"]})
    filters: dict = field(default_factory=lambda: {"hours": ["08:00", "17:00"], "weekdays": [0, 1, 2, 3, 4],
                                                   "sessions": [], "trend": "any", "trend_n": 200,
                                                   "min_vol_ratio": 0.0, "atr_pct": [0, 100], "rsi_range": [0, 100],
                                                   "max_trades_per_day": 3})
    entry: dict = field(default_factory=lambda: {"type": "next_open", "expiry_bars": 3})
    stop: dict = field(default_factory=lambda: {"type": "atr", "value": 1.5})
    target: dict = field(default_factory=lambda: {"type": "r", "value": 2.0})
    exits: dict = field(default_factory=lambda: {"max_bars": 48, "session_end": True, "breakeven_at_r": 0.0})

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "Strategy":
        d = json.loads(s)
        base = cls()
        for k in ("filters", "entry", "stop", "target", "exits"):   # the trigger is kept exactly as saved
            merged = dict(getattr(base, k))
            merged.update(d.get(k, {}))
            d[k] = merged
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})

    def describe(self) -> str:
        t = self.trigger
        k = t["kind"]
        if k == "pattern":
            from edgelab.patterns.base import PATTERNS

            trig = "when " + " or ".join(PATTERNS[p].name for p in t.get("patterns", []) if p in PATTERNS) + " completes"
        elif k == "cross":
            trig = f"when price closes across {CROSS_LINES.get(t.get('line'), t.get('line'))}" + (
                f" ({t.get('n', 20)})" if t.get("line") in ("ema", "sma") else "")
        elif k == "ema_cross":
            trig = f"when EMA {t.get('fast', 9)} crosses EMA {t.get('slow', 20)}"
        elif k == "rsi":
            trig = (f"when RSI({t.get('n', 14)}) crosses back {'up through' if t.get('mode', 'reversal') == 'reversal' else 'above'} "
                    f"{t.get('lower', 30)} (long) / {'down through' if t.get('mode', 'reversal') == 'reversal' else 'below'} "
                    f"{t.get('upper', 70)} (short)")
        else:
            trig = f"on a close beyond the last {t.get('n', 20)}-bar high/low"
        f = self.filters
        st, tg, ex = self.stop, self.target, self.exits
        parts = [f"{self.direction.title()} trades {trig}",
                 f"between {f['hours'][0]} and {f['hours'][1]} UK time"]
        if f.get("trend", "any") != "any":
            parts.append({"with_sma": f"only with the trend (price vs SMA {f.get('trend_n', 200)})",
                          "with_htf": "only with the higher-timeframe trend"}.get(f["trend"], ""))
        parts.append(f"entry {'at the next bar open' if self.entry['type'] == 'next_open' else 'on a stop beyond the signal bar'}")
        parts.append(f"stop {STOP_TYPES[st['type']].lower()}" + (f" {st['value']:g}" if st["type"] in ("atr", "pips") else ""))
        parts.append("no target" if tg["type"] == "none" else f"target {tg['value']:g} {tg['type'].upper()}")
        parts.append(f"exit after {ex['max_bars']} bars" + (" or at the session end" if ex.get("session_end") else ""))
        return ", ".join(p for p in parts if p) + "."


def _cross_up(x: np.ndarray, line: np.ndarray) -> np.ndarray:
    prev_x, prev_l = np.r_[np.nan, x[:-1]], np.r_[np.nan, line[:-1]]
    return (x > line) & (prev_x <= prev_l)


def _cross_dn(x: np.ndarray, line: np.ndarray) -> np.ndarray:
    prev_x, prev_l = np.r_[np.nan, x[:-1]], np.r_[np.nan, line[:-1]]
    return (x < line) & (prev_x >= prev_l)


def trigger_signals(strategy: Strategy, analysis) -> tuple[np.ndarray, np.ndarray, dict]:
    """Boolean arrays (long, short) of bars whose CLOSE triggers an entry, plus pattern stops by bar."""
    ctx = analysis.ctx
    df = ctx.df
    c = ctx.c
    n = len(c)
    t = strategy.trigger
    k = t["kind"]
    long_ = np.zeros(n, bool)
    short = np.zeros(n, bool)
    pstops: dict = {}
    if k == "pattern":
        pats = set(t.get("patterns", []))
        if "bull_flag" in pats or "bear_flag" in pats:
            pats |= {"bull_flag", "bear_flag"}
        for s in analysis.signals:
            if s.pattern in pats and s.direction in ("long", "short"):
                (long_ if s.direction == "long" else short)[s.i] = True
                pstops[(s.i, s.direction)] = s.stop_ref
    elif k == "cross":
        line = t.get("line", "vwap")
        nn = int(t.get("n", 20))
        if line == "vwap" and "vwap" in df:
            lv = df["vwap"].to_numpy(float)
            long_, short = _cross_up(c, lv), _cross_dn(c, lv)
        elif line in ("ema", "sma"):
            lv = (ema if line == "ema" else sma)(df["close"], nn).to_numpy(float)
            long_, short = _cross_up(c, lv), _cross_dn(c, lv)
        elif line == "pd" and "pdh" in df:
            long_, short = _cross_up(c, df["pdh"].to_numpy(float)), _cross_dn(c, df["pdl"].to_numpy(float))
        elif line == "or" and "or_high" in df:
            long_, short = _cross_up(c, df["or_high"].to_numpy(float)), _cross_dn(c, df["or_low"].to_numpy(float))
    elif k == "ema_cross":
        f = ema(df["close"], int(t.get("fast", 9))).to_numpy(float)
        sl = ema(df["close"], int(t.get("slow", 20))).to_numpy(float)
        long_, short = _cross_up(f, sl), _cross_dn(f, sl)
    elif k == "rsi":
        r = rsi(df["close"], int(t.get("n", 14))).to_numpy(float)
        lo, hi = float(t.get("lower", 30)), float(t.get("upper", 70))
        lo_a, hi_a = np.full(n, lo), np.full(n, hi)
        if t.get("mode", "reversal") == "reversal":
            long_, short = _cross_up(r, lo_a), _cross_dn(r, hi_a)
        else:
            long_, short = _cross_up(r, hi_a), _cross_dn(r, lo_a)
    elif k == "breakout":
        nn = int(t.get("n", 20))
        hh = pd.Series(ctx.h).rolling(nn).max().shift(1).to_numpy()
        ll = pd.Series(ctx.l).rolling(nn).min().shift(1).to_numpy()
        long_, short = (c > hh) & (np.r_[np.nan, c[:-1]] <= np.r_[np.nan, hh[:-1]]), \
                       (c < ll) & (np.r_[np.nan, c[:-1]] >= np.r_[np.nan, ll[:-1]])
    long_ = np.nan_to_num(long_, nan=0).astype(bool)
    short = np.nan_to_num(short, nan=0).astype(bool)
    if strategy.direction == "long":
        short[:] = False
    elif strategy.direction == "short":
        long_[:] = False
    return long_, short, pstops


def filter_mask(strategy: Strategy, analysis, side: str) -> np.ndarray:
    """Which bars pass the strategy's filters for a trade in `side` direction (evaluated at the bar's close)."""
    ctx = analysis.ctx
    df = ctx.df
    f = strategy.filters
    n = len(ctx.c)
    ok = np.ones(n, bool)
    close_t = df.index + pd.Timedelta(minutes=ctx.bar_minutes)      # decisions happen at the bar's close
    if ctx.intraday:
        uk = close_t.tz_convert("Europe/London")
        mins = np.asarray(uk.hour * 60 + uk.minute)
        h0, h1 = (int(x.split(":")[0]) * 60 + int(x.split(":")[1]) for x in f.get("hours", ["00:00", "24:00"]))
        ok &= (mins > h0) & (mins <= h1) if h0 < h1 else ((mins > h0) | (mins <= h1))
        ok &= np.isin(np.asarray(uk.weekday), f.get("weekdays", [0, 1, 2, 3, 4, 5, 6]))
        if f.get("sessions"):
            labels = np.array(SESSION_CODE_LABELS)[ctx.session]
            ok &= np.isin(labels, f["sessions"])
    d = 1 if side == "long" else -1
    trend = f.get("trend", "any")
    if trend == "with_sma":
        m = sma(df["close"], int(f.get("trend_n", 200))).to_numpy(float)
        ok &= (ctx.c - m) * d > 0
    elif trend == "with_htf":
        ok &= ctx.htf_trend == d
    elif trend == "against_htf":
        ok &= ctx.htf_trend == -d
    mv = float(f.get("min_vol_ratio", 0) or 0)
    if mv > 0 and ctx.has_volume:
        ok &= np.nan_to_num(ctx.vol_ratio, nan=0) >= mv
    lo, hi = f.get("atr_pct", [0, 100])
    if lo > 0 or hi < 100:
        pct = atr_percentile(ctx.atr)
        ok &= (pct >= lo) & (pct <= hi)
    rlo, rhi = f.get("rsi_range", [0, 100])
    if rlo > 0 or rhi < 100:
        r = df["rsi"].to_numpy(float) if "rsi" in df else rsi(df["close"], 14).to_numpy(float)
        ok &= (r >= rlo) & (r <= rhi)
    return ok


def atr_percentile(atr: np.ndarray, window: int = 500) -> np.ndarray:
    """Where today's ATR sits within the previous `window` bars (0-100). Causal: uses past bars only."""
    s = pd.Series(atr)
    return (s.rolling(window, min_periods=50).rank(pct=True) * 100).fillna(50).to_numpy()


def volatility_regime(atr: np.ndarray) -> np.ndarray:
    p = atr_percentile(atr)
    return np.where(p < 33.3, "low volatility", np.where(p > 66.6, "high volatility", "normal volatility"))
