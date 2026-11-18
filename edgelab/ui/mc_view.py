"""Monte Carlo controls and results display (used by the Backtests page and the per-signal button)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from edgelab.montecarlo import MCConfig, edge_test, kelly_fraction, run, sizing_table

# Sequential blue ramp + status red (dataviz reference palette)
BLUE_100, BLUE_250, BLUE_400, BLUE_550 = "#cde2fb", "#86b6ef", "#3987e5", "#1c5cab"
CRITICAL = "#d03b3b"
GRID, AXIS, MUTED = "#e1e0d9", "#c3c2b7", "#6b6a63"

METHODS = {
    "bootstrap": "Bootstrap - future trades drawn at random from past results",
    "perturb": "Parameter uncertainty - also varies win rate and average win/loss within their error margins",
    "shuffle": "Shuffle - the same past trades in random order (shows the luck of the sequence)",
}


def _money(x: float, ccy: str) -> str:
    return ("£" if ccy == "GBP" else ccy + " ") + f"{x:,.0f}"


def controls(key: str, balance: float, risk_pct: float, fixed_risk: float | None, ccy: str,
             default_trades: int = 100) -> MCConfig:
    c1, c2, c3 = st.columns(3)
    method = c1.selectbox("Method", list(METHODS), format_func=lambda m: METHODS[m].split(" - ")[0],
                          key=f"{key}_m", help="\n\n".join(METHODS.values()))
    n_trades = c2.number_input("Trades into the future", 10, 2000, int(default_trades), 10, key=f"{key}_n",
                               help="How many future trades to simulate. Pre-filled from how often this setup "
                                    "occurred historically (about a month).")
    n_sims = c3.select_slider("Number of simulated futures", [1000, 2000, 5000, 10000], value=5000, key=f"{key}_s")
    c1, c2, c3 = st.columns(3)
    sizing_opts = ["fixed_fraction"] + (["fixed_amount"] if fixed_risk else [])
    sizing = c1.radio("Position sizing", sizing_opts, key=f"{key}_z", horizontal=False,
                      format_func={"fixed_fraction": f"Risk {risk_pct:.2f}% of current balance each trade",
                                   "fixed_amount": f"Fixed 1 mini lot (about {_money(fixed_risk or 0, ccy)} risk/trade)"}.get)
    start = c2.number_input(f"Starting balance ({ccy})", 50.0, 10_000_000.0, float(round(balance, 2)), 100.0,
                            key=f"{key}_b")
    limit = c3.number_input("Loss limit (% of starting balance)", 1.0, 100.0, 20.0, 1.0, key=f"{key}_l",
                            help="The 'risk of ruin' line: the chance of losing this much at ANY point.")
    return MCConfig(n_trades=int(n_trades), n_sims=int(n_sims), start_balance=float(start), sizing=sizing,
                    risk_pct=risk_pct, risk_amount=float(fixed_risk or 0), loss_limit_pct=float(limit),
                    method=method)


def fan_chart(res, ccy: str, height: int = 360) -> go.Figure:
    x = np.arange(res.config.n_trades + 1)
    p = res.equity_pct
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=p[95], line=dict(width=0), hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(x=x, y=p[5], fill="tonexty", fillcolor=BLUE_100, line=dict(width=0),
                             name="90% of futures (5th-95th percentile)", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=p[75], line=dict(width=0), hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(x=x, y=p[25], fill="tonexty", fillcolor=BLUE_250, line=dict(width=0),
                             name="Middle 50% (25th-75th)", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=p[50], line=dict(color=BLUE_550, width=2), name="Median future",
                             customdata=np.stack([p[5], p[95]], axis=1),
                             hovertemplate="After %{x} trades<br>median %{y:,.0f}<br>90%% range %{customdata[0]:,.0f}"
                                           " - %{customdata[1]:,.0f}<extra></extra>"))
    sb = res.config.start_balance
    floor = sb * (1 - res.config.loss_limit_pct / 100)
    fig.add_hline(y=sb, line=dict(color=AXIS, width=1, dash="dot"),
                  annotation_text=f"start {_money(sb, ccy)}", annotation_position="top left",
                  annotation_font=dict(size=10, color=MUTED))
    fig.add_hline(y=floor, line=dict(color=CRITICAL, width=1.5, dash="dash"),
                  annotation_text=f"loss limit {_money(floor, ccy)}", annotation_position="bottom left",
                  annotation_font=dict(size=10, color=MUTED))
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10), hovermode="x",
                      title=dict(text="Range of possible account balances", font=dict(size=14)),
                      legend=dict(orientation="h", y=-0.2, font_size=11), plot_bgcolor="white")
    fig.update_xaxes(title="Trades from now", gridcolor=GRID, linecolor=AXIS)
    fig.update_yaxes(tickprefix="£" if ccy == "GBP" else "", gridcolor=GRID, linecolor=AXIS)
    return fig


def dd_histogram(res, height: int = 300) -> go.Figure:
    fig = go.Figure(go.Histogram(x=res.max_dd_pct, nbinsx=40, marker=dict(color=BLUE_400, line=dict(color="white", width=1)),
                                 hovertemplate="Max drawdown %{x}<br>%{y} futures<extra></extra>"))
    fig.add_vline(x=res.config.loss_limit_pct, line=dict(color=CRITICAL, dash="dash", width=1.5),
                  annotation_text="loss limit", annotation_font=dict(size=10, color=MUTED))
    fig.add_vline(x=float(np.percentile(res.max_dd_pct, 95)), line=dict(color=MUTED, dash="dot", width=1),
                  annotation_text="95% are better than this", annotation_position="top right",
                  annotation_font=dict(size=10, color=MUTED))
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10), bargap=0.05, plot_bgcolor="white",
                      title=dict(text="Worst drop from a peak (max drawdown)", font=dict(size=14)), showlegend=False)
    fig.update_xaxes(title="% below the previous peak", ticksuffix="%", gridcolor=GRID, linecolor=AXIS)
    fig.update_yaxes(title="Futures", gridcolor=GRID, linecolor=AXIS)
    return fig


def r_histogram(r, height: int = 280, title: str = "Results of past trades") -> go.Figure:
    r = np.asarray(r, float)
    fig = go.Figure()
    fig.add_trace(go.Histogram(x=r[r <= 0], name="Losing trades", marker=dict(color=CRITICAL), xbins=dict(size=0.25),
                               hovertemplate="%{x} R<br>%{y} trades<extra></extra>"))
    fig.add_trace(go.Histogram(x=r[r > 0], name="Winning trades", marker=dict(color=BLUE_400), xbins=dict(size=0.25),
                               hovertemplate="%{x} R<br>%{y} trades<extra></extra>"))
    fig.update_layout(height=height, barmode="overlay", margin=dict(l=10, r=10, t=30, b=10), plot_bgcolor="white",
                      title=dict(text=title, font=dict(size=14)), legend=dict(orientation="h", y=-0.25))
    fig.update_xaxes(title="Result in R (multiples of the amount risked, after costs)", gridcolor=GRID, linecolor=AXIS)
    fig.update_yaxes(title="Trades", gridcolor=GRID, linecolor=AXIS)
    return fig


def show_results(r_hist, cfg: MCConfig, ccy: str, *, n_tests: int = 1, trades_per_day: float | None = None,
                 synthetic: bool = False, key: str = "", compact: bool = False) -> None:
    r_hist = np.asarray(r_hist, float)
    r_hist = r_hist[np.isfinite(r_hist)]
    if len(r_hist) < 5:
        st.warning(f"Only {len(r_hist)} past trades - not enough to simulate anything meaningful.")
        return
    with st.spinner("Simulating futures..."):
        res = run(r_hist, cfg)
        et = edge_test(r_hist, n_tests=n_tests)
    s = res.summary
    if synthetic:
        st.warning("Simulated from SYNTHETIC sample-data trades. This shows how the tool works, not what a real "
                   "edge looks like. Connect OANDA and download data for real results.")
    # ---- headline
    (st.success if et.verdict.startswith("Distinguishable") else
     st.error if et.verdict.startswith("Negative") else st.info)(f"**Is there an edge?** {et.verdict} {et.detail}")
    c = st.columns(4)
    c[0].metric("Chance of being up", f"{s['prob_profit'] * 100:.0f}%", help=f"After {cfg.n_trades} trades.")
    c[1].metric("Typical outcome (median)", _money(s["median_end"], ccy), f"{s['median_return_pct']:+.1f}%")
    c[2].metric("Bad case (worst 5%)", _money(s["p5_end"], ccy),
                help="1 in 20 simulated futures ended at or below this.")
    c[3].metric(f"Chance of losing {cfg.loss_limit_pct:.0f}%+", f"{s['prob_limit'] * 100:.1f}%",
                help="Risk of ruin: the share of futures that fell to the loss limit at any point.")
    c = st.columns(4)
    c[0].metric("Typical worst drawdown", f"{s['median_dd']:.1f}%", help="Median of the largest peak-to-trough drop.")
    c[1].metric("Bad-case drawdown (95th pct)", f"{s['p95_dd']:.1f}%")
    c[2].metric("Longest losing streak", f"{s['median_streak']:.0f} in a row",
                help="Typical (median) longest run of consecutive losing trades.")
    c[2].caption(f"Bad case: {s['p95_streak']:.0f} in a row")
    rec = s["median_recovery"]
    tpd = trades_per_day or 0
    c[3].metric("Time to recover from the worst drop", "-" if not np.isfinite(rec) else f"{rec:.0f} trades",
                help=f"{s['prob_not_recovered'] * 100:.0f}% of futures had not recovered by the end of the horizon.")
    if np.isfinite(rec) and tpd:
        c[3].caption(f"About {rec / tpd:.0f} trading days at the historical rate")
    # ---- reality check: the data is also consistent with no edge at all
    if not et.verdict.startswith("Distinguishable") and et.n >= 30:
        zero = run(r_hist - r_hist.mean(), cfg).summary
        st.warning(
            f"**Reality check - if the true edge is zero** (equally consistent with this data): chance of being up "
            f"{zero['prob_profit'] * 100:.0f}%, typical outcome {_money(zero['median_end'], ccy)} (costs already "
            f"included), bad case {_money(zero['p5_end'], ccy)}, chance of losing {cfg.loss_limit_pct:.0f}%+ "
            f"{zero['prob_limit'] * 100:.1f}%. The simulation above assumes the past average "
            f"({r_hist.mean():+.3f} R) simply continues - that is the optimistic case, not the expected one.")
    # ---- charts
    if compact:
        st.plotly_chart(fan_chart(res, ccy, height=320), key=f"fan_{key}")
    else:
        a, b = st.columns(2)
        a.plotly_chart(fan_chart(res, ccy), key=f"fan_{key}")
        b.plotly_chart(dd_histogram(res), key=f"dd_{key}")
    # ---- sizing comparison
    k = kelly_fraction(r_hist)
    with st.expander("What if I risked more or less per trade? (including Kelly sizing)", expanded=not compact):
        rows = sizing_table(r_hist, cfg)
        df = pd.DataFrame(rows)
        for col in ("Median ending", "Worst 5% ending"):
            df[col] = df[col].map(lambda v: _money(v, ccy))
        for col in ("Median max drawdown %", "Chance of hitting loss limit %"):
            df[col] = df[col].map(lambda v: f"{v:.1f}")
        st.dataframe(df, hide_index=True)
        if k <= 0:
            st.caption("Kelly sizing is **zero** for these results: with no positive average, the growth-optimal bet "
                       "is not to trade this setup at all.")
        else:
            st.caption(f"Full Kelly would risk {k * 100:.1f}% per trade - the size that maximises long-run growth IF "
                       "the past results were exactly the future. They never are, so professionals use a quarter to a "
                       "half of Kelly at most; full Kelly brings brutal drawdowns.")
    st.caption(f"{METHODS[cfg.method]}. Based on {len(r_hist)} past trades, {cfg.n_sims:,} simulated futures of "
               f"{cfg.n_trades} trades. Results in R include spread and slippage. A simulation can only re-arrange "
               "the past: if the past results came from luck or from conditions that have changed, so will these.")


def manual_r(win_rate: float, avg_win: float, avg_loss: float, n: int) -> np.ndarray:
    """A trade list that exactly matches manual stats (for 'what if' simulations)."""
    n_w = int(round(win_rate / 100 * n))
    return np.r_[np.full(n_w, avg_win), np.full(n - n_w, -abs(avg_loss))]


def signal_mc_section(pattern: str, direction: str, symbol: str, tf: str, key: str) -> None:
    """The per-signal Monte Carlo button: simulate futures from this setup's own history."""
    from edgelab.patterns.base import PATTERNS
    from edgelab.ui.page_backtest import pattern_trades
    from edgelab.ui.state import balance_summary, get_settings

    flag = f"mc_open_{key}"
    if not st.session_state.get(flag):
        if st.button("🎲 Monte Carlo for this setup", key=f"mc_btn_{key}",
                     help="Simulate thousands of possible futures from this pattern's past results to see the range "
                          "of outcomes, drawdowns and risk of hitting your loss limit."):
            st.session_state[flag] = True
            st.rerun()
        return
    s = get_settings()
    st.markdown(f"#### 🎲 Monte Carlo: {PATTERNS[pattern].name} ({direction}) on {tf}")
    c1, c2 = st.columns([3, 1])
    scope = c1.radio("History to simulate from", ["This pair", "All my watchlist pairs"], horizontal=True,
                     key=f"mc_scope_{key}", help="More pairs = more trades = less noise, but pairs behave differently.")
    if c2.button("Close", key=f"mc_close_{key}"):
        st.session_state[flag] = False
        st.rerun()
    syms = (symbol,) if scope == "This pair" else tuple(dict.fromkeys([symbol] + list(s.watchlist)))
    df, sources = pattern_trades(syms, tf, False)
    sub = df[(df["pattern"] == pattern) & (df["direction"] == direction)] if not df.empty else df
    if sub.empty:
        st.info("No finished past trades for this setup yet.")
        return
    from edgelab.backtest.patterns_bt import trades_per_day

    fixed = float(sub["risk_fixed_size"].median()) if sub["risk_fixed_size"].notna().any() else None
    tpd = trades_per_day(sub)
    st.caption(f"{len(sub)} past trades from: " + ", ".join(f"{a} ({b})" for a, b, _ in sources) +
               f". This setup occurred about {tpd:.2f} times per trading day.")
    cfg = controls(f"mc_{key}", balance_summary().balance, s.risk_pct, fixed, s.account_currency,
                   default_trades=max(20, min(300, int(tpd * 63))))
    show_results(sub["r"].to_numpy(), cfg, s.account_currency, trades_per_day=tpd,
                 synthetic=bool(sub["synthetic"].any()), key=key, compact=True)
