"""Turn a signal into an explicit, educational trade plan and measure how such plans played out.

Plan rules (same for every pattern, so results are comparable):
* Entry: a STOP order just beyond the signal bar (or the pattern's trigger level) - price must move your
  way first. Buffer = max(1 pip, 0.1 x ATR).
* Stop-loss: just beyond where the pattern is proven wrong (pattern-specific), at least 0.5 x ATR and
  3 x the spread away from the entry.
* Take-profit: `target_r` x the risk (default 2R).
* The entry order is cancelled if not filled within `expiry` bars.

Outcome rules (deliberately conservative):
* If a bar touches both the stop and the target, the STOP is assumed to have been hit first.
* A gap through the entry fills at the bar's open (worse than planned).
* Trades still open after `max_hold` bars are closed at that bar's close.
* Costs (spread + slippage on both sides) are subtracted, in R.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from edgelab.patterns.base import Signal, TradePlan

MAX_HOLD = {5: 48, 15: 32, 30: 24, 60: 24, 240: 15, 1440: 10}


def max_hold_for(bar_minutes: float) -> int:
    return MAX_HOLD.get(int(bar_minutes), 24)


def build_plan(sig: Signal, ctx, target_r: float = 2.0, expiry: int = 3, spread_pips: float = 1.0) -> TradePlan | None:
    if sig.direction not in ("long", "short") or sig.stop_ref is None:
        return None
    i, inst = sig.i, ctx.inst
    atr = float(ctx.atr[i])
    buf = max(inst.pip, 0.1 * atr)
    min_dist = max(0.5 * atr, 3 * spread_pips * inst.pip)
    if sig.direction == "long":
        entry = (sig.entry_ref if sig.entry_ref is not None else ctx.h[i]) + buf
        stop = min(sig.stop_ref - buf, entry - min_dist)
        target = entry + target_r * (entry - stop)
        kind = "BUY STOP"
    else:
        entry = (sig.entry_ref if sig.entry_ref is not None else ctx.l[i]) - buf
        stop = max(sig.stop_ref + buf, entry + min_dist)
        target = entry - target_r * (stop - entry)
        kind = "SELL STOP"
    r = lambda x: round(float(x), inst.decimals)  # noqa: E731
    return TradePlan(sig.direction, kind, r(entry), r(stop), r(target), target_r, expiry)


@dataclass
class Outcome:
    filled: bool
    result: str          # "win", "loss", "timeout", "not filled", "open"
    r: float             # result in R after costs (0 if not filled)
    mfe_r: float         # best excursion in R while in the trade
    mae_r: float         # worst excursion in R (negative)
    bars_held: int
    fill_i: int | None
    exit_i: int | None
    exit_price: float | None
    fwd_atr: float       # move in the signal direction after `fwd` bars, in ATRs (regardless of fill)


def evaluate(sig: Signal, ctx, max_hold: int, cost_price: float, fwd: int = 12) -> Outcome:
    """Replay the bars AFTER the signal to see what the plan would have done."""
    p = sig.plan
    o, h, l, c = ctx.o, ctx.h, ctx.l, ctx.c
    n = len(c)
    i = sig.i
    d = 1 if p.side == "long" else -1
    fwd_atr = (c[i + fwd] - c[i]) * d / ctx.atr[i] if i + fwd < n and ctx.atr[i] > 0 else np.nan
    risk = p.risk
    cost_r = cost_price / risk if risk > 0 else 0.0
    fill_i = None
    for j in range(i + 1, min(n, i + 1 + p.expiry_bars)):
        if (d > 0 and h[j] >= p.entry) or (d < 0 and l[j] <= p.entry):
            fill_i = j
            break
    if fill_i is None:
        state = "not filled" if i + p.expiry_bars < n else "open"
        return Outcome(False, state, 0.0, 0.0, 0.0, 0, None, None, None, fwd_atr)
    fill = max(p.entry, o[fill_i]) if d > 0 else min(p.entry, o[fill_i])
    mfe = mae = 0.0
    last = min(n - 1, fill_i + max_hold)
    for j in range(fill_i, last + 1):
        hi_r = (h[j] - fill) / risk * d if d > 0 else (fill - l[j]) / risk
        lo_r = (l[j] - fill) / risk if d > 0 else (fill - h[j]) / risk
        mfe, mae = max(mfe, hi_r), min(mae, lo_r)
        hit_stop = l[j] <= p.stop if d > 0 else h[j] >= p.stop
        hit_tgt = h[j] >= p.target if d > 0 else l[j] <= p.target
        if hit_stop:
            ex = min(p.stop, o[j]) if (d > 0 and j > fill_i) else (max(p.stop, o[j]) if (d < 0 and j > fill_i) else p.stop)
            return Outcome(True, "loss", (ex - fill) * d / risk - cost_r, mfe, mae, j - fill_i, fill_i, j, ex, fwd_atr)
        if hit_tgt:
            return Outcome(True, "win", (p.target - fill) * d / risk - cost_r, mfe, mae, j - fill_i, fill_i, j,
                           p.target, fwd_atr)
    if last == n - 1 and last < fill_i + max_hold:
        return Outcome(True, "open", (c[last] - fill) * d / risk - cost_r, mfe, mae, last - fill_i, fill_i, None,
                       None, fwd_atr)
    return Outcome(True, "timeout", (c[last] - fill) * d / risk - cost_r, mfe, mae, last - fill_i, fill_i, last,
                   float(c[last]), fwd_atr)
