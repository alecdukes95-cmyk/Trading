"""Backtest engine for no-code strategies: realistic, sequential, and honest about what it measured.

* One position at a time per pair; a new signal is ignored while a trade is open.
* Entry on the bar AFTER the signal (open, or a stop order beyond the signal bar with an expiry).
* Stop checked before target on every bar (a bar touching both is a loss); gaps through a stop fill at the open.
* Costs: spread + 2 x slippage from your broker profile, plus commission, all converted into R.
* Results are reported in R (per unit of risk) and, using your risk-per-trade rule, in account currency.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from edgelab.backtest.rules import Strategy, filter_mask, trigger_signals, volatility_regime
from edgelab.brokers import STANDARD_LOT
from edgelab.data.sessions import SESSION_CODE_LABELS, in_window, trading_day
from edgelab.fx import FxConverter
from edgelab.risk import value_per_price_unit

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass
class Costs:
    spread_pips: float
    slippage_pips: float
    commission_model: str = "none"
    commission_value: float = 0.0


def costs_for(broker, symbol: str) -> Costs:
    return Costs(broker.spread_for(symbol), broker.slippage_pips, broker.commission_model, broker.commission_value)


def simulate(strategy: Strategy, analysis, symbol: str, costs: Costs, *, risk_amount: float = 65.0,
             ccy: str = "GBP", conv: FxConverter | None = None) -> pd.DataFrame:
    """All trades the strategy would have taken on this pair (one at a time)."""
    ctx = analysis.ctx
    inst = ctx.inst
    o, h, l, c, atr = ctx.o, ctx.h, ctx.l, ctx.c, ctx.atr
    n = len(c)
    idx = ctx.df.index
    conv = conv or FxConverter()
    long_sig, short_sig, pstops = trigger_signals(strategy, analysis)
    long_sig &= filter_mask(strategy, analysis, "long")
    short_sig &= filter_mask(strategy, analysis, "short")
    cand = sorted([(i, "long") for i in np.flatnonzero(long_sig)] + [(i, "short") for i in np.flatnonzero(short_sig)])
    day = np.asarray(trading_day(idx, ctx.cfg))
    win = in_window(idx, ctx.cfg) if ctx.intraday else np.ones(n, bool)
    vol_reg = volatility_regime(atr)
    st, tg, ex, en = strategy.stop, strategy.target, strategy.exits, strategy.entry
    max_bars = int(ex.get("max_bars", 48))
    be_r = float(ex.get("breakeven_at_r") or 0)
    max_per_day = int(strategy.filters.get("max_trades_per_day", 0) or 0)
    spread_px = costs.spread_pips * inst.pip
    cost_px = (costs.spread_pips + 2 * costs.slippage_pips) * inst.pip
    rows, busy_until, per_day = [], -1, {}
    for i, side in cand:
        if i <= busy_until or i + 1 >= n:
            continue
        if max_per_day and per_day.get(day[i], 0) >= max_per_day:
            continue
        d = 1 if side == "long" else -1
        a = float(atr[i]) if np.isfinite(atr[i]) and atr[i] > 0 else float(h[i] - l[i]) or inst.pip
        buf = max(inst.pip, 0.1 * a)
        # ---- entry
        if en.get("type") == "stop":
            level = (h[i] + buf) if d > 0 else (l[i] - buf)
            fill_i = None
            for j in range(i + 1, min(n, i + 1 + int(en.get("expiry_bars", 3)))):
                if (d > 0 and h[j] >= level) or (d < 0 and l[j] <= level):
                    fill_i = j
                    break
            if fill_i is None:
                continue
            entry = max(level, o[fill_i]) if d > 0 else min(level, o[fill_i])
        else:
            fill_i, entry = i + 1, float(o[i + 1])
        # ---- stop
        stype, sval = st.get("type", "atr"), float(st.get("value", 1.5))
        if stype == "pips":
            dist = sval * inst.pip
        elif stype == "bar":
            dist = (entry - (l[i] - buf)) if d > 0 else ((h[i] + buf) - entry)
        elif stype == "pattern" and pstops.get((i, side)) is not None:
            dist = (entry - (pstops[(i, side)] - buf)) if d > 0 else ((pstops[(i, side)] + buf) - entry)
        else:
            dist = (sval if stype == "atr" else 1.5) * a
        dist = max(dist, 3 * spread_px, inst.pip)
        stop = entry - d * dist
        # ---- target
        ttype, tval = tg.get("type", "r"), float(tg.get("value", 2.0))
        target = None if ttype == "none" else entry + d * {"r": tval * dist, "atr": tval * a, "pips": tval * inst.pip}[ttype]
        # ---- manage
        cur_stop, moved_be = stop, False
        mfe = mae = 0.0
        result, exit_i, exit_px = "timeout", None, None
        last = min(n - 1, fill_i + max_bars)
        for j in range(fill_i, last + 1):
            if ex.get("session_end") and ctx.intraday and j > fill_i and (not win[j] or day[j] != day[fill_i]):
                result, exit_i, exit_px = "session end", j - 1, float(c[j - 1])
                break
            mfe = max(mfe, ((h[j] - entry) if d > 0 else (entry - l[j])) / dist)
            mae = min(mae, ((l[j] - entry) if d > 0 else (entry - h[j])) / dist)
            hit_stop = (l[j] <= cur_stop) if d > 0 else (h[j] >= cur_stop)
            if hit_stop:
                px = min(cur_stop, o[j]) if (d > 0 and j > fill_i) else (max(cur_stop, o[j]) if (d < 0 and j > fill_i) else cur_stop)
                result, exit_i, exit_px = ("breakeven" if moved_be else "loss"), j, float(px)
                break
            if target is not None and ((h[j] >= target) if d > 0 else (l[j] <= target)):
                result, exit_i, exit_px = "win", j, float(target)
                break
            if be_r > 0 and not moved_be and mfe >= be_r:
                cur_stop, moved_be = entry, True     # from the next bar
        if exit_i is None:
            exit_i, exit_px = last, float(c[last])
            if last == n - 1 and last < fill_i + max_bars:
                result = "open at end of data"
        gross_r = (exit_px - entry) * d / dist
        vpu = value_per_price_unit(inst, 1.0, entry, conv, ccy)
        comm_r = 0.0
        if vpu and costs.commission_model != "none":
            units = risk_amount / (dist * vpu)
            comm = {"per_lot_round_turn": costs.commission_value * units / STANDARD_LOT,
                    "per_lot_per_side": 2 * costs.commission_value * units / STANDARD_LOT,
                    "per_trade": costs.commission_value,
                    "percent_per_side": 2 * costs.commission_value / 100 * units * entry * vpu}.get(costs.commission_model, 0.0)
            comm_r = comm / risk_amount
        r = gross_r - cost_px / dist - comm_r
        busy_until = exit_i
        per_day[day[i]] = per_day.get(day[i], 0) + 1
        t_sig = idx[i]
        rows.append({"symbol": symbol, "side": side, "signal_i": int(i), "signal_time": t_sig, "entry_time": idx[fill_i],
                     "exit_time": idx[exit_i], "entry": entry, "stop": stop, "target": target, "exit": exit_px,
                     "result": result, "r": r, "gross_r": gross_r, "mfe_r": mfe, "mae_r": mae,
                     "bars": int(exit_i - fill_i + 1), "risk_pips": dist / inst.pip,
                     "hour_uk": (t_sig + pd.Timedelta(minutes=ctx.bar_minutes)).tz_convert("Europe/London").hour,
                     "weekday": WEEKDAYS[(t_sig + pd.Timedelta(minutes=ctx.bar_minutes)).tz_convert("Europe/London").weekday()],
                     "session": SESSION_CODE_LABELS[ctx.session[i]] if ctx.intraday else "daily",
                     "trend_regime": {1: "with an up-trend", -1: "with a down-trend", 0: "no clear trend"}[int(ctx.htf_trend[i])],
                     "vol_regime": str(vol_reg[i])})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------------- metrics
def metrics(trades: pd.DataFrame, risk_pct: float = 0.65, start_balance: float = 10_000.0) -> dict:
    if trades is None or trades.empty:
        return {"trades": 0}
    t = trades.sort_values("exit_time")
    r = t["r"].to_numpy(float)
    n = len(r)
    wins, losses = r[r > 0], r[r <= 0]
    cum = np.r_[0.0, np.cumsum(r)]
    dd_r = float((np.maximum.accumulate(cum) - cum).max())
    eq = start_balance * np.cumprod(1 + risk_pct / 100 * r)
    peak = np.maximum.accumulate(np.r_[start_balance, eq])
    dd_pct = float(((peak - np.r_[start_balance, eq]) / peak).max() * 100)
    gl = -losses.sum()
    sd = r.std(ddof=1) if n > 1 else np.nan
    return {"trades": n, "win_rate": float((r > 0).mean()), "avg_win": float(wins.mean()) if len(wins) else 0.0,
            "avg_loss": float(losses.mean()) if len(losses) else 0.0, "expectancy": float(r.mean()),
            "total_r": float(r.sum()), "profit_factor": float(wins.sum() / gl) if gl > 0 else np.nan,
            "max_dd_r": dd_r, "max_dd_pct": dd_pct, "t_stat": float(r.mean() / (sd / np.sqrt(n))) if n > 1 and sd > 0 else np.nan,
            "ending_balance": float(eq[-1]), "return_pct": float((eq[-1] / start_balance - 1) * 100),
            "avg_bars": float(t["bars"].mean())}


def split_is_oos(trades: pd.DataFrame, data_start: pd.Timestamp, data_end: pd.Timestamp, is_fraction: float = 0.7):
    cut = data_start + (data_end - data_start) * is_fraction
    return trades[trades["signal_time"] <= cut], trades[trades["signal_time"] > cut], cut


def breakdown(trades: pd.DataFrame, by: str) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    g = trades.groupby(by, observed=True)["r"]
    out = pd.DataFrame({"Trades": g.size(), "Win rate %": g.apply(lambda x: (x > 0).mean() * 100),
                        "Avg R": g.mean(), "Total R": g.sum()}).reset_index()
    if by == "weekday":
        out["_o"] = out["weekday"].map({d: k for k, d in enumerate(WEEKDAYS)})
        out = out.sort_values("_o").drop(columns="_o")
    elif by == "hour_uk":
        out = out.sort_values("hour_uk")
    return out


# ----------------------------------------------------------------------------------- multi-pair runner
def run_backtest(strategy: Strategy, analyses: dict, broker, *, risk_amount: float, ccy: str = "GBP",
                 conv: FxConverter | None = None) -> tuple[pd.DataFrame, dict]:
    """analyses: symbol -> pattern Analysis (with ctx). Returns all trades and each pair's data span."""
    frames, spans = [], {}
    for sym, a in analyses.items():
        tr = simulate(strategy, a, sym, costs_for(broker, sym), risk_amount=risk_amount, ccy=ccy, conv=conv)
        if not tr.empty:
            frames.append(tr)
        spans[sym] = (a.ctx.df.index[0], a.ctx.df.index[-1])
    trades = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return trades, spans


