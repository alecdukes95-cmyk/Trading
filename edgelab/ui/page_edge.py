import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from edgelab.backtest import engine as E
from edgelab.backtest.rules import CROSS_LINES, STOP_TYPES, TARGET_TYPES, TRIGGERS, Strategy
from edgelab.data.sessions import SESSION_CODE_LABELS
from edgelab.montecarlo import edge_test
from edgelab.patterns.base import PATTERNS, TRADEABLE
from edgelab.ui import mc_view
from edgelab.ui.page_settings import money, num
from edgelab.ui.state import balance_summary, get_converter, get_settings, get_store, pattern_analysis

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SESSIONS = sorted(set(SESSION_CODE_LABELS))


# ------------------------------------------------------------------------------------------------ state <-> strategy
def _load_into_state(s: Strategy) -> None:
    ss = st.session_state
    t, f, en, sp, tg, ex = s.trigger, s.filters, s.entry, s.stop, s.target, s.exits
    ss.update({"eb_name": s.name, "eb_dir": s.direction, "eb_kind": t["kind"],
               "eb_patterns": [p for p in t.get("patterns", ["orb"]) if p in TRADEABLE and p != "bear_flag"],
               "eb_line": t.get("line", "vwap"), "eb_n": int(t.get("n", 20)), "eb_fast": int(t.get("fast", 9)),
               "eb_slow": int(t.get("slow", 20)), "eb_rsi_lo": float(t.get("lower", 30)),
               "eb_rsi_hi": float(t.get("upper", 70)), "eb_rsi_mode": t.get("mode", "reversal"),
               "eb_h0": f["hours"][0], "eb_h1": f["hours"][1], "eb_wd": [WEEKDAY_NAMES[d] for d in f["weekdays"]],
               "eb_sessions": f.get("sessions", []), "eb_trend": f.get("trend", "any"),
               "eb_trend_n": int(f.get("trend_n", 200)), "eb_vol": float(f.get("min_vol_ratio", 0)),
               "eb_atr": tuple(f.get("atr_pct", [0, 100])), "eb_rsi_rng": tuple(f.get("rsi_range", [0, 100])),
               "eb_mpd": int(f.get("max_trades_per_day", 3)), "eb_entry": en.get("type", "next_open"),
               "eb_exp": int(en.get("expiry_bars", 3)), "eb_stop": sp["type"], "eb_stop_v": float(sp["value"]),
               "eb_tgt": tg["type"], "eb_tgt_v": float(tg["value"]), "eb_maxb": int(ex["max_bars"]),
               "eb_send": bool(ex.get("session_end", True)), "eb_be": float(ex.get("breakeven_at_r") or 0)})


def _from_state() -> Strategy:
    ss = st.session_state
    kind = ss.get("eb_kind", "pattern")
    trig = {"kind": kind}
    if kind == "pattern":
        trig["patterns"] = list(ss.get("eb_patterns", ["orb"]))
    elif kind == "cross":
        trig.update(line=ss.get("eb_line", "vwap"), n=int(ss.get("eb_n", 20)))
    elif kind == "ema_cross":
        trig.update(fast=int(ss.get("eb_fast", 9)), slow=int(ss.get("eb_slow", 20)))
    elif kind == "rsi":
        trig.update(n=int(ss.get("eb_n", 14)), lower=float(ss.get("eb_rsi_lo", 30)), upper=float(ss.get("eb_rsi_hi", 70)),
                    mode=ss.get("eb_rsi_mode", "reversal"))
    else:
        trig["n"] = int(ss.get("eb_n", 20))
    return Strategy(
        name=ss.get("eb_name", "My strategy"), direction=ss.get("eb_dir", "both"), trigger=trig,
        filters={"hours": [ss.get("eb_h0", "08:00"), ss.get("eb_h1", "17:00")],
                 "weekdays": [WEEKDAY_NAMES.index(d) for d in ss.get("eb_wd", WEEKDAY_NAMES[:5])],
                 "sessions": list(ss.get("eb_sessions", [])), "trend": ss.get("eb_trend", "any"),
                 "trend_n": int(ss.get("eb_trend_n", 200)), "min_vol_ratio": float(ss.get("eb_vol", 0.0)),
                 "atr_pct": list(ss.get("eb_atr", (0, 100))), "rsi_range": list(ss.get("eb_rsi_rng", (0, 100))),
                 "max_trades_per_day": int(ss.get("eb_mpd", 3))},
        entry={"type": ss.get("eb_entry", "next_open"), "expiry_bars": int(ss.get("eb_exp", 3))},
        stop={"type": ss.get("eb_stop", "atr"), "value": float(ss.get("eb_stop_v", 1.5))},
        target={"type": ss.get("eb_tgt", "r"), "value": float(ss.get("eb_tgt_v", 2.0))},
        exits={"max_bars": int(ss.get("eb_maxb", 48)), "session_end": bool(ss.get("eb_send", True)),
               "breakeven_at_r": float(ss.get("eb_be", 0.0))})


