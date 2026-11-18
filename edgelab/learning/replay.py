"""Bar-by-bar replay trainer: hide the future, decide, reveal, score.

Scoring has three independent parts, because a good decision can lose and a bad one can win:
1. Read of the market: did the move that followed match your call? The 'hindsight label' is whichever of
   +1.5 ATR or -1.5 ATR price reached first in the next `horizon` bars ('up', 'down'), else 'chop'.
   Long matches 'up', short matches 'down', no-trade matches 'chop'.
2. Trade result in R after costs (market entry at the next bar's open, stop checked before target).
3. Risk discipline: stop at a sensible distance (0.5-4 ATR), reward:risk at least 1.5, and the loss at the stop
   within your risk-per-trade rule.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

MOVE_ATR = 1.5


@dataclass
class Decision:
    side: str            # "long", "short" or "none"
    stop: float | None = None
    target: float | None = None


@dataclass
class ReplayResult:
    hindsight: str                  # "up", "down", "chop"
    read_correct: bool
    filled: bool
    result: str                     # "win", "loss", "timeout", "no trade"
    r: float
    entry: float | None
    exit_i: int | None
    rr: float | None
    stop_atr: float | None
    discipline: list = field(default_factory=list)    # (ok: bool, message)
    score: int = 0                                     # 0-100 summary for progress tracking


def hindsight_label(h, l, c, i: int, atr: float, horizon: int) -> tuple[str, int | None]:
    up, dn = c[i] + MOVE_ATR * atr, c[i] - MOVE_ATR * atr
    for j in range(i + 1, min(len(c), i + 1 + horizon)):
        hit_up, hit_dn = h[j] >= up, l[j] <= dn
        if hit_up and hit_dn:
            return "chop", j          # both in one bar: violent but directionless
        if hit_up:
            return "up", j
        if hit_dn:
            return "down", j
    return "chop", None


def evaluate_decision(ctx, i: int, d: Decision, horizon: int, cost_price: float, risk_check=None) -> ReplayResult:
    """Score a decision made at the CLOSE of bar i."""
    o, h, l, c = ctx.o, ctx.h, ctx.l, ctx.c
    atr = float(ctx.atr[i])
    label, _ = hindsight_label(h, l, c, i, atr, horizon)
    want = {"long": "up", "short": "down", "none": "chop"}[d.side]
    read_ok = label == want
    if d.side == "none":
        score = 100 if read_ok else 30
        msg = [(True, "No trade: nothing to lose. Patience is a position.")] if read_ok else \
              [(False, f"No trade, but price moved {MOVE_ATR} ATR {label} - there was an opportunity.")]
        return ReplayResult(label, read_ok, False, "no trade", 0.0, None, None, None, None, msg, score)

    if i + 1 >= len(c):
        return ReplayResult(label, read_ok, False, "no trade", 0.0, None, None, None, None, [], 0)
    sgn = 1 if d.side == "long" else -1
    entry = float(o[i + 1])
    risk = (entry - d.stop) * sgn
    disc = []
    if risk <= 0:
        disc.append((False, "Your stop is on the wrong side of the entry price."))
        return ReplayResult(label, read_ok, False, "no trade", 0.0, entry, None, None, None, disc, 0)
    reward = (d.target - entry) * sgn
    rr = reward / risk if reward > 0 else 0.0
    stop_atr = risk / atr if atr > 0 else None
    if stop_atr is not None:
        if stop_atr < 0.5:
            disc.append((False, f"Stop only {stop_atr:.1f} ATR away - inside normal noise, easily hit by a random wiggle."))
        elif stop_atr > 4:
            disc.append((False, f"Stop {stop_atr:.1f} ATR away - very wide; size down or find a tighter invalidation."))
        else:
            disc.append((True, f"Stop {stop_atr:.1f} ATR away - a sensible distance."))
    disc.append((rr >= 1.5, f"Reward:risk {rr:.1f}:1" + (" - good." if rr >= 1.5 else
                                                          " - below 1.5:1 you need a high win rate just to break even.")))
    if risk_check is not None:
        ok, text = risk_check(entry, d.stop)
        disc.append((ok, text))
    cost_r = cost_price / risk
    result, ex, r_val = "timeout", None, None
    last = min(len(c) - 1, i + horizon)
    for j in range(i + 1, last + 1):
        if (sgn > 0 and l[j] <= d.stop) or (sgn < 0 and h[j] >= d.stop):
            px = min(d.stop, o[j]) if sgn > 0 else max(d.stop, o[j])   # a gap through the stop fills at the open
            result, ex, r_val = "loss", j, (px - entry) * sgn / risk
            break
        if (sgn > 0 and h[j] >= d.target) or (sgn < 0 and l[j] <= d.target):
            result, ex, r_val = "win", j, reward / risk
            break
    if r_val is None:
        ex, r_val = last, (c[last] - entry) * sgn / risk
    r_val -= cost_r
    n_ok = sum(ok for ok, _ in disc)
    score = int(round(40 * read_ok + 30 * (n_ok / max(1, len(disc))) + 30 * float(np.clip((r_val + 1) / 3, 0, 1))))
    return ReplayResult(label, read_ok, True, result, float(r_val), entry, ex, rr, stop_atr, disc, score)


def pick_start(ctx, rng: np.random.Generator, mode: str, signals=None, history: int = 150, future: int = 60,
               settings=None) -> int | None:
    """Choose a replay starting bar: a random in-window moment, or a bar where a pattern just completed."""
    n = len(ctx.c)
    lo, hi = history, n - future - 1
    if hi <= lo:
        return None
    if mode == "pattern" and signals:
        from edgelab.live.alerts import passes_filters

        cands = [s.i for s in signals if lo <= s.i <= hi and s.plan is not None and
                 (settings is None or passes_filters(s, ctx, settings))]
        if not cands:
            cands = [s.i for s in signals if lo <= s.i <= hi and s.plan is not None]
        return int(rng.choice(cands)) if cands else None
    ok = np.flatnonzero(ctx.in_window[lo:hi]) + lo if ctx.intraday else np.arange(lo, hi)
    return int(rng.choice(ok)) if len(ok) else None
