import numpy as np
import pandas as pd
import pytest

from edgelab.data.sessions import FOREX_SESSION
from edgelab.instruments import get_instrument
from edgelab.learning.replay import Decision, evaluate_decision, hindsight_label, pick_start
from edgelab.patterns.detectors import make_context

EURUSD = get_instrument("EURUSD")


def ctx_from(closes, rng=0.0004):
    c = np.asarray(closes, float)
    o = np.r_[c[0], c[:-1]]
    idx = pd.date_range("2026-01-14 08:00", periods=len(c), freq="5min", tz="UTC")
    df = pd.DataFrame({"open": o, "high": np.maximum(o, c) + rng / 2, "low": np.minimum(o, c) - rng / 2, "close": c,
                       "volume": 100.0}, index=idx)
    return make_context(df, FOREX_SESSION, EURUSD, 5)


def test_hindsight_labels():
    flat = [1.1] * 40
    up = flat + list(np.linspace(1.1, 1.11, 20))
    ctx = ctx_from(up)
    assert hindsight_label(ctx.h, ctx.l, ctx.c, 39, float(ctx.atr[39]), 20)[0] == "up"
    ctx = ctx_from(flat + list(np.linspace(1.1, 1.09, 20)))
    assert hindsight_label(ctx.h, ctx.l, ctx.c, 39, float(ctx.atr[39]), 20)[0] == "down"
    ctx = ctx_from(flat + [1.1] * 20)
    assert hindsight_label(ctx.h, ctx.l, ctx.c, 39, float(ctx.atr[39]), 20)[0] == "chop"


def test_long_into_a_rally_wins_and_scores_well():
    ctx = ctx_from([1.1] * 40 + list(np.linspace(1.1, 1.12, 30)))
    entry = ctx.o[40]
    res = evaluate_decision(ctx, 39, Decision("long", entry - 0.0010, entry + 0.0030), 30, cost_price=0.0001)
    assert res.read_correct and res.result == "win"
    assert res.r == pytest.approx(3.0 - 0.1)
    assert res.score >= 80


def test_short_into_a_rally_loses_and_wrong_side_stop_rejected():
    ctx = ctx_from([1.1] * 40 + list(np.linspace(1.1, 1.12, 30)))
    entry = ctx.o[40]
    res = evaluate_decision(ctx, 39, Decision("short", entry + 0.0010, entry - 0.0020), 30, cost_price=0.0)
    assert not res.read_correct and res.result == "loss" and res.r == pytest.approx(-1.0, abs=0.05)
    bad = evaluate_decision(ctx, 39, Decision("long", entry + 0.0010, entry + 0.0030), 30, cost_price=0.0)
    assert bad.result == "no trade" and not bad.discipline[0][0]


def test_no_trade_in_chop_is_rewarded_and_discipline_flags():
    ctx = ctx_from([1.1] * 80)
    res = evaluate_decision(ctx, 39, Decision("none"), 30, cost_price=0.0)
    assert res.read_correct and res.score == 100
    entry = ctx.o[40]
    tight = evaluate_decision(ctx, 39, Decision("long", entry - 0.00001, entry + 0.00001), 30, 0.0,
                              risk_check=lambda e, s: (False, "too big"))
    msgs = [m for ok, m in tight.discipline if not ok]
    assert any("inside normal noise" in m for m in msgs) and any("1.5" in m for m in msgs) and "too big" in msgs


def test_pick_start_modes():
    from edgelab.brokers import BUILTIN_BROKERS
    from edgelab.data.sample import generate_bars
    from edgelab.patterns import analyse

    df = generate_bars("EURUSD", start="2026-01-05", end="2026-02-01")
    a = analyse(df, FOREX_SESSION, EURUSD, 5, BUILTIN_BROKERS["OANDA"])
    rng = np.random.default_rng(0)
    i = pick_start(a.ctx, rng, "random", history=120, future=40)
    assert 120 <= i < len(df) - 40 and a.ctx.in_window[i]
    j = pick_start(a.ctx, rng, "pattern", a.signals, history=120, future=40)
    assert any(s.i == j for s in a.signals)


def test_attempts_store(tmp_path):
    from edgelab.data.store import Store

    st = Store(tmp_path / "x.db")
    st.add_attempt({"symbol": "EURUSD", "timeframe": "15m", "bar_ts": pd.Timestamp("2026-01-14 09:00", tz="UTC"),
                    "mode": "Random moment", "decision": "none", "hindsight": "chop", "read_correct": 1,
                    "result": "no trade", "r": 0.0, "score": 100, "pattern": "none", "synthetic": 1})
    t = st.load_attempts()
    assert len(t) == 1 and t["bar_time"].iloc[0] == pd.Timestamp("2026-01-14 09:00", tz="UTC")
