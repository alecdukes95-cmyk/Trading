import numpy as np
import pandas as pd
import pytest

from edgelab.backtest import engine as E
from edgelab.backtest.rules import Strategy, filter_mask, trigger_signals
from edgelab.brokers import BUILTIN_BROKERS, BrokerProfile
from edgelab.data.resample import resample_ohlcv
from edgelab.data.sample import generate_bars
from edgelab.data.sessions import FOREX_SESSION
from edgelab.instruments import get_instrument
from edgelab.patterns import analyse

EURUSD = get_instrument("EURUSD")
NO_COST = E.Costs(0.0, 0.0)


def analysis_from(closes, start="2026-01-14 08:00", spread=0.0002):
    c = np.asarray(closes, float)
    o = np.r_[c[0], c[:-1]]
    idx = pd.date_range(start, periods=len(c), freq="5min", tz="UTC")
    df = pd.DataFrame({"open": o, "high": np.maximum(o, c) + spread / 2, "low": np.minimum(o, c) - spread / 2,
                       "close": c, "volume": 100.0}, index=idx)
    return analyse(df, FOREX_SESSION, EURUSD, 5, BrokerProfile("T"))


@pytest.fixture(scope="module")
def sample_analyses():
    out = {}
    for sym in ("EURUSD", "GBPUSD"):
        df = resample_ohlcv(generate_bars(sym, start="2026-01-05", end="2026-05-01"), 15, FOREX_SESSION)
        out[sym] = analyse(df, FOREX_SESSION, get_instrument(sym), 15, BUILTIN_BROKERS["OANDA"])
    return out


def breakout_strategy(**kw):
    base = dict(trigger={"kind": "breakout", "n": 10}, direction="long",
                filters={**Strategy().filters, "hours": ["00:00", "24:00"], "max_trades_per_day": 0},
                stop={"type": "pips", "value": 10}, target={"type": "r", "value": 2.0},
                exits={"max_bars": 50, "session_end": False, "breakeven_at_r": 0.0})
    base.update(kw)
    return Strategy(**base)


def test_breakout_long_enters_next_open_and_hits_2r():
    closes = [1.1000] * 20 + [1.1010] + list(np.linspace(1.1012, 1.1060, 20))
    a = analysis_from(closes)
    tr = E.simulate(breakout_strategy(), a, "EURUSD", NO_COST)
    t = tr.iloc[0]
    assert t["signal_i"] == 20 and t["entry_time"] == a.ctx.df.index[21]     # entry one bar AFTER the signal
    assert t["entry"] == pytest.approx(a.ctx.o[21])
    assert t["result"] == "win" and t["r"] == pytest.approx(2.0)


def test_stop_first_costs_and_breakeven():
    closes = [1.1000] * 20 + [1.1010, 1.1011, 1.1000, 1.0990, 1.0980] + [1.0980] * 10
    a = analysis_from(closes)
    tr = E.simulate(breakout_strategy(), a, "EURUSD", NO_COST)
    assert tr.iloc[0]["result"] == "loss" and tr.iloc[0]["r"] == pytest.approx(-1.0, abs=0.05)
    costly = E.simulate(breakout_strategy(), a, "EURUSD", E.Costs(1.0, 0.5))
    assert costly.iloc[0]["r"] == pytest.approx(tr.iloc[0]["r"] - 2.0 / 10, abs=1e-9)   # 1 + 2x0.5 pips on a 10-pip stop
    up_then_down = [1.1000] * 20 + [1.1010] + list(np.linspace(1.1012, 1.1025, 8)) + list(np.linspace(1.1024, 1.0990, 10))
    a2 = analysis_from(up_then_down)
    be = E.simulate(breakout_strategy(exits={"max_bars": 50, "session_end": False, "breakeven_at_r": 1.0}), a2,
                    "EURUSD", NO_COST)
    assert be.iloc[0]["result"] == "breakeven" and be.iloc[0]["r"] == pytest.approx(0.0, abs=0.02)


def test_one_position_at_a_time_and_daily_cap(sample_analyses):
    a = sample_analyses["EURUSD"]
    s = Strategy(trigger={"kind": "breakout", "n": 5}, filters={**Strategy().filters, "max_trades_per_day": 2})
    tr = E.simulate(s, a, "EURUSD", NO_COST)
    t = tr.sort_values("entry_time")
    assert (t["entry_time"].iloc[1:].to_numpy() > t["exit_time"].iloc[:-1].to_numpy()).all()  # never overlapping
    from edgelab.data.sessions import trading_day

    per_day = pd.Series(trading_day(pd.DatetimeIndex(t["signal_time"]), FOREX_SESSION)).value_counts()
    assert per_day.max() <= 2


