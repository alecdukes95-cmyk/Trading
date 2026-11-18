"""Monte Carlo analysis of a trading edge, built on trade results measured in R.

R = profit or loss as a multiple of the amount risked (after costs). Working in R lets the same history be
replayed at any position size.

Methods
-------
bootstrap  Draw future trades at random (with replacement) from the past results. Answers: "if the future
           looks like the past, what range of outcomes should I expect?"
shuffle    Re-order the SAME past trades at random. Total profit is identical; it shows how much of the
           drawdown/streak experience was just luck of the order.
perturb    Parameter uncertainty: each simulated future first draws its own win rate, average win and average
           loss from their statistical uncertainty (the past is only a sample), then plays trades with those.
           This is the most honest view when the history is short.

Sizing
------
fixed_fraction  risk a % of CURRENT equity on every trade (compounding; the common professional rule).
fixed_amount    risk the same money amount on every trade (e.g. what 1 mini lot typically risks).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

PCTS = (5, 25, 50, 75, 95)


@dataclass
class MCConfig:
    n_trades: int = 100
    n_sims: int = 5000
    start_balance: float = 10_000.0
    sizing: str = "fixed_fraction"      # or "fixed_amount"
    risk_pct: float = 0.65               # for fixed_fraction
    risk_amount: float = 65.0            # for fixed_amount
    loss_limit_pct: float = 20.0         # "ruin": losing this % of the starting balance at any point
    method: str = "bootstrap"            # bootstrap | shuffle | perturb
    seed: int | None = 7


@dataclass
class MCResult:
    config: MCConfig
    method: str
    n_hist: int
    equity_pct: dict                     # percentile -> array (n_trades + 1) of equity
    ending: np.ndarray
    max_dd_pct: np.ndarray               # max drawdown per sim, % of the running peak
    hit_limit: np.ndarray                # bool per sim
    longest_losing: np.ndarray
    recovery_trades: np.ndarray          # trades from the deepest point back to the old peak (nan = not within horizon)
    sample_paths: np.ndarray             # a few individual paths for illustration
    summary: dict = field(default_factory=dict)


def _clean(r) -> np.ndarray:
    r = np.asarray(r, dtype=float)
    return r[np.isfinite(r)]


def simulate_r(r_hist, n_trades: int, n_sims: int, method: str, rng: np.random.Generator) -> np.ndarray:
    """Matrix (n_sims, n_trades) of simulated trade results in R."""
    r = _clean(r_hist)
    if len(r) == 0:
        raise ValueError("No trade results to simulate from.")
    if method == "bootstrap":
        return rng.choice(r, size=(n_sims, n_trades), replace=True)
    if method == "shuffle":
        idx = np.argsort(rng.random((n_sims, len(r))), axis=1)
        out = r[idx]
        if n_trades <= len(r):
            return out[:, :n_trades]
        reps = int(np.ceil(n_trades / len(r)))
        return np.tile(out, reps)[:, :n_trades]
    if method == "perturb":
        wins, losses = r[r > 0], r[r <= 0]
        nw, nl = len(wins), len(losses)
        p = rng.beta(nw + 1, nl + 1, size=n_sims)                       # uncertainty in the win rate
        mw = wins.mean() if nw else 0.0
        ml = losses.mean() if nl else -1.0
        sw = wins.std(ddof=1) / np.sqrt(nw) if nw > 1 else abs(mw) * 0.5
        sl = losses.std(ddof=1) / np.sqrt(nl) if nl > 1 else abs(ml) * 0.5
        avg_w = np.maximum(0.0, rng.normal(mw, sw, n_sims))              # uncertainty in the average win
        avg_l = np.minimum(0.0, rng.normal(ml, sl, n_sims))              # ...and the average loss
        is_win = rng.random((n_sims, n_trades)) < p[:, None]
        # keep the shape of real wins/losses, rescaled to each simulation's drawn averages
        w_draw = rng.choice(wins, size=(n_sims, n_trades)) / mw * avg_w[:, None] if nw and mw > 0 else \
            np.broadcast_to(avg_w[:, None], (n_sims, n_trades))
        l_draw = rng.choice(losses, size=(n_sims, n_trades)) / ml * avg_l[:, None] if nl and ml < 0 else \
            np.broadcast_to(avg_l[:, None], (n_sims, n_trades))
        return np.where(is_win, w_draw, l_draw)
    raise ValueError(f"Unknown method {method}")


def equity_paths(r: np.ndarray, cfg: MCConfig) -> np.ndarray:
    """(n_sims, n_trades+1) equity paths in money, starting at start_balance."""
    if cfg.sizing == "fixed_fraction":
        f = cfg.risk_pct / 100.0
        growth = np.clip(1.0 + f * r, 0.0, None)          # can't lose more than the account
        eq = cfg.start_balance * np.cumprod(growth, axis=1)
    else:
        eq = cfg.start_balance + np.cumsum(cfg.risk_amount * r, axis=1)
        eq = np.maximum(eq, 0.0)
        # once at zero, stay at zero
        eq = np.where(np.minimum.accumulate(eq, axis=1) <= 0, 0.0, eq)
    return np.hstack([np.full((len(r), 1), cfg.start_balance), eq])


def longest_streak(mask: np.ndarray) -> np.ndarray:
    """Longest run of True per row."""
    c = np.cumsum(mask, axis=1)
    reset = np.maximum.accumulate(np.where(~mask, c, 0), axis=1)
    return (c - reset).max(axis=1)


def path_stats(eq: np.ndarray, r: np.ndarray, cfg: MCConfig):
    peak = np.maximum.accumulate(eq, axis=1)
    dd = np.where(peak > 0, (peak - eq) / peak, 0.0)
    max_dd = dd.max(axis=1) * 100
    floor = cfg.start_balance * (1 - cfg.loss_limit_pct / 100)
    hit = (eq <= floor).any(axis=1)
    streak = longest_streak(r < 0)
    trough = dd.argmax(axis=1)
    peak_at = peak[np.arange(len(eq)), trough]
    after = np.arange(eq.shape[1])[None, :] > trough[:, None]
    back = after & (eq >= peak_at[:, None])
    rec = np.where(back.any(axis=1), back.argmax(axis=1) - trough, np.nan)
    rec = np.where(max_dd == 0, 0.0, rec)
    return max_dd, hit, streak, rec


def run(r_hist, cfg: MCConfig) -> MCResult:
    rng = np.random.default_rng(cfg.seed)
    r_hist = _clean(r_hist)
    r = simulate_r(r_hist, cfg.n_trades, cfg.n_sims, cfg.method, rng)
    eq = equity_paths(r, cfg)
    max_dd, hit, streak, rec = path_stats(eq, r, cfg)
    ending = eq[:, -1]
    pct = {p: np.percentile(eq, p, axis=0) for p in PCTS}
    res = MCResult(cfg, cfg.method, len(r_hist), pct, ending, max_dd, hit, streak, rec, eq[:12])
    sb = cfg.start_balance
    res.summary = {
        "prob_profit": float((ending > sb).mean()),
        "median_end": float(np.median(ending)),
        "p5_end": float(np.percentile(ending, 5)),
        "p95_end": float(np.percentile(ending, 95)),
        "median_return_pct": float((np.median(ending) / sb - 1) * 100),
        "prob_limit": float(hit.mean()),
        "median_dd": float(np.median(max_dd)),
        "p95_dd": float(np.percentile(max_dd, 95)),
        "median_streak": float(np.median(streak)),
        "p95_streak": float(np.percentile(streak, 95)),
        "median_recovery": float(np.nanmedian(rec)) if np.isfinite(rec).any() else float("nan"),
        "prob_not_recovered": float(np.isnan(rec).mean()),
    }
    return res


# ----------------------------------------------------------------------------------- edge statistics
@dataclass
class EdgeTest:
    n: int
    mean_r: float
    ci_low: float
    ci_high: float
    prob_mean_le_zero: float
    win_rate: float
    avg_win: float
    avg_loss: float
    verdict: str
    detail: str


def edge_test(r_hist, n_boot: int = 10_000, seed: int = 11, n_tests: int = 1) -> EdgeTest:
    """Is the average trade result distinguishable from zero after costs? (bootstrap)"""
    r = _clean(r_hist)
    n = len(r)
    if n == 0:
        return EdgeTest(0, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, "No trades.", "")
    rng = np.random.default_rng(seed)
    means = rng.choice(r, size=(n_boot, n), replace=True).mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    # p-value style: how often would a strategy with a true mean of 0 (same spread) look this good?
    centred = r - r.mean()
    null = rng.choice(centred, size=(n_boot, n), replace=True).mean(axis=1)
    p_le0 = float((null >= r.mean()).mean()) if r.mean() > 0 else 1.0
    wins, losses = r[r > 0], r[r <= 0]
    adj = 0.05 / max(1, n_tests)
    if n < 30:
        verdict = "Too few trades to tell an edge from luck."
    elif lo > 0 and p_le0 < adj:
        verdict = "Distinguishable from zero after costs (on this data)."
    elif hi < 0:
        verdict = "Negative after costs: on this data the trades lost money."
    else:
        verdict = "NOT distinguishable from zero after costs."
    detail = (f"Average {r.mean():+.3f} R per trade over {n} trades; 95% bootstrap range {lo:+.3f} to {hi:+.3f} R. "
              + ("The average is at or below zero, so there is no evidence of an edge at all."
                 if r.mean() <= 0 else
                 "If the true edge were zero, a result at least this good would appear by luck "
                 + ("less than 0.1% of the time." if p_le0 < 0.001 else f"about {p_le0 * 100:.1f}% of the time.")))
    if n_tests > 1:
        detail += (f" You are comparing {n_tests} pattern/direction combinations, so the bar is raised to "
                   f"{adj * 100:.2f}% - with that many tries, about {n_tests * 0.05:.1f} would look 'significant' "
                   "by pure chance.")
    return EdgeTest(n, float(r.mean()), float(lo), float(hi), p_le0, float(len(wins) / n),
                    float(wins.mean()) if len(wins) else 0.0, float(losses.mean()) if len(losses) else 0.0,
                    verdict, detail)


# ----------------------------------------------------------------------------------- sizing
def kelly_fraction(r_hist) -> float:
    """Fraction of equity to risk per trade (per 1R) that maximises long-run growth, from the past results.

    Returns 0 when the average result is not positive (Kelly says: don't bet)."""
    r = _clean(r_hist)
    if len(r) == 0 or r.mean() <= 0:
        return 0.0
    fs = np.linspace(0.0005, 0.5, 1000)
    with np.errstate(divide="ignore", invalid="ignore"):
        g = np.array([np.mean(np.log(np.clip(1 + f * r, 1e-12, None))) for f in fs])
    return float(fs[int(np.argmax(g))])


def sizing_table(r_hist, cfg: MCConfig, risks_pct=(0.25, 0.5, 1.0, 2.0), n_sims: int = 2000) -> list[dict]:
    """Same bootstrap futures at different risk-per-trade sizes, including your rule and fractional Kelly."""
    r = _clean(r_hist)
    k = kelly_fraction(r) * 100
    rows = [("Your rule", cfg.risk_pct)] + [(f"{x:g}%", x) for x in risks_pct if abs(x - cfg.risk_pct) > 1e-9]
    if k > 0:
        rows += [("Quarter Kelly", k / 4), ("Half Kelly", k / 2), ("Full Kelly", k)]
    rng = np.random.default_rng(cfg.seed)
    sims = simulate_r(r, cfg.n_trades, n_sims, "bootstrap", rng)   # identical futures for every row
    out = []
    for label, pct in rows:
        c = MCConfig(**{**cfg.__dict__, "risk_pct": pct, "sizing": "fixed_fraction", "n_sims": n_sims})
        eq = equity_paths(sims, c)
        dd, hit, _, _ = path_stats(eq, sims, c)
        out.append({"Sizing": label, "Risk per trade %": round(pct, 2),
                    "Median ending": float(np.median(eq[:, -1])), "Worst 5% ending": float(np.percentile(eq[:, -1], 5)),
                    "Median max drawdown %": float(np.median(dd)), "Chance of hitting loss limit %": float(hit.mean() * 100)})
    return out