def split_multi(trades: pd.DataFrame, spans: dict, is_fraction: float = 0.7):
    """Per-pair chronological split (each pair's first 70% of its own data is in-sample)."""
    if trades.empty:
        return trades, trades
    ins, oos = [], []
    for sym, (a, b) in spans.items():
        t = trades[trades["symbol"] == sym]
        i_, o_, _ = split_is_oos(t, a, b, is_fraction)
        ins.append(i_)
        oos.append(o_)
    return pd.concat(ins), pd.concat(oos)


# ----------------------------------------------------------------------------------- parameters
PARAMS = {
    "stop.value": ("Stop size (ATR multiple / pips)", [0.75, 1.0, 1.5, 2.0, 3.0]),
    "target.value": ("Target (R / ATR / pips)", [1.0, 1.5, 2.0, 3.0, 4.0]),
    "exits.max_bars": ("Maximum bars in a trade", [12, 24, 48, 96]),
    "filters.trend_n": ("Trend filter SMA length", [50, 100, 200]),
    "trigger.n": ("Trigger length n (MA / breakout / RSI)", [10, 20, 50]),
    "exits.breakeven_at_r": ("Move stop to breakeven at (R, 0 = never)", [0.0, 0.5, 1.0, 1.5]),
}


def with_param(strategy: Strategy, path: str, value) -> Strategy:
    section, key = path.split(".")
    d = dict(getattr(strategy, section))
    d[key] = value
    return replace(strategy, **{section: d})