def test_no_look_ahead_in_strategy_signals(sample_analyses):
    """Signals computed with future bars available must equal signals computed without them."""
    from edgelab.data.sessions import FOREX_SESSION as F

    full = sample_analyses["GBPUSD"]
    df = full.ctx.df[["open", "high", "low", "close", "volume"]]
    cut = int(len(df) * 0.6)
    part = analyse(df.iloc[:cut], F, get_instrument("GBPUSD"), 15, BUILTIN_BROKERS["OANDA"])
    for trig in ({"kind": "cross", "line": "vwap"}, {"kind": "ema_cross", "fast": 9, "slow": 20},
                 {"kind": "rsi", "n": 14, "lower": 30, "upper": 70}, {"kind": "breakout", "n": 20},
                 {"kind": "pattern", "patterns": ["orb", "pin_bar"]}, {"kind": "cross", "line": "pd"}):
        s = Strategy(trigger=trig, filters={**Strategy().filters, "trend": "with_htf"})
        lf, sf, _ = trigger_signals(s, full)
        lp, sp, _ = trigger_signals(s, part)
        mf, mp = filter_mask(s, full, "long"), filter_mask(s, part, "long")
        assert (lf[:cut] == lp).all() and (sf[:cut] == sp).all(), trig
        assert (mf[:cut] == mp).all(), trig


def test_filters_hours_weekdays_direction(sample_analyses):
    a = sample_analyses["EURUSD"]
    s = Strategy(trigger={"kind": "breakout", "n": 10}, direction="short",
                 filters={**Strategy().filters, "hours": ["13:00", "17:00"], "weekdays": [1]})
    tr = E.simulate(s, a, "EURUSD", NO_COST)
    assert not tr.empty and set(tr["side"]) == {"short"} and set(tr["weekday"]) == {"Tue"}
    assert tr["hour_uk"].between(13, 17).all()


def test_split_metrics_walk_forward_and_grid(sample_analyses):
    b = BUILTIN_BROKERS["OANDA"]
    s = Strategy(trigger={"kind": "breakout", "n": 20})
    tr, spans = E.run_backtest(s, sample_analyses, b, risk_amount=65)
    ins, oos = E.split_multi(tr, spans, 0.7)
    assert len(ins) + len(oos) == len(tr) and ins["signal_time"].max() < oos["signal_time"].min() + pd.Timedelta(days=60)
    m = E.metrics(tr)
    assert m["trades"] == len(tr) and m["expectancy"] == pytest.approx(tr["r"].mean())
    assert 0 <= m["win_rate"] <= 1 and m["max_dd_r"] >= 0
    wf = E.walk_forward(s, sample_analyses, b, "stop.value", [1.0, 2.0], folds=3, risk_amount=65)
    assert len(wf["folds"]) == 3 and set(wf["folds"]["Chosen value"]) <= {1.0, 2.0}
    first_test_start = wf["folds"]["Trained up to"].iloc[0]
    assert (wf["oos_trades"]["signal_time"] > first_test_start).all()   # only unseen periods are scored
    g = E.grid(s, sample_analyses, b, "stop.value", [1.0, 2.0], "target.value", [1.0, 3.0], risk_amount=65)
    assert g["is"].shape == (2, 2) and np.isfinite(g["oos"].to_numpy()).all()


def test_overfitting_threshold_grows_with_tests():
    assert E.luck_threshold(1) == pytest.approx(1.96)
    assert E.luck_threshold(100) > E.luck_threshold(10) > 1.96
    assert "does NOT clear" in E.overfitting_note(2.2, 50) and "clears" in E.overfitting_note(4.0, 50)


def test_strategy_json_roundtrip_and_store(tmp_path):
    from edgelab.data.store import Store

    s = breakout_strategy(name="X")
    back = Strategy.from_json(s.to_json())
    assert back == s and "10-bar" in s.describe()
    st = Store(tmp_path / "s.db")
    st.save_strategy("X", s.to_json())
    st.save_strategy("X", s.to_json())          # update, not duplicate
    assert list(st.list_strategies()) == ["X"]
    st.log_run("EURUSD|15m", E.strategy_hash(s), "X", "backtest", 10, 0.1, 1.2, float("nan"))
    assert len(st.runs("EURUSD|15m")) == 1
    assert E.strategy_hash(s) == E.strategy_hash(breakout_strategy(name="renamed"))   # renaming isn't a new test