# ------------------------------------------------------------------------------------------------ page
def render():
    s = get_settings()
    st.title("Edge builder")
    st.caption("Describe a trading idea as explicit rules - no coding - and test it honestly: realistic costs, "
               "entries only after the signal bar closes, an untouched out-of-sample period, walk-forward testing, "
               "and a running count of how many variations you've tried.")
    if "eb_name" not in st.session_state:
        _load_into_state(Strategy())
    _library()
    left, right = st.columns([2, 3])
    with left:
        _form(s)
    strat = _from_state()
    with right:
        st.markdown(f"**Your rules:** {strat.describe()}")
        c1, c2, c3 = st.columns([3, 1, 2])
        syms = c1.multiselect("Pairs", s.watchlist, default=s.watchlist[:4], key="eb_syms")
        tf = c2.selectbox("Timeframe", ["5m", "15m", "1h"], index=1, key="eb_tf")
        isf = c3.slider("In-sample share (first part of the data)", 0.5, 0.9, 0.7, 0.05, key="eb_isf",
                        help="Rules are judged on the LAST part only (out-of-sample). Don't peek and re-tune.")
        t1, t2, t3 = st.tabs(["▶ Backtest", "🚶 Walk-forward", "🧱 Robustness grid"])
        with t1:
            _backtest_tab(s, strat, syms, tf, isf)
        with t2:
            _walk_forward_tab(s, strat, syms, tf)
        with t3:
            _grid_tab(s, strat, syms, tf, isf)


def _library():
    store = get_store()
    saved = store.list_strategies()
    with st.expander(f"📚 My strategies ({len(saved)} saved)"):
        c1, c2, c3, c4 = st.columns([3, 1, 1, 2])
        pick = c1.selectbox("Saved strategies", ["-"] + list(saved), key="eb_pick")
        if c2.button("Load", disabled=pick == "-"):
            _load_into_state(Strategy.from_json(saved[pick]))
            st.rerun()
        if c3.button("Delete", disabled=pick == "-"):
            store.delete_strategy(pick)
            st.rerun()
        if c4.button("💾 Save current rules", type="primary"):
            strat = _from_state()
            store.save_strategy(strat.name, strat.to_json())
            st.success(f"Saved '{strat.name}'.")
        st.caption("Examples to start from:")
        ex = st.columns(4)
        presets = {
            "London ORB": Strategy(name="London ORB", trigger={"kind": "pattern", "patterns": ["orb"]},
                                   stop={"type": "pattern", "value": 1.5}),
            "VWAP reclaim with trend": Strategy(name="VWAP reclaim with trend", trigger={"kind": "cross", "line": "vwap"},
                                                filters={**Strategy().filters, "trend": "with_htf"}),
            "20-bar breakout": Strategy(name="20-bar breakout", trigger={"kind": "breakout", "n": 20},
                                        entry={"type": "stop", "expiry_bars": 3}),
            "RSI reversal (fade)": Strategy(name="RSI reversal (fade)",
                                            trigger={"kind": "rsi", "n": 14, "lower": 30, "upper": 70, "mode": "reversal"},
                                            target={"type": "r", "value": 1.5}),
        }
        for col, (name, strat) in zip(ex, presets.items()):
            if col.button(name, key=f"eb_preset_{name}"):
                _load_into_state(strat)
                st.rerun()