def walk_forward(strategy: Strategy, analyses: dict, broker, param: str, values: list, *, folds: int = 5,
                 risk_amount: float = 65.0, ccy: str = "GBP", min_trades: int = 10, conv=None) -> dict:
    """Anchored walk-forward: before each test window, pick the parameter value that did best on ALL data so far,
    then record how that choice did on the next, unseen window. Only the unseen-window results count."""
    runs = {}
    for v in values:
        tr, spans = run_backtest(with_param(strategy, param, v), analyses, broker, risk_amount=risk_amount, ccy=ccy,
                                 conv=conv)
        runs[v] = tr
    start = min(a for a, _ in spans.values())
    end = max(b for _, b in spans.values())
    edges = [start + (end - start) * k / (folds + 1) for k in range(folds + 2)]
    rows, oos_parts = [], []
    for k in range(1, folds + 1):
        is_end, oos_end = edges[k], edges[k + 1]
        scores = {}
        for v, tr in runs.items():
            ins = tr[tr["signal_time"] <= is_end] if not tr.empty else tr
            scores[v] = ins["r"].mean() if len(ins) >= min_trades else -np.inf
        best = max(scores, key=scores.get)
        tr = runs[best]
        oos = tr[(tr["signal_time"] > is_end) & (tr["signal_time"] <= oos_end)] if not tr.empty else tr
        oos_parts.append(oos.assign(fold=k, chosen=best))
        rows.append({"Fold": k, "Trained up to": is_end, "Tested until": oos_end, "Chosen value": best,
                     "In-sample avg R": scores[best] if np.isfinite(scores[best]) else np.nan,
                     "Out-of-sample trades": len(oos), "Out-of-sample avg R": oos["r"].mean() if len(oos) else np.nan})
    oos_all = pd.concat(oos_parts, ignore_index=True) if oos_parts else pd.DataFrame()
    hindsight = {v: (tr["r"].mean() if len(tr) else np.nan) for v, tr in runs.items()}
    return {"folds": pd.DataFrame(rows), "oos_trades": oos_all, "hindsight": hindsight, "runs": runs}


