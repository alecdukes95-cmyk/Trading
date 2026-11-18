"""Pattern detection, trade plans and historical pattern statistics."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from edgelab.brokers import BrokerProfile
from edgelab.data.sessions import SessionConfig
from edgelab.instruments import Instrument
from edgelab.patterns.base import PATTERNS, TRADEABLE, PatternInfo, Signal, TradePlan  # noqa: F401
from edgelab.patterns.detectors import Context, detect_all  # noqa: F401
from edgelab.patterns.plan import Outcome, build_plan, evaluate, max_hold_for
from edgelab.patterns.stats import PatternStats, stats_table


@dataclass
class Analysis:
    signals: list
    swings: list
    outcomes: list
    stats: dict
    ctx: Context


def analyse(df: pd.DataFrame, cfg: SessionConfig, inst: Instrument, bar_minutes: float, broker: BrokerProfile,
            target_r: float = 2.0, expiry: int = 3, enabled: list | None = None) -> Analysis:
    """Detect every pattern, attach trade plans, replay what happened after each one, and summarise."""
    sigs, swings, ctx = detect_all(df, cfg, inst, bar_minutes, enabled)
    spread = broker.spread_for(inst.symbol)
    cost_price = (spread + 2 * broker.slippage_pips) * inst.pip
    hold = max_hold_for(bar_minutes)
    outcomes = []
    for s in sigs:
        s.plan = build_plan(s, ctx, target_r, expiry, spread)
        if s.plan:
            atr_i = float(ctx.atr[s.i])
            if atr_i > 0 and s.plan.risk > 4 * atr_i:
                s.cautions.append(f"Wide stop: {s.plan.risk / atr_i:.1f}x the average bar range - a big loss if wrong, "
                                  "and a fixed position size may break your risk rule")
            risk_pips = s.plan.risk / inst.pip
            if risk_pips > 0 and (spread + 2 * broker.slippage_pips) / risk_pips > 0.25:
                s.cautions.append(f"Costs are {(spread + 2 * broker.slippage_pips) / risk_pips * 100:.0f}% of the risk "
                                  f"({risk_pips:.1f}-pip stop) - hard to profit after costs")
            outcomes.append(evaluate(s, ctx, hold, cost_price))
        else:
            outcomes.append(None)
    return Analysis(sigs, swings, outcomes, stats_table(sigs, outcomes), ctx)