def _form(s):
    st.text_input("Strategy name", key="eb_name")
    st.radio("Direction", ["both", "long", "short"], horizontal=True, key="eb_dir")
    with st.expander("1. Entry trigger", expanded=True):
        kind = st.selectbox("Enter when...", list(TRIGGERS), format_func=TRIGGERS.get, key="eb_kind")
        if kind == "pattern":
            st.multiselect("Patterns", [k for k in TRADEABLE if k != "bear_flag"], key="eb_patterns",
                           format_func=lambda k: "Bull / bear flags" if k == "bull_flag" else PATTERNS[k].name)
        elif kind == "cross":
            line = st.selectbox("Line", list(CROSS_LINES), format_func=CROSS_LINES.get, key="eb_line")
            if line in ("ema", "sma"):
                st.number_input("n (bars)", 2, 500, key="eb_n")
        elif kind == "ema_cross":
            c1, c2 = st.columns(2)
            c1.number_input("Fast EMA", 2, 200, key="eb_fast")
            c2.number_input("Slow EMA", 3, 500, key="eb_slow")
        elif kind == "rsi":
            st.number_input("RSI length", 2, 100, key="eb_n")
            c1, c2 = st.columns(2)
            c1.number_input("Lower level", 1.0, 50.0, key="eb_rsi_lo")
            c2.number_input("Upper level", 50.0, 99.0, key="eb_rsi_hi")
            st.radio("Style", ["reversal", "momentum"], horizontal=True, key="eb_rsi_mode",
                     format_func={"reversal": "Reversal (buy back up through the low level)",
                                  "momentum": "Momentum (buy above the high level)"}.get)
        else:
            st.number_input("N bars", 2, 500, key="eb_n")
    with st.expander("2. Filters (all must be true)"):
        c1, c2 = st.columns(2)
        c1.text_input("From (UK time)", key="eb_h0")
        c2.text_input("To (UK time)", key="eb_h1")
        st.multiselect("Weekdays", WEEKDAY_NAMES, key="eb_wd")
        st.multiselect("Only in these sessions (blank = any)", SESSIONS, key="eb_sessions")
        st.selectbox("Trend filter", ["any", "with_sma", "with_htf", "against_htf"], key="eb_trend",
                     format_func={"any": "No trend filter", "with_sma": "With the trend (price vs SMA)",
                                  "with_htf": "With the higher-timeframe trend",
                                  "against_htf": "Against the higher-timeframe trend (fades)"}.get)
        if st.session_state.get("eb_trend") == "with_sma":
            st.number_input("SMA length", 10, 500, key="eb_trend_n")
        st.number_input("Minimum volume vs recent average (x, 0 = off)", 0.0, 10.0, step=0.1, key="eb_vol")
        st.slider("Volatility (ATR percentile vs the last 500 bars)", 0, 100, key="eb_atr")
        st.slider("RSI between", 0, 100, key="eb_rsi_rng")
        st.number_input("Max trades per day per pair (0 = no limit)", 0, 50, key="eb_mpd")
    with st.expander("3. Entry, stop, target and exits"):
        st.radio("Entry", ["next_open", "stop"], horizontal=True, key="eb_entry",
                 format_func={"next_open": "Next bar's open", "stop": "Stop order beyond the signal bar"}.get)
        if st.session_state.get("eb_entry") == "stop":
            st.number_input("Cancel if not filled after (bars)", 1, 20, key="eb_exp")
        c1, c2 = st.columns(2)
        c1.selectbox("Stop-loss", list(STOP_TYPES), format_func=STOP_TYPES.get, key="eb_stop")
        c2.number_input("Stop value", 0.1, 500.0, step=0.1, key="eb_stop_v",
                        help="ATR multiple, or pips. Ignored for 'signal bar' and 'pattern' stops.")
        c1, c2 = st.columns(2)
        c1.selectbox("Take-profit", list(TARGET_TYPES), format_func=TARGET_TYPES.get, key="eb_tgt")
        c2.number_input("Target value", 0.1, 1000.0, step=0.1, key="eb_tgt_v")
        st.number_input("Exit after at most (bars)", 1, 2000, key="eb_maxb")
        st.checkbox("Exit at the end of my trading window / day", key="eb_send")
        st.number_input("Move stop to breakeven at (R, 0 = never)", 0.0, 10.0, step=0.25, key="eb_be")


