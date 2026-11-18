import numpy as np
import pandas as pd
import pytest

from edgelab.brokers import BUILTIN_BROKERS, BrokerProfile
from edgelab.data.sample import generate_bars
from edgelab.data.sessions import FOREX_SESSION
from edgelab.instruments import get_instrument
from edgelab.patterns import analyse
from edgelab.patterns.base import PATTERNS, Signal, TradePlan
from edgelab.patterns.detectors import detect_all, make_context
from edgelab.patterns.plan import build_plan, evaluate
from edgelab.patterns.stats import summarise

EURUSD = get_instrument("EURUSD")
BROKER = BrokerProfile("T", spreads_pips={"EURUSD": 1.0}, slippage_pips=0.0)


def frame(ohlc, start="2026-01-14 08:00", freq="5min", volume=100.0):
    a = np.asarray(ohlc, dtype=float)
    idx = pd.date_range(start, periods=len(a), freq=freq, tz="UTC")
    v = np.full(len(a), volume) if np.isscalar(volume) else np.asarray(volume, float)
    return pd.DataFrame({"open": a[:, 0], "high": a[:, 1], "low": a[:, 2], "close": a[:, 3], "volume": v}, index=idx)


def walk(n, start=1.1000, step=0.0, rng=0.0010, seed=0):
    """n bars of mild noise around a drift; returns list of (o,h,l,c)."""
    r = np.random.default_rng(seed)
    out, c = [], start
    for _ in range(n):
        o = c
        c = o + step + r.normal(0, rng * 0.2)
        out.append((o, max(o, c) + rng * 0.3, min(o, c) - rng * 0.3, c))
    return out


def sigs_of(df, pattern, direction=None, bar_minutes=5):
    s, _, _ = detect_all(df, FOREX_SESSION, EURUSD, bar_minutes)
    return [x for x in s if x.pattern == pattern and (direction is None or x.direction == direction)]


# ------------------------------------------------------------------ no look-ahead
@pytest.mark.parametrize("tf_minutes", [5, 15])
def test_detectors_never_use_future_bars(tf_minutes):
    from edgelab.data.resample import resample_ohlcv

    df = generate_bars("GBPUSD", start="2026-02-02", end="2026-03-07")
    if tf_minutes != 5:
        df = resample_ohlcv(df, tf_minutes, FOREX_SESSION)
    full, sw_full, _ = detect_all(df, FOREX_SESSION, get_instrument("GBPUSD"), tf_minutes)
    cut = int(len(df) * 0.6)
    part, sw_part, _ = detect_all(df.iloc[:cut], FOREX_SESSION, get_instrument("GBPUSD"), tf_minutes)
    key = lambda s: (s.pattern, s.direction, s.i, round(s.stop_ref or 0, 8), round(s.entry_ref or 0, 8))  # noqa: E731
    assert sorted(map(key, [s for s in full if s.i < cut])) == sorted(map(key, part))
    assert [w for w in sw_full if w[1] < cut] == sw_part
    assert len(part) > 50  # the test is meaningful only if patterns were found


# ------------------------------------------------------------------ hand-built patterns
def test_bullish_engulfing_after_decline():
    bars = walk(30)
    c = bars[-1][3]
    for _ in range(7):                       # steady decline
        bars.append((c, c + 0.0002, c - 0.0012, c - 0.0010))
        c -= 0.0010
    bars.append((c, c + 0.0002, c - 0.0008, c - 0.0006))          # small red bar
    c -= 0.0006
    bars.append((c - 0.0001, c + 0.0022, c - 0.0003, c + 0.0020))  # big green body engulfing it
    found = sigs_of(frame(bars), "engulfing", "long")
    assert found and found[-1].i == len(bars) - 1
    assert found[-1].stop_ref == pytest.approx(min(bars[-1][2], bars[-2][2]))


def test_bearish_pin_bar_at_new_high():
    bars = walk(30)
    c = bars[-1][3]
    bars.append((c, c + 0.0030, c - 0.0002, c - 0.0001))  # long upper wick, tiny body, new 10-bar high
    found = sigs_of(frame(bars), "pin_bar", "short")
    assert found and found[-1].i == len(bars) - 1 and found[-1].entry_ref == pytest.approx(bars[-1][2])


