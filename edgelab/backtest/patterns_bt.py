"""Pattern backtests across your pairs: every past signal replayed with the standard plan, as a trade table.

Data: your own downloads (OANDA) are used where available; otherwise the synthetic samples (flagged).
Time split: the first 70% of each pair's history is 'in-sample', the last 30% 'out-of-sample', so you can
check whether results hold up on data that wasn't used to look for them.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from edgelab.data.sessions import SESSION_CODE_LABELS
from edgelab.instruments import get_instrument
from edgelab.patterns.base import PATTERNS
from edgelab.risk import pip_value

IS_FRACTION = 0.7


def trades_frame(analysis, symbol: str, tf: str, settings, conv, source: str, is_sample: bool,
                 alert_rules: bool = False) -> pd.DataFrame:
    from edgelab.live.alerts import passes_filters

    inst = get_instrument(symbol)
    ctx = analysis.ctx
    idx = ctx.df.index
    if len(idx) == 0:
        return pd.DataFrame()
    split_t = idx[0] + (idx[-1] - idx[0]) * IS_FRACTION
    rows = []
    for s, o in zip(analysis.signals, analysis.outcomes):
        if o is None or not o.filled or o.result not in ("win", "loss", "timeout"):
            continue
        if alert_rules and not passes_filters(s, ctx, settings):
            continue
        pv = pip_value(inst, settings.forex_fixed_units, s.plan.entry, conv, settings.account_currency)
        risk_pips = s.plan.risk / inst.pip
        rows.append({
            "symbol": symbol, "timeframe": tf, "pattern": s.pattern, "pattern_name": PATTERNS[s.pattern].name,
            "direction": s.direction, "signal_time": s.ts, "exit_time": idx[o.exit_i] if o.exit_i is not None else pd.NaT,
            "result": o.result, "r": o.r, "mfe_r": o.mfe_r, "mae_r": o.mae_r, "bars_held": o.bars_held,
            "entry": s.plan.entry, "stop": s.plan.stop, "target": s.plan.target, "risk_pips": risk_pips,
            "risk_fixed_size": (risk_pips * pv) if pv else np.nan,
            "session": SESSION_CODE_LABELS[ctx.session[s.i]] if ctx.intraday else "daily",
            "hour_uk": s.ts.tz_convert("Europe/London").hour,
            "sample": "in-sample" if s.ts <= split_t else "out-of-sample",
            "data": source, "synthetic": is_sample, "n_cautions": len(s.cautions),
        })
    return pd.DataFrame(rows)


def summary(df: pd.DataFrame, by: list[str] | None = None) -> pd.DataFrame:
    """Trades, win rate, average R (with a 95% range), total R and profit factor per group."""
    if df.empty:
        return pd.DataFrame()

    def agg(g: pd.DataFrame) -> pd.Series:
        r = g["r"].to_numpy(float)
        n = len(r)
        sd = r.std(ddof=1) if n > 1 else np.nan
        half = 1.96 * sd / np.sqrt(n) if n > 1 else np.nan
        gl = -r[r < 0].sum()
        return pd.Series({"Trades": n, "Win rate %": (r > 0).mean() * 100, "Avg R": r.mean(),
                          "95% low": r.mean() - half, "95% high": r.mean() + half, "Total R": r.sum(),
                          "Profit factor": (r[r > 0].sum() / gl) if gl > 0 else np.nan})

    if not by:
        return agg(df).to_frame().T
    out = df.groupby(by, observed=True).apply(agg, include_groups=False).reset_index()
    return out.sort_values("Trades", ascending=False)


def equity_r(df: pd.DataFrame) -> pd.Series:
    """Cumulative R through time (trades ordered by exit)."""
    d = df.dropna(subset=["exit_time"]).sort_values("exit_time")
    return pd.Series(d["r"].cumsum().to_numpy(), index=pd.DatetimeIndex(d["exit_time"]))


def max_drawdown_r(cum: pd.Series) -> float:
    if cum.empty:
        return 0.0
    c = np.r_[0.0, cum.to_numpy()]
    return float((np.maximum.accumulate(c) - c).max())


def trades_per_day(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    days = max(1.0, (df["signal_time"].max() - df["signal_time"].min()) / pd.Timedelta(days=1) * 5 / 7)
    return len(df) / days