def _analyses(symbols, tf):
    from edgelab.live.scanner import pick_dataset
    from edgelab.timeutil import timeframe_minutes

    s = get_settings()
    out, sources = {}, []
    for sym in symbols:
        ds = pick_dataset(get_store(), sym)
        if ds is None or timeframe_minutes(tf) < timeframe_minutes(ds["timeframe"]):
            continue
        out[sym] = pattern_analysis(int(ds["id"]), sym, ds["timeframe"], tf, s.session_for("forex").to_dict(),
                                    s.broker().to_dict(), float(s.target_r), int(s.order_expiry_bars))
        sources.append((sym, ds["name"], bool(ds["is_sample"])))
    return out, sources


def _risk_amount(s):
    return max(1.0, balance_summary().balance * s.risk_pct / 100)


def _backtest_tab(s, strat, syms, tf, isf):
    if st.button("▶ Run backtest", type="primary", disabled=not syms):
        with st.spinner("Testing your rules on every pair..."):
            an, sources = _analyses(syms, tf)
            trades, spans = E.run_backtest(strat, an, s.broker(), risk_amount=_risk_amount(s), ccy=s.account_currency,
                                           conv=get_converter())
            ins, oos = E.split_multi(trades, spans, isf)
            mi = E.metrics(ins, s.risk_pct)
            mo = E.metrics(oos, s.risk_pct)
            key = f"{','.join(sorted(syms))}|{tf}"
            get_store().log_run(key, E.strategy_hash(strat), strat.name, "backtest", len(trades), mi.get("expectancy"),
                                mi.get("t_stat"), mo.get("expectancy"))
            st.session_state["eb_res"] = {"trades": trades, "ins": ins, "oos": oos, "spans": spans, "sources": sources,
                                          "key": key, "strat": strat.to_json(), "isf": isf}
    res = st.session_state.get("eb_res")
    if not res:
        st.info("Set your rules on the left, then **Run backtest**.")
        return
    if res["strat"] != strat.to_json():
        st.caption("⚠️ Rules changed since this result - run again to update.")
    fake = [x[0] for x in res["sources"] if x[2]]
    if fake:
        st.warning(f"SYNTHETIC sample data for {', '.join(fake)} - results are for learning the tool. Download real "
                   "data (Data page) for meaningful tests.")
    trades, ins, oos = res["trades"], res["ins"], res["oos"]
    if trades.empty:
        st.info("These rules produced no trades. Loosen the filters or pick another trigger.")
        return
    runs = get_store().runs(res["key"])
    n_tests = max(1, runs["strategy_hash"].nunique()) if not runs.empty else 1
    mi, mo, ma = E.metrics(ins, s.risk_pct), E.metrics(oos, s.risk_pct), E.metrics(trades, s.risk_pct)
    tab = pd.DataFrame({"In-sample": _fmt_metrics(mi), "Out-of-sample": _fmt_metrics(mo), "All": _fmt_metrics(ma)})
    st.dataframe(tab, width="stretch")
    et = edge_test(oos["r"], n_tests=n_tests) if len(oos) else None
    if et:
        (st.success if et.verdict.startswith("Distinguishable") else st.error if et.verdict.startswith("Negative")
         else st.info)(f"**Out-of-sample verdict:** {et.verdict} {et.detail}")
    st.markdown("**Overfitting check:** " + E.overfitting_note(mi.get("t_stat", np.nan), n_tests))
    if mi.get("trades", 0) and mo.get("trades", 0) and mi["expectancy"] > 0 and mo["expectancy"] < mi["expectancy"] / 2:
        st.warning("Out-of-sample results are less than half as good as in-sample - a classic sign the rules fit "
                   "past noise. Resist tuning them until they look good again on the out-of-sample part.")

    cum = trades.sort_values("exit_time")
    fig = go.Figure(go.Scatter(x=cum["exit_time"], y=cum["r"].cumsum(), mode="lines", line=dict(color="#1c5cab", width=2),
                               hovertemplate="%{x|%d %b %Y}<br>%{y:+.1f} R<extra></extra>"))
    if len(oos):
        fig.add_vrect(x0=oos["signal_time"].min(), x1=cum["exit_time"].max(), fillcolor="#fff8e1", opacity=0.6,
                      line_width=0, layer="below", annotation_text="out-of-sample", annotation_position="top left")
    fig.add_hline(y=0, line=dict(color="#c3c2b7", width=1))
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10), plot_bgcolor="white",
                      title=dict(text="Cumulative result (R) - all pairs", font=dict(size=14)))
    st.plotly_chart(fig, key="eb_eq")
    a, b = st.columns(2)
    a.plotly_chart(mc_view.r_histogram(trades["r"], height=260), key="eb_hist")
    with b:
        st.markdown("**How trades ended**")
        st.dataframe(trades["result"].value_counts().rename_axis("Exit").reset_index(name="Trades"), hide_index=True)
    labels = {"hour_uk": "By hour (UK)", "weekday": "By weekday", "session": "By session", "trend_regime": "By trend regime",
              "vol_regime": "By volatility regime", "symbol": "By pair", "side": "Long vs short"}
    tabs = st.tabs(list(labels.values()))
    fmt = {"Win rate %": "{:.0f}", "Avg R": "{:+.3f}", "Total R": "{:+.1f}"}
    for tb, by in zip(tabs, labels):
        with tb:
            st.dataframe(E.breakdown(trades, by).style.format(fmt), hide_index=True)
    with st.expander(f"All {len(trades)} trades"):
        show = trades.drop(columns=["signal_i"]).copy()
        for col in ("signal_time", "entry_time", "exit_time"):
            show[col] = show[col].dt.tz_convert(s.display_tz).dt.strftime("%d %b %y %H:%M")
        st.dataframe(show, hide_index=True)
        st.download_button("Download trades (CSV)", trades.to_csv(index=False).encode(), "edge_trades.csv", "text/csv")
    st.divider()
    st.markdown("#### 🎲 Monte Carlo on these results")
    which = st.radio("Use", ["out-of-sample", "all"], horizontal=True, key="eb_mc_which",
                     help="Out-of-sample trades are the honest basis; in-sample results are flattered by the design process.")
    r = (oos if which == "out-of-sample" else trades)["r"].to_numpy()
    cfg = mc_view.controls("eb_mc", balance_summary().balance, s.risk_pct, None, s.account_currency, default_trades=100)
    if st.button("🎲 Run Monte Carlo", key="eb_mc_go"):
        mc_view.show_results(r, cfg, s.account_currency, n_tests=n_tests, synthetic=bool(fake), key="eb", compact=True)