def _two_days(today):
    """Day 1 (22:00 Jan 12 -> 22:00 Jan 13 UTC): range 1.1000-1.1020. Then `today` bars."""
    day1 = []
    for k in range(288):
        mid = 1.1010 + 0.0008 * np.sin(k / 10)
        day1.append((mid, mid + 0.0002, mid - 0.0002, mid + 0.0001))
    return frame(day1 + today, start="2026-01-12 22:00")


def test_prior_day_breakout_and_failed_breakout():
    pdh = max(b[1] for b in _two_days([]).itertuples(index=False, name=None) if True)
    today = [(1.1010, 1.1012, 1.1008, 1.1010)] * 100
    today += [(1.1010, pdh + 0.0010, 1.1009, pdh + 0.0008)]        # closes above yesterday's high
    today += [(pdh + 0.0008, pdh + 0.0009, pdh - 0.0006, pdh - 0.0005)]  # and straight back below it
    df = _two_days(today)
    bo = sigs_of(df, "breakout_pd", "long")
    assert bo and bo[0].levels["Prior-day high"] == pytest.approx(pdh)
    fb = sigs_of(df, "failed_breakout", "short")
    assert fb and fb[0].i == len(df) - 1 and fb[0].stop_ref >= pdh + 0.0010 - 1e-9


def test_bull_flag():
    bars = walk(40, rng=0.0004)
    c = bars[-1][3]
    for _ in range(6):                      # pole: +6 x 0.0012
        bars.append((c, c + 0.0013, c - 0.0001, c + 0.0012))
        c += 0.0012
    top = c
    for k in range(5):                      # flag: shallow, quiet drift down
        o = c
        c = o - 0.0003
        bars.append((o, o + 0.0001, c - 0.0001, c))
    flag_high = max(b[1] for b in bars[-5:])
    bars.append((c, flag_high + 0.0008, c - 0.0001, flag_high + 0.0006))  # break above the flag
    f = sigs_of(frame(bars), "bull_flag", "long")
    assert f and f[-1].i == len(bars) - 1
    assert f[-1].zone[3] == pytest.approx(flag_high) and f[-1].stop_ref < top


def test_break_of_structure_in_uptrend():
    # zig-zag uptrend: higher highs and higher lows, then a close above the last high
    path = []
    levels = [1.1000, 1.1030, 1.1015, 1.1045, 1.1030, 1.1060, 1.1045, 1.1075]
    for a, b in zip(levels[:-1], levels[1:]):
        path += list(np.linspace(a, b, 12, endpoint=False))
    path += list(np.linspace(1.1075, 1.1060, 12))
    path += [1.1065, 1.1072, 1.1080, 1.1090]
    bars = [(p, p + 0.0002, p - 0.0002, p) for p in path]
    bars = [(bars[k - 1][3] if k else b[0], max(b[1], bars[k - 1][3] if k else b[0]), min(b[2], bars[k - 1][3] if k else b[0]), b[3])
            for k, b in enumerate(bars)]
    s = sigs_of(frame(bars), "bos", "long")
    assert s, "expected a bullish break of structure"
    assert s[-1].levels["Swing high broken"] == pytest.approx(1.1075 + 0.0002, abs=3e-4)


def test_opening_range_breakout_in_london():
    # 08:00-08:30 UK range 1.1000-1.1010, then a close above at 08:45
    bars = [(1.1005, 1.1010, 1.1000, 1.1005)] * 6 + [(1.1005, 1.1008, 1.1002, 1.1006)] * 2
    bars += [(1.1006, 1.1020, 1.1005, 1.1018)]
    pre = [(1.1005, 1.1007, 1.1003, 1.1005)] * 96   # 00:00-08:00
    df = frame(pre + bars, start="2026-01-14 00:00")
    orb = sigs_of(df, "orb", "long")
    assert orb and orb[0].ts == pd.Timestamp("2026-01-14 08:40", tz="UTC")
    assert orb[0].levels["Opening range high"] == pytest.approx(1.1010)


# ------------------------------------------------------------------ trade plans and outcomes
def _ctx(bars):
    return make_context(frame(bars), FOREX_SESSION, EURUSD, 5)


