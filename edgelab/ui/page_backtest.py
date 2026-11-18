import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from edgelab.backtest.patterns_bt import equity_r, max_drawdown_r, summary, trades_frame, trades_per_day
from edgelab.config import api_key
from edgelab.instruments import FOREX_SYMBOLS
from edgelab.patterns.base import PATTERNS, TRADEABLE
from edgelab.timeutil import timeframe_minutes
from edgelab.montecarlo import edge_test
from edgelab.ui import mc_view
from edgelab.ui.state import balance_summary, get_converter, get_settings, get_store, pattern_analysis


def pattern_trades(symbols: tuple, tf: str, alert_rules: bool) -> tuple[pd.DataFrame, list]:
    """Trade table for the given pairs (cached per dataset by pattern_analysis)."""
    from edgelab.live.scanner import pick_dataset

    s = get_settings()
    store = get_store()
    conv = get_converter()
    frames, sources = [], []
    for sym in symbols:
        ds = pick_dataset(store, sym)
        if ds is None:
            sources.append((sym, "no data", True))
            continue
        if timeframe_minutes(tf) < timeframe_minutes(ds["timeframe"]):
            sources.append((sym, f"{ds['name']} is {ds['timeframe']} - too coarse for {tf}", bool(ds["is_sample"])))
            continue
        a = pattern_analysis(int(ds["id"]), sym, ds["timeframe"], tf, s.session_for("forex").to_dict(),
                             s.broker().to_dict(), float(s.target_r), int(s.order_expiry_bars))
        frames.append(trades_frame(a, sym, tf, s, conv, ds["name"], bool(ds["is_sample"]), alert_rules))
        sources.append((sym, ds["name"], bool(ds["is_sample"])))
    df = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(not f.empty for f in frames) else pd.DataFrame()
    return df, sources


def render():
    s = get_settings()
    ccy = s.account_currency
    st.title("Backtests & Monte Carlo")
    st.caption("Every past occurrence of each pattern, replayed with the standard plan (stop entry, pattern stop, "
               f"{s.target_r:g}R target, spread and slippage charged, stop assumed first when a bar touches both). "
               "Then simulate thousands of possible futures from those results.")

    with st.form("bt_form"):
        c1, c2, c3 = st.columns([3, 1, 2])
        syms = c1.multiselect("Pairs", FOREX_SYMBOLS, default=s.watchlist)
        tf = c2.selectbox("Timeframe", ["5m", "15m", "1h"], index=1)
        rules = c3.checkbox("Only trades my alert rules would have flagged", value=True,
                            help="Same filters as the live scanner (patterns, quality, trading window).")
        c1, c2, c3 = st.columns([3, 2, 2])
        pats = c1.multiselect("Patterns", TRADEABLE, default=[], format_func=lambda k: PATTERNS[k].name,
                              placeholder="All patterns")
        dirs = c2.multiselect("Direction", ["long", "short"], default=["long", "short"])
        period = c3.radio("Period", ["all", "in-sample", "out-of-sample"], horizontal=True,
                          help="In-sample = first 70% of each pair's history; out-of-sample = the last 30%. If "
                               "results only look good in one of them, be suspicious.")
        go_ = st.form_submit_button("Run backtest", type="primary")
    if go_ or "bt_last" not in st.session_state:
        with st.spinner("Replaying every pattern on every pair (cached after the first run)..."):
            df, sources = pattern_trades(tuple(syms), tf, rules)
        st.session_state["bt_last"] = (df, sources, pats, dirs, period, tf)
    df, sources, pats, dirs, period, tf = st.session_state["bt_last"]

    _sources_box(sources)
    if df.empty:
        st.info("No trades found for this selection.")
        return
    view = df[df["direction"].isin(dirs)]
    if pats:
        keep = set(pats) | ({"bear_flag"} if "bull_flag" in pats else set())
        view = view[view["pattern"].isin(keep)]
    if period != "all":
        view = view[view["sample"] == period]
    if view.empty:
        st.info("No trades match these filters.")
        return
    synthetic = bool(view["synthetic"].any())

    # ---- headline
    tot = summary(view).iloc[0]
    cum = equity_r(view)
    st.subheader("Results")
    c = st.columns(6)
    c[0].metric("Trades", f"{int(tot['Trades']):,}")
    c[1].metric("Win rate", f"{tot['Win rate %']:.0f}%")
    c[2].metric("Average per trade", f"{tot['Avg R']:+.3f} R", help="After spread and slippage.")
    c[3].metric("Total", f"{tot['Total R']:+.1f} R")
    c[4].metric("Profit factor", f"{tot['Profit factor']:.2f}" if np.isfinite(tot["Profit factor"]) else "-")
    c[5].metric("Worst drawdown", f"{max_drawdown_r(cum):.1f} R")
    groups = view.groupby(["pattern", "direction"]).ngroups
    et = edge_test(view["r"], n_tests=groups)
    (st.success if et.verdict.startswith("Distinguishable") else
     st.error if et.verdict.startswith("Negative") else st.info)(f"**Edge check:** {et.verdict} {et.detail}")

    a, b = st.columns(2)
    with a:
        fig = go.Figure(go.Scatter(x=cum.index, y=cum.to_numpy(), mode="lines",
                                   line=dict(color=mc_view.BLUE_550, width=2),
                                   hovertemplate="%{x|%d %b %Y}<br>%{y:+.1f} R<extra></extra>"))
        fig.add_hline(y=0, line=dict(color=mc_view.AXIS, width=1))
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), plot_bgcolor="white",
                          title=dict(text="Cumulative result (R) over time", font=dict(size=14)))
        fig.update_xaxes(gridcolor=mc_view.GRID)
        fig.update_yaxes(gridcolor=mc_view.GRID, ticksuffix=" R")
        st.plotly_chart(fig, key="bt_equity")
    with b:
        st.plotly_chart(mc_view.r_histogram(view["r"], height=320), key="bt_hist")

    t1, t2, t3, t4 = st.tabs(["By pattern", "By pair", "By session", "In- vs out-of-sample"])
    fmt = {"Win rate %": "{:.1f}", "Avg R": "{:+.3f}", "95% low": "{:+.2f}", "95% high": "{:+.2f}",
           "Total R": "{:+.1f}", "Profit factor": "{:.2f}", "Trades": "{:.0f}"}
    with t1:
        st.dataframe(summary(view, ["pattern_name", "direction"]).style.format(fmt), hide_index=True)
        st.caption("If the 95% range of 'Avg R' includes zero, that row shows no evidence of an edge. Rows with "
                   "few trades will show wild numbers - that is noise.")
    with t2:
        st.dataframe(summary(view, ["symbol"]).style.format(fmt), hide_index=True)
    with t3:
        st.dataframe(summary(view, ["session"]).style.format(fmt), hide_index=True)
    with t4:
        st.dataframe(summary(df[df["direction"].isin(dirs) & (df["pattern"].isin(view["pattern"].unique()))],
                             ["sample"]).style.format(fmt), hide_index=True)
        st.caption("A real edge should look similar in both halves. A big drop out-of-sample suggests the in-sample "
                   "result was luck or overfitting.")

    # ---- Monte Carlo
    st.divider()
    st.subheader("🎲 Monte Carlo: what could the next stretch of trades look like?")
    tpd = trades_per_day(view)
    fixed = float(view["risk_fixed_size"].median()) if view["risk_fixed_size"].notna().any() else None
    bal = balance_summary().balance
    cfg = mc_view.controls("bt_mc", bal, s.risk_pct, fixed, ccy, default_trades=max(20, min(500, int(tpd * 21))))
    st.caption(f"This selection produced about {tpd:.1f} trades per trading day historically.")
    if st.button("🎲 Run Monte Carlo simulation", type="primary", key="bt_mc_go"):
        st.session_state["bt_mc_cfg"] = cfg
    if "bt_mc_cfg" in st.session_state:
        mc_view.show_results(view["r"].to_numpy(), st.session_state["bt_mc_cfg"], ccy, n_tests=groups,
                             trades_per_day=tpd, synthetic=synthetic, key="bt")

    with st.expander("Simulate your own numbers instead (manual stats or your trade log)"):
        _manual_mc(s, ccy, bal)