def _fmt_metrics(m: dict) -> dict:
    if not m.get("trades"):
        return {"Trades": "0"}
    return {"Trades": f"{m['trades']:,}", "Win rate": f"{m['win_rate'] * 100:.1f}%",
            "Average win": f"{m['avg_win']:+.2f} R", "Average loss": f"{m['avg_loss']:+.2f} R",
            "Expectancy (avg per trade)": f"{m['expectancy']:+.3f} R", "Total": f"{m['total_r']:+.1f} R",
            "Profit factor": num(m["profit_factor"], ".2f"), "Max drawdown": f"{m['max_dd_r']:.1f} R / {m['max_dd_pct']:.1f}%",
            "t-statistic": num(m["t_stat"], ".2f"), f"Return at your risk rule": f"{m['return_pct']:+.1f}%",
            "Average bars held": f"{m['avg_bars']:.1f}"}


def _parse_values(text: str, default: list) -> list:
    try:
        vals = [float(x) for x in text.replace(";", ",").split(",") if x.strip()]
        return vals or default
    except ValueError:
        return default


def _walk_forward_tab(s, strat, syms, tf):
    st.markdown("Walk-forward testing: for each period, choose the best setting using **only the data before it**, "
                "then record how that choice did on the period itself. Only those unseen results count - it's the "
                "closest a backtest gets to trading it live.")
    c1, c2, c3 = st.columns([2, 2, 1])
    param = c1.selectbox("Setting to optimise", list(E.PARAMS), format_func=lambda k: E.PARAMS[k][0], key="eb_wf_p")
    vals = _parse_values(c2.text_input("Values to try", ", ".join(f"{v:g}" for v in E.PARAMS[param][1]),
                                       key=f"eb_wf_v_{param}"), E.PARAMS[param][1])
    folds = c3.number_input("Periods", 2, 10, 5, key="eb_wf_f")
    if st.button("🚶 Run walk-forward", type="primary", disabled=not syms):
        with st.spinner(f"Testing {len(vals)} values x {folds} periods..."):
            an, _ = _analyses(syms, tf)
            vals_cast = [int(v) if param in ("exits.max_bars", "filters.trend_n", "trigger.n") else v for v in vals]
            wf = E.walk_forward(strat, an, s.broker(), param, vals_cast, folds=int(folds), risk_amount=_risk_amount(s),
                                ccy=s.account_currency, conv=get_converter())
            key = f"{','.join(sorted(syms))}|{tf}"
            for v in vals_cast:
                tr = wf["runs"][v]
                m = E.metrics(tr)
                get_store().log_run(key, E.strategy_hash(E.with_param(strat, param, v)), strat.name, "walk-forward",
                                    m.get("trades", 0), m.get("expectancy"), m.get("t_stat"), None)
            st.session_state["eb_wf"] = wf
    wf = st.session_state.get("eb_wf")
    if not wf:
        return
    folds_df = wf["folds"].copy()
    for col in ("Trained up to", "Tested until"):
        folds_df[col] = pd.to_datetime(folds_df[col]).dt.strftime("%d %b %Y")
    st.dataframe(folds_df.style.format({"In-sample avg R": "{:+.3f}", "Out-of-sample avg R": "{:+.3f}"}, na_rep="-"),
                 hide_index=True)
    oos = wf["oos_trades"]
    m = E.metrics(oos, s.risk_pct)
    best_h = max(wf["hindsight"], key=lambda v: wf["hindsight"][v] if np.isfinite(wf["hindsight"][v]) else -9)
    c = st.columns(3)
    c[0].metric("Walk-forward expectancy (unseen data)", f"{m.get('expectancy', 0):+.3f} R" if m.get("trades") else "-")
    c[1].metric("Trades", m.get("trades", 0))
    c[2].metric(f"Hindsight-best value ({best_h:g})", f"{wf['hindsight'][best_h]:+.3f} R",
                help="What you'd see if you picked the best value using ALL the data - flattering and unrealistic.")
    st.caption("The gap between the hindsight-best and the walk-forward result is the price of not knowing the future.")
    if m.get("trades"):
        cum = oos.sort_values("exit_time")
        fig = go.Figure(go.Scatter(x=cum["exit_time"], y=cum["r"].cumsum(), mode="lines",
                                   line=dict(color="#1c5cab", width=2)))
        fig.update_layout(height=260, margin=dict(l=10, r=10, t=30, b=10), plot_bgcolor="white",
                          title=dict(text="Walk-forward result on unseen periods (R)", font=dict(size=14)))
        st.plotly_chart(fig, key="eb_wf_eq")


