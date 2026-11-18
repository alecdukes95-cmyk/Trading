import numpy as np
import pytest

from edgelab.montecarlo import MCConfig, edge_test, kelly_fraction, run, sizing_table
from edgelab.montecarlo.engine import equity_paths, longest_streak, path_stats, simulate_r
from edgelab.ui.mc_view import manual_r


def test_fixed_amount_and_fixed_fraction_paths_exact():
    r = np.array([[1.0, -1.0, 2.0]])
    eq = equity_paths(r, MCConfig(sizing="fixed_amount", risk_amount=50, start_balance=1000))
    assert eq.tolist() == [[1000, 1050, 1000, 1100]]
    eq = equity_paths(r, MCConfig(sizing="fixed_fraction", risk_pct=1.0, start_balance=1000))
    assert eq[0] == pytest.approx([1000, 1010, 999.9, 1019.898])


def test_account_cannot_go_below_zero():
    r = np.array([[-1.0] * 10])
    eq = equity_paths(r, MCConfig(sizing="fixed_amount", risk_amount=300, start_balance=1000))
    assert eq.min() == 0 and eq[0, -1] == 0


def test_streaks_drawdown_recovery_and_limit():
    assert longest_streak(np.array([[1, 1, 0, 1, 1, 1, 0]], bool)).tolist() == [3]
    r = np.array([[1.0, -1.0, -1.0, -1.0, 1.0, 1.0, 1.0, 1.0]])
    cfg = MCConfig(sizing="fixed_amount", risk_amount=100, start_balance=1000, loss_limit_pct=20)
    eq = equity_paths(r, cfg)      # 1000 1100 1000 900 800 900 1000 1100 1200
    dd, hit, streak, rec = path_stats(eq, r, cfg)
    assert dd[0] == pytest.approx(300 / 1100 * 100)
    assert rec[0] == 3             # from 800 (trade 4) back to 1100 (trade 7)
    assert streak[0] == 3
    assert bool(hit[0]) is True    # touched 800 = start - 20%


def test_shuffle_keeps_total_and_bootstrap_matches_mean():
    rng = np.random.default_rng(0)
    hist = rng.normal(0.1, 1.0, 200)
    sh = simulate_r(hist, 200, 500, "shuffle", rng)
    assert np.allclose(sh.sum(axis=1), hist.sum())
    bs = simulate_r(hist, 200, 4000, "bootstrap", rng)
    assert bs.mean() == pytest.approx(hist.mean(), abs=0.02)


def test_parameter_uncertainty_is_wider_for_small_samples():
    hist = manual_r(40, 2.0, 1.0, 30)
    cfg = dict(n_trades=100, n_sims=4000, sizing="fixed_amount", risk_amount=10, start_balance=1000)
    boot = run(hist, MCConfig(method="bootstrap", **cfg))
    pert = run(hist, MCConfig(method="perturb", **cfg))
    spread = lambda res: np.percentile(res.ending, 95) - np.percentile(res.ending, 5)  # noqa: E731
    assert spread(pert) > spread(boot)


def test_run_summary_is_sensible():
    res = run(manual_r(50, 2.0, 1.0, 200), MCConfig(n_trades=200, n_sims=3000, start_balance=1000, risk_pct=1))
    s = res.summary
    assert s["prob_profit"] > 0.95 and s["median_end"] > 1000 and s["p5_end"] < s["median_end"] < s["p95_end"]
    assert res.equity_pct[5][0] == res.equity_pct[95][0] == 1000
    losing = run(manual_r(20, 1.0, 1.0, 200), MCConfig(n_trades=200, n_sims=3000, start_balance=1000, risk_pct=2))
    assert losing.summary["prob_limit"] > 0.9


def test_edge_test_verdicts():
    rng = np.random.default_rng(3)
    assert edge_test(rng.normal(0.5, 1, 300)).verdict.startswith("Distinguishable")
    zero = rng.normal(0.0, 1, 300)
    assert edge_test(zero - zero.mean()).verdict.startswith("NOT")   # exactly zero average
    assert edge_test(rng.normal(-0.5, 1, 300)).verdict.startswith("Negative")
    assert edge_test([1.0, -1.0] * 5).verdict.startswith("Too few")
    # the same result that passes alone can fail once you account for testing 25 variations
    z = rng.normal(0, 1, 400)
    borderline = (z - z.mean()) / z.std(ddof=1) + 2.33 / np.sqrt(400)   # mean = 2.33 standard errors above zero
    assert edge_test(borderline, n_tests=1).prob_mean_le_zero < 0.05
    assert not edge_test(borderline, n_tests=25).verdict.startswith("Distinguishable")


def test_kelly_matches_formula_and_refuses_negative_edges():
    # 50% wins of +2R, 50% losses of -1R: Kelly f* = p - q/b = 0.5 - 0.5/2 = 0.25
    assert kelly_fraction(manual_r(50, 2.0, 1.0, 1000)) == pytest.approx(0.25, abs=0.002)
    assert kelly_fraction(manual_r(30, 2.0, 1.0, 100)) == 0.0
    rows = sizing_table(manual_r(50, 2.0, 1.0, 200), MCConfig(n_trades=100, start_balance=1000))
    labels = [r["Sizing"] for r in rows]
    assert labels[0] == "Your rule" and "Half Kelly" in labels
    full = next(r for r in rows if r["Sizing"] == "Full Kelly")
    quarter = next(r for r in rows if r["Sizing"] == "Quarter Kelly")
    assert full["Median max drawdown %"] > quarter["Median max drawdown %"]


def test_manual_r():
    r = manual_r(40, 2.5, 1.0, 50)
    assert len(r) == 50 and (r > 0).sum() == 20 and r.max() == 2.5 and r.min() == -1.0


def test_pattern_backtest_trade_table():
    from edgelab.backtest.patterns_bt import equity_r, max_drawdown_r, summary, trades_frame, trades_per_day
    from edgelab.brokers import BUILTIN_BROKERS
    from edgelab.config import Settings
    from edgelab.data.sample import generate_bars
    from edgelab.data.sessions import FOREX_SESSION
    from edgelab.fx import FxConverter
    from edgelab.instruments import get_instrument
    from edgelab.patterns import analyse

    df = generate_bars("EURUSD", start="2026-01-05", end="2026-03-01")
    a = analyse(df, FOREX_SESSION, get_instrument("EURUSD"), 5, BUILTIN_BROKERS["OANDA"])
    t = trades_frame(a, "EURUSD", "5m", Settings(), FxConverter(), "sample", True)
    filled = [o for o in a.outcomes if o is not None and o.filled and o.result in ("win", "loss", "timeout")]
    assert len(t) == len(filled) and set(t["sample"]) == {"in-sample", "out-of-sample"}
    assert (t["risk_fixed_size"] > 0).all()   # 1 mini lot risk in GBP
    total = summary(t).iloc[0]
    assert total["Trades"] == len(t) and total["Avg R"] == pytest.approx(t["r"].mean())
    assert max_drawdown_r(equity_r(t)) >= 0 and trades_per_day(t) > 0
    strict = trades_frame(a, "EURUSD", "5m", Settings(), FxConverter(), "sample", True, alert_rules=True)
    assert 0 < len(strict) < len(t)