def _sources_box(sources):
    real = [x for x in sources if not x[2]]
    fake = [x for x in sources if x[2]]
    if fake:
        msg = ", ".join(sym for sym, _, _ in fake)
        hint = ("Use Data -> Download forex (OANDA) -> Download my watchlist." if api_key("OANDA_API_KEY")
                else "Connect OANDA (token in .env) and download your watchlist on the Data page.")
        st.warning(f"Using SYNTHETIC sample data for: {msg}. Results from it are for learning the tool only. {hint}")
    if real:
        st.success("Using your real data for: " + ", ".join(f"{sym} ({name})" for sym, name, _ in real))


def _manual_mc(s, ccy, bal):
    src = st.radio("Source", ["Manual stats", "My imported trade log"], horizontal=True, key="man_src")
    if src == "Manual stats":
        c1, c2, c3, c4 = st.columns(4)
        wr = c1.number_input("Win rate %", 1.0, 99.0, 40.0, 1.0, key="man_wr")
        aw = c2.number_input("Average win (R)", 0.1, 20.0, 2.0, 0.1, key="man_aw")
        alv = c3.number_input("Average loss (R)", 0.1, 5.0, 1.0, 0.1, key="man_al")
        n = c4.number_input("Based on how many trades?", 10, 5000, 50, 10, key="man_n",
                            help="Fewer trades = more uncertainty. Use the 'Parameter uncertainty' method to see it.")
        r = mc_view.manual_r(wr, aw, alv, int(n))
        exp = wr / 100 * aw - (1 - wr / 100) * alv
        st.caption(f"Expectancy: {exp:+.3f} R per trade before any extra costs. Breakeven win rate for "
                   f"{aw:g}R winners: {alv / (aw + alv) * 100:.0f}%.")
    else:
        t = get_store().load_trades()
        r = t["r_multiple"].dropna().to_numpy() if not t.empty and "r_multiple" in t else np.array([])
        st.caption(f"{len(r)} imported trades have an R value (they need a stop-loss price).")
    if len(r) < 5:
        st.info("Need at least 5 trades.")
        return
    cfg = mc_view.controls("man_mc", bal, s.risk_pct, None, ccy)
    if st.button("🎲 Run Monte Carlo on these numbers", key="man_go"):
        mc_view.show_results(r, cfg, ccy, key="man", compact=True)