def _grid_tab(s, strat, syms, tf, isf):
    st.markdown("A robust edge works across a **neighbourhood** of settings, not just one magic value. If only a "
                "single cell is good, it's probably luck.")
    keys = list(E.PARAMS)
    c1, c2 = st.columns(2)
    p1 = c1.selectbox("Setting A", keys, index=0, format_func=lambda k: E.PARAMS[k][0], key="eb_g1")
    p2 = c2.selectbox("Setting B", keys, index=1, format_func=lambda k: E.PARAMS[k][0], key="eb_g2")
    v1 = _parse_values(c1.text_input("A values", ", ".join(f"{v:g}" for v in E.PARAMS[p1][1]), key=f"eb_g1v_{p1}"),
                       E.PARAMS[p1][1])
    v2 = _parse_values(c2.text_input("B values", ", ".join(f"{v:g}" for v in E.PARAMS[p2][1]), key=f"eb_g2v_{p2}"),
                       E.PARAMS[p2][1])
    if p1 == p2:
        st.info("Pick two different settings.")
        return
    if st.button("🧱 Run grid", type="primary", disabled=not syms):
        with st.spinner(f"Running {len(v1) * len(v2)} backtests..."):
            an, _ = _analyses(syms, tf)
            cast = lambda p, v: int(v) if p in ("exits.max_bars", "filters.trend_n", "trigger.n") else v  # noqa: E731
            g = E.grid(strat, an, s.broker(), p1, [cast(p1, v) for v in v1], p2, [cast(p2, v) for v in v2],
                       risk_amount=_risk_amount(s), ccy=s.account_currency, is_fraction=isf, conv=get_converter())
            key = f"{','.join(sorted(syms))}|{tf}"
            for a in g["is"].index:
                for b in g["is"].columns:
                    get_store().log_run(key, E.strategy_hash(E.with_param(E.with_param(strat, p1, a), p2, b)),
                                        strat.name, "grid", int(g["n"].loc[a, b] or 0), g["is"].loc[a, b], None,
                                        g["oos"].loc[a, b])
            st.session_state["eb_grid"] = (g, p1, p2)
    res = st.session_state.get("eb_grid")
    if not res:
        return
    g, p1, p2 = res
    a, b = st.columns(2)
    for col, which, title in ((a, "is", "In-sample avg R"), (b, "oos", "Out-of-sample avg R")):
        tabl = g[which].astype(float)
        lim = float(np.nanmax(np.abs(tabl.to_numpy()))) if np.isfinite(tabl.to_numpy()).any() else 1.0
        fig = go.Figure(go.Heatmap(z=tabl.to_numpy(), x=[f"{v:g}" for v in tabl.columns], y=[f"{v:g}" for v in tabl.index],
                                   colorscale=[[0, "#d03b3b"], [0.5, "#f0efec"], [1, "#1c5cab"]], zmid=0, zmin=-lim, zmax=lim,
                                   text=np.vectorize(lambda v: "" if not np.isfinite(v) else f"{v:+.2f}")(tabl.to_numpy()),
                                   texttemplate="%{text}", hovertemplate="A %{y}, B %{x}: %{z:+.3f} R<extra></extra>",
                                   colorbar=dict(title="R")))
        fig.update_layout(height=340, margin=dict(l=10, r=10, t=40, b=10), title=dict(text=title, font=dict(size=14)))
        fig.update_xaxes(title=E.PARAMS[p2][0])
        fig.update_yaxes(title=E.PARAMS[p1][0])
        col.plotly_chart(fig, key=f"eb_grid_{which}")
    st.caption("Blue = positive average result after costs, red = negative, grey = around zero. Each cell is one more "
               "variation tested - it counts towards the overfitting check.")
