import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from edgelab.indicators.levels import support_resistance
from edgelab.instruments import get_instrument
from edgelab.learning.concepts import BY_KEY, CONCEPTS
from edgelab.learning.glossary import GLOSSARY
from edgelab.ui.charts import ChartOptions, build_chart, signal_figure
from edgelab.ui.state import get_settings, get_store, pattern_analysis


def _analysis(sym, tf="15m"):
    from edgelab.live.scanner import pick_dataset

    s = get_settings()
    ds = pick_dataset(get_store(), sym)
    if ds is None:
        return None, None
    return pattern_analysis(int(ds["id"]), sym, ds["timeframe"], tf, s.session_for("forex").to_dict(),
                            s.broker().to_dict(), float(s.target_r), int(s.order_expiry_bars)), ds


def render():
    s = get_settings()
    st.title("Concepts library")
    st.caption("The ideas behind reading charts and managing risk - each one with a live example from your own data "
               "or a calculator to play with, and a couple of questions to test yourself.")
    titles = {c.key: c.title for c in CONCEPTS}
    c1, c2 = st.columns([3, 1])
    key = c1.selectbox("Concept", list(titles), format_func=titles.get, key="cc_key")
    sym = c2.selectbox("Example pair", s.watchlist, key="cc_sym")
    c = BY_KEY[key]
    st.header(c.title)
    st.markdown(c.body)
    st.subheader("See it" if c.example not in ("expectancy", "sizing", "costs", "drawdown", "overfitting") else "Try it")
    try:
        EXAMPLES[c.example](sym, s)
    except Exception as e:  # noqa: BLE001 - an example must never break the page
        st.info(f"Example unavailable for this data ({e}). Try another pair.")
    _test_yourself(c)


# ------------------------------------------------------------------------------------------------ examples
def _pick(a, pattern, rng=None, after=24):
    rng = rng or np.random.default_rng()
    cands = [x for x in a.signals if (x.pattern == pattern or (pattern == "flags" and "flag" in x.pattern))
             and 80 < x.i < len(a.ctx.c) - after - 1 and x.plan is not None and not x.cautions]
    cands = cands or [x for x in a.signals if x.pattern == pattern and 80 < x.i < len(a.ctx.c) - after - 1]
    return cands[int(rng.integers(len(cands)))] if cands else None


def _signal_example(sym, s, pattern, note):
    a, ds = _analysis(sym)
    sig = _pick(a, pattern)
    if sig is None:
        st.info("No example of this pattern in the data for this pair - try another pair.")
        return
    inst = get_instrument(sym)
    reveal = st.toggle("Show what happened next", key=f"cc_rev_{pattern}")
    st.plotly_chart(signal_figure(a.ctx.df, sig, s.display_tz, inst.decimals, after=24 if reveal else 0, height=460,
                                  title=f"{sym} 15m - {sig.title}"), key=f"cc_sig_{pattern}_{reveal}")
    st.markdown(note + "\n\n" + "\n".join(f"- ✅ {r}" for r in sig.reasons[:4]))
    if ds["is_sample"]:
        st.caption("Synthetic sample data - download real data for real examples.")
    if st.button("🔁 Show me another example", key=f"cc_again_{pattern}"):
        st.rerun()


def ex_structure(sym, s):
    a, _ = _analysis(sym)
    sig = _pick(a, "bos")
    if sig is None:
        st.info("No example here.")
        return
    lo = max(0, sig.i - 120)
    view = a.ctx.df.iloc[lo:sig.i + 1]
    inst = get_instrument(sym)
    fig = build_chart(view, ChartOptions(overlays={"structure"}, panels=[], decimals=inst.decimals, height=460),
                      tz=s.display_tz, swings=a.swings, signals=[sig], offset=lo)
    st.plotly_chart(fig, key="cc_structure")
    st.markdown("Follow the labels left to right: each **HH/HL** (green) is a step up, each **LH/LL** (red) a step "
                f"down. The triangle marks a **{sig.title}**: " + "; ".join(sig.reasons[:2]) + ".")