def grid(strategy: Strategy, analyses: dict, broker, p1: str, v1: list, p2: str, v2: list, *, risk_amount: float,
         ccy: str = "GBP", is_fraction: float = 0.7, conv=None) -> dict:
    """Average R for every combination of two parameters, in-sample and out-of-sample."""
    is_tab = pd.DataFrame(index=v1, columns=v2, dtype=float)
    oos_tab = pd.DataFrame(index=v1, columns=v2, dtype=float)
    n_tab = pd.DataFrame(index=v1, columns=v2, dtype=float)
    for a in v1:
        for b in v2:
            s = with_param(with_param(strategy, p1, a), p2, b)
            tr, spans = run_backtest(s, analyses, broker, risk_amount=risk_amount, ccy=ccy, conv=conv)
            i_, o_ = split_multi(tr, spans, is_fraction)
            is_tab.loc[a, b] = i_["r"].mean() if len(i_) else np.nan
            oos_tab.loc[a, b] = o_["r"].mean() if len(o_) else np.nan
            n_tab.loc[a, b] = len(tr)
    return {"is": is_tab, "oos": oos_tab, "n": n_tab}


# ----------------------------------------------------------------------------------- multiple testing
def strategy_hash(strategy: Strategy) -> str:
    d = strategy.to_json().replace(f'"name": "{strategy.name}"', "")
    return hashlib.sha1(d.encode()).hexdigest()[:12]


def luck_threshold(n_tests: int) -> float:
    """Roughly the t-statistic the BEST of n zero-edge strategies reaches by luck (expected max of n normals)."""
    if n_tests <= 1:
        return 1.96
    from math import log, sqrt

    return max(1.96, sqrt(2 * log(n_tests)))


def overfitting_note(best_t: float, n_tests: int) -> str:
    thr = luck_threshold(n_tests)
    if not np.isfinite(best_t):
        return "Not enough trades to judge."
    if n_tests <= 1:
        return ("This is your first variation on this data. " +
                ("Its t-statistic clears 1.96 (conventional significance)." if best_t > 1.96 else
                 "Its t-statistic does not clear 1.96 - no evidence of an edge."))
    verdict = "clears" if best_t > thr else "does NOT clear"
    return (f"You've now tested **{n_tests} distinct variations** on this data. The best of {n_tests} random, zero-edge "
            f"strategies would typically reach a t-statistic of about **{thr:.2f}** by luck alone. This one's in-sample "
            f"t-statistic is **{best_t:.2f}**, so it {verdict} that bar. Every extra tweak raises the bar further.")