def test_plan_long_uses_stop_entry_buffer_and_2r():
    bars = [(1.1, 1.1010, 1.0990, 1.1005)] * 30
    ctx = _ctx(bars)
    sig = Signal("engulfing", "long", 29, ctx.df.index[29], 1.1005, stop_ref=1.0990)
    p = build_plan(sig, ctx, target_r=2.0, expiry=3, spread_pips=1.0)
    buf = max(0.0001, 0.1 * ctx.atr[29])
    assert p.order_type == "BUY STOP" and p.entry == pytest.approx(1.1010 + buf, abs=1e-5)
    assert p.stop == pytest.approx(1.0990 - buf, abs=1e-5)
    assert p.target - p.entry == pytest.approx(2 * (p.entry - p.stop), abs=2e-5)


def test_plan_enforces_minimum_stop_distance():
    bars = [(1.1, 1.1010, 1.0990, 1.1005)] * 30
    ctx = _ctx(bars)
    sig = Signal("pin_bar", "short", 29, ctx.df.index[29], 1.1005, stop_ref=1.0990)  # stop below a short entry?!
    p = build_plan(sig, ctx, spread_pips=1.0)
    assert p.stop - p.entry >= 0.5 * ctx.atr[29] - 1e-9


def _eval(bars_after, plan, cost=0.0):
    pre = [(1.1, 1.1001, 1.0999, 1.1)] * 20
    ctx = _ctx(pre + bars_after)
    sig = Signal("engulfing", plan.side, 19, ctx.df.index[19], 1.1, stop_ref=plan.stop, plan=plan)
    return evaluate(sig, ctx, max_hold=10, cost_price=cost)


PLAN = TradePlan("long", "BUY STOP", 1.1010, 1.1000, 1.1030, 2.0, 3)


def test_outcome_win_loss_and_stop_first_rule():
    win = _eval([(1.1, 1.1012, 1.1001, 1.1011), (1.1011, 1.1031, 1.1008, 1.1030)], PLAN)
    assert win.result == "win" and win.r == pytest.approx(2.0)
    loss = _eval([(1.1, 1.1012, 1.1005, 1.1006), (1.1006, 1.1007, 1.0995, 1.0996)], PLAN)
    assert loss.result == "loss" and loss.r == pytest.approx(-1.0)
    both = _eval([(1.1, 1.1012, 1.1005, 1.1010), (1.1010, 1.1035, 1.0995, 1.1020)], PLAN)
    assert both.result == "loss"  # touched stop and target in one bar -> assume the worst


def test_outcome_not_filled_gap_and_costs():
    nf = _eval([(1.1, 1.1005, 1.0999, 1.1003)] * 5, PLAN)
    assert not nf.filled and nf.result == "not filled"
    gap = _eval([(1.1015, 1.1018, 1.1012, 1.1016)] + [(1.1016, 1.1017, 1.1014, 1.1016)] * 12, PLAN, cost=0.0002)
    assert gap.filled and gap.result == "timeout"
    assert gap.r == pytest.approx((1.1016 - 1.1015) / 0.0010 - 0.2)   # filled at the open (worse), minus costs


def test_stats_verdicts():
    from edgelab.patterns.plan import Outcome

    mk = lambda r: Outcome(True, "win" if r > 0 else "loss", r, 0, 0, 1, 1, 2, 1.0, 0.0)  # noqa: E731
    assert "Not enough" in summarise("engulfing", "long", [mk(1.0)] * 5).verdict
    noise = summarise("engulfing", "long", [mk(2.0), mk(-1.0), mk(-1.0)] * 20)
    assert "NOT distinguishable" in noise.verdict and noise.win_rate == pytest.approx(1 / 3)
    bad = summarise("engulfing", "long", [mk(-1.0)] * 30 + [mk(0.5)] * 5)
    assert "below zero" in bad.verdict


def test_analyse_on_sample_year_is_fast_and_honest():
    import time

    df = generate_bars("EURUSD", start="2025-11-03", end="2026-05-01")
    t = time.time()
    a = analyse(df, FOREX_SESSION, EURUSD, 5, BUILTIN_BROKERS["OANDA"])
    assert time.time() - t < 10
    assert {s.pattern for s in a.signals} >= {"engulfing", "pin_bar", "orb", "bos", "bull_flag", "vwap_reclaim"}
    assert all(s.plan is not None for s in a.signals if s.direction in ("long", "short"))
    big = [st for st in a.stats.values() if st.n_filled >= 200]
    # synthetic random data: no pattern should show a strong positive edge after costs
    assert big and all(st.avg_r < 0.3 for st in big)
    assert set(PATTERNS) >= {s.pattern for s in a.signals}