def ex_sr(sym, s):
    a, _ = _analysis(sym)
    df = a.ctx.df.iloc[-400:]
    inst = get_instrument(sym)
    levels = support_resistance(df, (df["high"].max() - df["low"].min()) / 20, left=8, right=8)
    fig = build_chart(df, ChartOptions(overlays=set(), panels=[], levels=levels, decimals=inst.decimals, height=460),
                      tz=s.display_tz)
    st.plotly_chart(fig, key="cc_sr")
    st.markdown(f"{len(levels)} zones found from confirmed swing points over the last {len(df)} bars. Shaded zones = "
                "where price turned repeatedly; the number of touches is labelled.")


def ex_trend_range(sym, s):
    a, _ = _analysis(sym, "5m")
    df = a.ctx.df
    g = df.groupby("day").agg(o=("open", "first"), h=("high", "max"), l=("low", "min"), c=("close", "last"),
                              n=("close", "size"))
    g = g[g["n"] >= g["n"].median() * 0.8]
    eff = (g["c"] - g["o"]).abs() / (g["h"] - g["l"])
    trend_day, range_day = eff.idxmax(), eff.idxmin()
    inst = get_instrument(sym)
    c1, c2 = st.columns(2)
    for col, day, name in ((c1, trend_day, "Trend day"), (c2, range_day, "Range day")):
        view = df[df["day"] == day]
        fig = build_chart(view, ChartOptions(overlays={"vwap", "sessions"}, panels=[], decimals=inst.decimals, height=380),
                          tz=s.display_tz, title=f"{name}: {pd.Timestamp(day):%a %d %b}")
        col.plotly_chart(fig, key=f"cc_{name}")
        col.caption(f"Net move {eff[day] * 100:.0f}% of the day's range.")
    st.markdown("Left: price travels one way and closes near the extreme. Right: lots of movement, little progress, "
                "repeated VWAP crossings.")


def ex_sessions(sym, s):
    a, _ = _analysis(sym, "5m")
    df = a.ctx.df
    days = pd.DatetimeIndex(df["day"].unique())
    view = df[df["day"] == days[-2]]
    inst = get_instrument(sym)
    fig = build_chart(view, ChartOptions(overlays={"sessions"}, panels=["volume"], decimals=inst.decimals, height=480),
                      tz=s.display_tz, title=f"{sym} 5m - {pd.Timestamp(days[-2]):%a %d %b}")
    st.plotly_chart(fig, key="cc_sessions")
    st.markdown("Orange = Asia, blue = London, green = New York. Compare the volume bars and candle sizes in each.")


def ex_expectancy(sym, s):
    c1, c2, c3 = st.columns(3)
    wr = c1.slider("Win rate %", 10, 90, 40)
    aw = c2.slider("Average win (R)", 0.2, 5.0, 2.0, 0.1)
    al = c3.slider("Average loss (R)", 0.2, 3.0, 1.0, 0.1)
    exp = wr / 100 * aw - (1 - wr / 100) * al
    be = al / (aw + al) * 100
    c = st.columns(3)
    c[0].metric("Expectancy per trade", f"{exp:+.2f} R")
    c[1].metric("Break-even win rate", f"{be:.0f}%")
    c[2].metric("Over 100 trades (average)", f"{exp * 100:+.0f} R")
    rng = np.random.default_rng(1)
    paths = np.cumsum(np.where(rng.random((30, 100)) < wr / 100, aw, -al), axis=1)
    fig = go.Figure([go.Scatter(y=p, mode="lines", line=dict(width=1, color="#86b6ef"), showlegend=False,
                                hoverinfo="skip") for p in paths])
    fig.add_trace(go.Scatter(y=np.median(paths, axis=0), mode="lines", line=dict(width=2.5, color="#1c5cab"),
                             name="median"))
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), plot_bgcolor="white",
                      title=dict(text="30 possible sequences of 100 trades (R)", font=dict(size=14)))
    st.plotly_chart(fig, key="cc_exp")
    st.caption("Same edge, very different journeys: that's variance. Monte Carlo on the Backtests page does this properly.")


def ex_sizing(sym, s):
    from edgelab.risk import pip_value, units_for_risk, value_per_price_unit
    from edgelab.ui.state import balance_summary, get_converter

    inst = get_instrument(sym)
    conv = get_converter()
    c1, c2, c3 = st.columns(3)
    bal = c1.number_input("Balance (£)", 100.0, 1e7, float(max(100.0, round(balance_summary().balance, 2))), 100.0)
    risk = c2.number_input("Risk per trade %", 0.05, 5.0, float(s.risk_pct), 0.05)
    stop = c3.number_input("Stop distance (pips)", 1.0, 500.0, 20.0, 1.0)
    price = float(conv.reference.get(sym, inst.typical_price))
    vpu = value_per_price_unit(inst, 1.0, price, conv, s.account_currency) or 0
    units = units_for_risk(bal * risk / 100, stop * inst.pip, vpu, 1000)
    pv = pip_value(inst, 10_000, price, conv, s.account_currency) or 0
    c = st.columns(3)
    c[0].metric("Risk amount", f"£{bal * risk / 100:,.2f}")
    c[1].metric("Position size", f"{units:,} units", f"{units / 100_000:.2f} lots", delta_color="off")
    c[2].metric("1 mini lot would risk", f"£{pv * stop:,.2f}", f"{pv * stop / bal * 100:.2f}% of balance", delta_color="off")
    st.caption(f"{sym}: 1 pip on 10,000 units is worth about £{pv:.2f}. Size = £{bal * risk / 100:,.2f} / "
               f"({stop:g} pips x £{pv / 10_000:.6f} per pip per unit).")


def ex_costs(sym, s):
    b = s.broker()
    c1, c2, c3 = st.columns(3)
    spread = c1.number_input("Spread (pips)", 0.0, 10.0, float(b.spread_for(sym)), 0.1)
    slip = c2.number_input("Slippage per side (pips)", 0.0, 5.0, float(b.slippage_pips), 0.1)
    rr = c3.number_input("Target (R)", 0.5, 5.0, float(s.target_r), 0.5)
    stops = np.array([3, 5, 8, 10, 15, 20, 30, 50])
    cost_r = (spread + 2 * slip) / stops
    need = (1 + cost_r) / (rr + 1)           # break-even win rate with costs: p*(rr - c) = (1-p)*(1 + c)
    df = pd.DataFrame({"Stop (pips)": stops, "Costs as R": cost_r.round(3), "Costs as % of risk": (cost_r * 100).round(0),
                       "Break-even win rate %": (need * 100).round(1)})
    st.dataframe(df, hide_index=True)
    st.caption(f"Without costs a {rr:g}R target breaks even at {100 / (rr + 1):.1f}% wins. Tight stops push that up fast.")


def ex_drawdown(sym, s):
    from edgelab.montecarlo import MCConfig, run
    from edgelab.ui.mc_view import fan_chart, manual_r

    c1, c2 = st.columns(2)
    wr = c1.slider("Win rate %", 20, 70, 40, key="dd_wr")
    rr = c2.slider("Reward (R)", 0.5, 4.0, 2.0, 0.5, key="dd_rr")
    r = manual_r(wr, rr, 1.0, 500)
    res = run(r, MCConfig(n_trades=200, n_sims=3000, start_balance=10_000, risk_pct=s.risk_pct))
    st.plotly_chart(fan_chart(res, s.account_currency, height=320), key="cc_dd")
    sm = res.summary
    st.markdown(f"With {wr}% winners at {rr:g}R and {s.risk_pct}% risk: typical worst drawdown **{sm['median_dd']:.1f}%**, "
                f"bad case **{sm['p95_dd']:.1f}%**; typical longest losing streak **{sm['median_streak']:.0f}** "
                f"(bad case {sm['p95_streak']:.0f}).")


def ex_overfitting(sym, s):
    rng = np.random.default_rng(int(st.number_input("Random seed", 0, 9999, 7)))
    n_strats = st.slider("How many random 'strategies' to try", 5, 500, 100)
    # each 'strategy' is pure luck: coin-flip trades with zero true edge
    is_r = np.where(rng.random((n_strats, 100)) < 1 / 3, 2.0, -1.0)
    oos_r = np.where(rng.random((n_strats, 100)) < 1 / 3, 2.0, -1.0)
    best = int(np.argmax(is_r.mean(axis=1)))
    c = st.columns(3)
    c[0].metric("Best in-sample average", f"{is_r[best].mean():+.2f} R/trade")
    c[1].metric("Same strategy out-of-sample", f"{oos_r[best].mean():+.2f} R/trade")
    c[2].metric("True edge of every strategy", "0.00 R")
    fig = go.Figure(go.Histogram(x=is_r.mean(axis=1), nbinsx=30, marker=dict(color="#3987e5")))
    fig.add_vline(x=is_r[best].mean(), line=dict(color="#d03b3b", dash="dash"), annotation_text="the one you'd pick")
    fig.update_layout(height=280, margin=dict(l=10, r=10, t=30, b=10), plot_bgcolor="white", showlegend=False,
                      title=dict(text="In-sample results of strategies that are all pure luck", font=dict(size=14)))
    st.plotly_chart(fig, key="cc_of")
    st.caption("Picking the best of many tries guarantees an impressive-looking backtest. The out-of-sample number is "
               "the honest one - which is why the Edge builder tracks how many variations you test.")


EXAMPLES = {
    "structure": ex_structure, "sr": ex_sr, "trend_range": ex_trend_range, "sessions": ex_sessions,
    "vwap": lambda sym, s: _signal_example(sym, s, "vwap_reclaim", "A VWAP reclaim/loss from your data:"),
    "orb": lambda sym, s: _signal_example(sym, s, "orb", "An opening-range breakout from your data:"),
    "liquidity": lambda sym, s: _signal_example(sym, s, "failed_breakout", "A failed breakout (trapped traders):"),
    "volume": lambda sym, s: _signal_example(sym, s, "volume_climax", "A volume climax from your data:"),
    "expectancy": ex_expectancy, "sizing": ex_sizing, "costs": ex_costs, "drawdown": ex_drawdown,
    "overfitting": ex_overfitting,
}


def _test_yourself(c):
    from edgelab.learning.quiz import concept_question

    st.subheader("Test yourself")
    terms = [t for t in c.terms if t in GLOSSARY][:2]
    for t in terms:
        key = f"cc_q_{c.key}_{t}"
        if key not in st.session_state:
            st.session_state[key] = concept_question(np.random.default_rng(), t)
        q = st.session_state[key]
        ans = st.radio(q.prompt, list(range(len(q.options))), index=None, format_func=lambda k: q.options[k],
                       key=f"{key}_r")
        if ans is not None:
            ok = ans == q.answer
            done = f"{key}_logged"
            if not st.session_state.get(done):
                get_store().add_quiz_attempt("concept", t, ok, q.options[ans], q.options[q.answer])
                st.session_state[done] = True
            (st.success if ok else st.error)("Correct." if ok else f"Not quite - it's: {q.options[q.answer]}")
    if c.quiz_topic and st.button("Practise this on real charts (Learn -> Quiz)"):
        st.session_state["qz_topic"] = c.quiz_topic
        st.info("Open **Learn: replay & quiz -> Quiz**: the next question will drill this pattern.")
