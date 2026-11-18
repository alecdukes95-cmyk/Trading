import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from edgelab.analysis import add_indicators
from edgelab.instruments import FOREX_SYMBOLS, get_instrument
from edgelab.paper.behaviour import CHECKLIST, EMOTIONS, MISTAKES, behaviour_insights, pre_trade_checks, trades_frame
from edgelab.paper.service import describe
from edgelab.patterns.base import PATTERNS, TRADEABLE
from edgelab.risk import units_for_risk, value_per_price_unit
from edgelab.ui.charts import ChartOptions, build_chart
from edgelab.ui.page_settings import clamp, money, num
from edgelab.ui.state import balance_summary, get_paper, get_settings, get_store

SETUPS = [PATTERNS[k].name for k in TRADEABLE if k != "bear_flag"] + ["My own idea", "Alert from the scanner"]


def render():
    s = get_settings()
    svc = get_paper()
    st.title("Paper trading")
    st.caption("Place fake trades against real, live prices to practise the human side: planning, sizing, managing "
               "and reviewing trades. Everything happens inside EdgeLab - no order is ever sent to a broker. Closed "
               "paper trades go into your trade log (marked 'paper') and never touch your real balance.")
    _feed_controls(svc, s)
    if svc.feed is None:
        st.info("Start a price feed above. **Live** uses your OANDA practice token (or MetaTrader 5) for real "
                "bid/ask prices. **Replay** plays back stored history at speed - handy at weekends.")
        _tabs(svc, s)
        return
    _account_bar(svc, s)
    left, right = st.columns([3, 2])
    with left:
        c1, c2 = st.columns(2)
        sym = c1.selectbox("Pair", s.watchlist or FOREX_SYMBOLS, key="pp_sym")
        tf = c2.selectbox("Chart timeframe", ["1m", "5m", "15m", "1h"], index=1, key="pp_tf")
        svc.watch = {sym}
        _chart(svc, s, sym, tf)
    with right:
        _ticket(svc, s, sym)
    _positions(svc, s)
    _tabs(svc, s)


# ------------------------------------------------------------------------------------------------ feed
def _feed_controls(svc, s):
    from edgelab.data.providers import live_source_ready

    ok, desc = live_source_ready(s)
    with st.container(border=True):
        c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
        status = ("🟢 " + svc.feed.name) if svc.running else "⚪ stopped"
        c1.markdown(f"**Price feed:** {status}")
        if svc.last_update is not None:
            c1.caption(f"Last quote {svc.last_update.tz_convert(s.display_tz):%a %d %b %H:%M:%S}")
        if c2.button(f"▶ Live prices ({'MT5' if s.live_source == 'mt5' else 'OANDA'})", type="primary",
                     disabled=not ok, width="stretch"):
            from edgelab.data.providers import make_live_client
            from edgelab.paper.feeds import LiveFeed

            try:
                svc.start(LiveFeed(make_live_client(s), desc), interval=2.0)
            except Exception as e:  # noqa: BLE001
                st.error(f"Couldn't start the live feed: {e}")
            st.rerun()
        if not ok:
            c2.caption(desc)
        days = c3.number_input("Replay: start N days ago", 1, 120, 5, key="pp_days")
        if c3.button("▶ Replay practice", width="stretch"):
            feed = _replay_feed(s, int(days))
            if feed:
                svc.start(feed, interval=1.0)
                st.rerun()
        if c4.button("■ Stop", width="stretch", disabled=not svc.running):
            svc.stop()
            st.rerun()
        if svc.errors:
            with st.expander(f"Feed messages ({len(svc.errors)})"):
                for e in svc.errors[-8:]:
                    st.text(e)


def _replay_feed(s, days):
    from edgelab.live.scanner import pick_dataset
    from edgelab.paper.feeds import ReplayFeed
    from edgelab.ui.state import load_bars

    store = get_store()
    data, spreads = {}, {}
    for sym in dict.fromkeys(list(s.watchlist) + ["GBPUSD", "USDJPY", "EURUSD"]):
        ds = pick_dataset(store, sym)
        if ds is None:
            continue
        data[sym] = load_bars(int(ds["id"]))
        spreads[sym] = s.broker().spread_for(sym) * get_instrument(sym).pip
    if not data:
        st.error("No stored data to replay - download some on the Data page.")
        return None
    end = min(df.index[-1] for df in data.values())
    start = end - pd.Timedelta(days=days)
    st.caption("Replaying stored data from " + f"{start:%d %b %Y}. The chart never shows bars after the replay clock.")
    return ReplayFeed(data, spreads, start)


# ------------------------------------------------------------------------------------------------ account
@st.fragment(run_every="2s")
def _account_bar(svc, s):
    env = svc.env(s)
    snap = svc.book.snapshot(svc.quotes, env)
    ccy = env.ccy
    m0 = lambda x: ("£" if ccy == "GBP" else ccy + " ") + f"{x:,.0f}"  # noqa: E731 - whole pounds fit the tiles
    c = st.columns(6)
    c[0].metric("Paper balance", m0(snap["balance"]), help=money(snap["balance"], ccy))
    c[1].metric("Equity", m0(snap["equity"]), f"{snap['unrealised']:+,.2f} open P&L", help=money(snap["equity"], ccy))
    c[2].metric("Margin used", m0(snap["used_margin"]))
    c[3].metric("Free margin", m0(snap["free_margin"]))
    c[4].metric("Margin level", f"{snap['margin_level']:,.0f}%" if snap["margin_level"] else "-")
    closed = svc.book.positions("closed")
    c[5].metric("Closed paper trades", len(closed))
    seen = st.session_state.setdefault("_pp_seen", 0)
    new = svc.events[seen:] if seen <= len(svc.events) else svc.events
    for e in new[-3:]:
        st.toast(describe(e, ccy), icon="🧾")
    st.session_state["_pp_seen"] = len(svc.events)
    if new and any(e["type"] != "filled" for e in new):
        st.rerun(scope="app")


# ------------------------------------------------------------------------------------------------ chart
@st.fragment(run_every="3s")
def _chart(svc, s, sym, tf):
    inst = get_instrument(sym)
    try:
        df = svc.feed.chart(sym, tf, 160)
    except Exception as e:  # noqa: BLE001
        st.warning(f"Chart unavailable: {e}")
        return
    if df is None or df.empty:
        st.info("No candles yet for this pair.")
        return
    ind = add_indicators(df, s.session_for("forex"), intraday=True)
    fig = build_chart(ind, ChartOptions(overlays={"sessions", "vwap", "ema_fast", "ema_slow"}, panels=["volume"],
                                        decimals=inst.decimals, height=560), tz=s.display_tz, title=f"{sym} {tf} (live)")
    xs = list(ind.index.tz_convert(s.display_tz).strftime("%a %d %b %H:%M"))
    q = svc.quotes.get(sym)
    if q:
        for price, lab, col in ((q["bid"], "bid", "#b71c1c"), (q["ask"], "ask", "#1b5e20")):
            fig.add_shape(type="line", x0=xs[0], x1=xs[-1], y0=price, y1=price, line=dict(color=col, width=1, dash="dot"),
                          row=1, col=1)
        fig.add_annotation(x=xs[-1], y=q["ask"], text=f"ask {inst.fmt_price(q['ask'])}", showarrow=False,
                           xanchor="right", yanchor="bottom", font=dict(size=10, color="#1b5e20"), row=1, col=1)
        fig.add_annotation(x=xs[-1], y=q["bid"], text=f"bid {inst.fmt_price(q['bid'])}", showarrow=False,
                           xanchor="right", yanchor="top", font=dict(size=10, color="#b71c1c"), row=1, col=1)
    for p in svc.book.positions():
        if p["symbol"] != sym:
            continue
        for price, lab, col in ((p["open_price"], f"#{p['id']} {p['side']} entry", "#0d47a1"),
                                (p["sl"], f"#{p['id']} stop", "#c62828"), (p["tp"], f"#{p['id']} target", "#2e7d32")):
            if price is None:
                continue
            fig.add_shape(type="line", x0=xs[0], x1=xs[-1], y0=price, y1=price, line=dict(color=col, width=2), row=1, col=1)
            fig.add_annotation(x=xs[0], y=price, text=f"{lab} {inst.fmt_price(price)}", showarrow=False, xanchor="left",
                               yanchor="bottom", font=dict(size=10, color=col), row=1, col=1)
    for o in svc.book.orders():
        if o["symbol"] == sym:
            fig.add_shape(type="line", x0=xs[0], x1=xs[-1], y0=o["price"], y1=o["price"],
                          line=dict(color="#6a1b9a", width=1.5, dash="dash"), row=1, col=1)
            fig.add_annotation(x=xs[0], y=o["price"], text=f"pending {o['side']} {o['order_type']} {inst.fmt_price(o['price'])}",
                               showarrow=False, xanchor="left", yanchor="top", font=dict(size=10, color="#6a1b9a"), row=1, col=1)
    fig.update_layout(uirevision=f"{sym}-{tf}")   # keep your zoom/pan between refreshes
    st.plotly_chart(fig, config={"displaylogo": False, "scrollZoom": True}, key="pp_chart")


# ------------------------------------------------------------------------------------------------ ticket
def _ticket(svc, s, sym):
    inst = get_instrument(sym)
    env = svc.env(s)
    q = svc.quotes.get(sym)
    st.markdown("#### Order ticket")
    if q:
        spread = (q["ask"] - q["bid"]) / inst.pip
        c = st.columns(3)
        c[0].metric("Sell (bid)", inst.fmt_price(q["bid"]))
        c[1].metric("Buy (ask)", inst.fmt_price(q["ask"]))
        c[2].metric("Spread", f"{spread:.1f} pips")
    else:
        st.info("Waiting for a price for this pair...")
    side = st.radio("Direction", ["long", "short"], horizontal=True, key="pp_side",
                    format_func={"long": "🟢 Buy (long)", "short": "🔴 Sell (short)"}.get)
    otype = st.radio("Order type", ["market", "limit", "stop"], horizontal=True, key="pp_type",
                     format_func={"market": "Market (now)", "limit": "Limit (better price)", "stop": "Stop (on breakout)"}.get,
                     help="Market fills now at the ask (buy) or bid (sell). Limit waits for a better price. Stop waits "
                          "for price to break through a level.")
    d = 1 if side == "long" else -1
    now_px = (q["ask"] if d > 0 else q["bid"]) if q else inst.typical_price
    price = None
    if otype != "market":
        offset = 10 * inst.pip * (d if otype == "stop" else -d)
        price = st.number_input("Entry price", value=float(round(now_px + offset, inst.decimals)),
                                step=inst.pip, format=f"%.{inst.decimals}f", key=f"pp_px_{sym}_{otype}_{side}")
    ref = price if price else now_px
    c1, c2 = st.columns(2)
    sl_p = c1.number_input("Stop-loss (pips, 0 = none)", 0.0, 1000.0, 15.0, 0.5, key="pp_sl")
    tp_p = c2.number_input("Take-profit (pips, 0 = none)", 0.0, 3000.0, 30.0, 0.5, key="pp_tp")
    sl = round(ref - d * sl_p * inst.pip, inst.decimals) if sl_p > 0 else None
    tp = round(ref + d * tp_p * inst.pip, inst.decimals) if tp_p > 0 else None

    balance = svc.book.balance()
    size_mode = st.radio("Size", ["mini", "micro", "risk", "custom"], horizontal=True, key="pp_size",
                         format_func={"mini": "1 mini lot", "micro": "1 micro lot", "risk": f"Risk {s.risk_pct:.2f}%",
                                      "custom": "Custom"}.get)
    if size_mode == "mini":
        units = 10_000
    elif size_mode == "micro":
        units = 1_000
    elif size_mode == "risk":
        vpu = value_per_price_unit(inst, 1.0, ref, env.converter(), env.ccy)
        units = units_for_risk(balance * s.risk_pct / 100, sl_p * inst.pip, vpu or 0, 1_000) if sl_p > 0 else 0
        if sl_p <= 0:
            st.caption("Risk-based sizing needs a stop-loss.")
    else:
        units = int(st.number_input("Units", 1_000, 10_000_000, 10_000, 1_000, key="pp_units"))
    expiry = st.number_input("Cancel pending order after (minutes, 0 = never)", 0, 10_000, 60, key="pp_exp") \
        if otype != "market" else 0

    with st.expander("📝 Your plan (the human side)", expanded=True):
        setup = st.selectbox("Setup", SETUPS, key="pp_setup")
        why = st.text_area("Why this trade? Where are you wrong?", key="pp_why", height=70,
                           placeholder="e.g. London ORB long above 1.0850, wrong if back inside the range")
        c1, c2 = st.columns(2)
        conf = c1.slider("Confidence", 1, 5, 3, key="pp_conf")
        emo = c2.selectbox("How do you feel right now?", EMOTIONS, key="pp_emo")
        ticked = [item for k, item in enumerate(CHECKLIST) if st.checkbox(item, key=f"pp_chk_{k}")]

    # live preview
    risk_ccy = rr = risk_pct = None
    if sl is not None and units:
        risk_ccy = svc.book.quote_to_ccy(abs(ref - sl) * units, sym, ref, env)
        risk_ccy = (risk_ccy or 0) + svc.book.commission(sym, units, ref, env)
        risk_pct = risk_ccy / balance * 100 if balance > 0 else None
    if sl is not None and tp is not None:
        rr = abs(tp - ref) / abs(ref - sl)
    from edgelab.risk import margin_required

    marg = margin_required(inst, units, ref, env.converter(), env.ccy, env.broker) if units else None
    st.markdown(f"**{units:,} units** ({units / 100_000:.2f} lots) | entry ~{inst.fmt_price(ref)} | "
                f"stop {inst.fmt_price(sl) if sl else '-'} | target {inst.fmt_price(tp) if tp else '-'}  \n"
                + (f"Loss at stop **{money(risk_ccy, env.ccy)}** ({num(risk_pct, '.2f', '%')} of paper balance) | "
                   if risk_ccy else "**No stop-loss set** | ")
                + (f"margin {money(marg, env.ccy)}" if marg else ""))
    checks = pre_trade_checks(has_sl=sl is not None, risk_pct=risk_pct, rule_pct=s.risk_pct, rr=rr, emotion=emo,
                              ticked=ticked, closed=svc.book.positions("closed"), open_count=len(svc.book.positions()),
                              now=svc.feed.now(), units=units)
    for level, msg in checks:
        {"stop": st.error, "warn": st.warning, "ok": st.success}[level](msg)
    if st.button(f"Place paper {side} order", type="primary", width="stretch", disabled=units <= 0):
        plan = {"setup": setup, "why": why, "confidence": conf, "emotion": emo, "checklist": ticked,
                "warnings": [m for lvl, m in checks if lvl != "ok"]}
        res = svc.book.place_order(sym, side, otype, units, quote=q, env=env, now=svc.feed.now(), price=price, sl=sl,
                                   tp=tp, expiry_minutes=int(expiry) or None, plan=plan, all_quotes=svc.quotes)
        (st.success if res["ok"] else st.error)(res["message"])


# ------------------------------------------------------------------------------------------------ positions
@st.fragment(run_every="2s")
def _positions(svc, s):
    env = svc.env(s)
    snap = svc.book.snapshot(svc.quotes, env)
    st.subheader(f"Open positions ({len(snap['positions'])})")
    if not snap["positions"]:
        st.caption("None open.")
    for p in snap["positions"]:
        inst = get_instrument(p["symbol"])
        pnl = p["pnl_live"]
        head = (f"#{p['id']} {p['symbol']} {p['side']} {p['units']:,.0f} @ {inst.fmt_price(p['open_price'])} | "
                + (f"{p['pips_live']:+.1f} pips, {money(pnl, env.ccy)}" if pnl is not None else "no price")
                + (f" ({p['r_live']:+.2f} R)" if p.get("r_live") is not None else "")
                + f" | SL {inst.fmt_price(p['sl']) if p['sl'] else 'none'} | TP {inst.fmt_price(p['tp']) if p['tp'] else 'none'}")
        with st.expander(("🟢 " if (pnl or 0) >= 0 else "🔴 ") + head):
            c1, c2, c3 = st.columns(3)
            if c1.button("Close now", key=f"cl_{p['id']}", type="primary"):
                q = svc.quotes.get(p["symbol"])
                if q:
                    res = svc.book.close_position(p["id"], q["bid"] if p["side"] == "long" else q["ask"], svc.feed.now(),
                                                  "closed by you", env)
                    st.toast(describe({"type": "closed by you", **res}, env.ccy))
                    st.rerun(scope="app")
            if c2.button("Stop to breakeven", key=f"be_{p['id']}"):
                st.toast(svc.book.modify_position(p["id"], svc.feed.now(), sl=p["open_price"]))
            with c3.popover("Change stop / target"):
                nsl = st.number_input("New stop (0 = remove)", value=float(p["sl"] or 0), step=inst.pip,
                                      format=f"%.{inst.decimals}f", key=f"nsl_{p['id']}")
                ntp = st.number_input("New target (0 = remove)", value=float(p["tp"] or 0), step=inst.pip,
                                      format=f"%.{inst.decimals}f", key=f"ntp_{p['id']}")
                if st.button("Save", key=f"sv_{p['id']}"):
                    st.toast(svc.book.modify_position(p["id"], svc.feed.now(), sl=nsl or None, tp=ntp or None))
            plan = p.get("plan") or {}
            st.caption(f"Plan: {plan.get('setup', '-')} - {plan.get('why') or 'no notes'} | felt: {plan.get('emotion', '-')}"
                       f" | best {p.get('best') or 0:+.1f} / worst {p.get('worst') or 0:+.1f} pips so far")
    orders = svc.book.orders()
    if orders:
        st.markdown(f"**Pending orders ({len(orders)})**")
        for o in orders:
            inst = get_instrument(o["symbol"])
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"#{o['id']} {o['symbol']} {o['side']} {o['order_type']} {o['units']:,.0f} @ "
                        f"{inst.fmt_price(o['price'])} | SL {inst.fmt_price(o['sl']) if o['sl'] else '-'} | "
                        f"TP {inst.fmt_price(o['tp']) if o['tp'] else '-'}"
                        + (f" | expires {pd.Timestamp(o['expiry_ts']).tz_convert(s.display_tz):%H:%M}" if o['expiry_ts'] else ""))
            if c2.button("Cancel", key=f"co_{o['id']}"):
                svc.book.cancel_order(o["id"], svc.feed.now())
                st.rerun(scope="fragment")


# ------------------------------------------------------------------------------------------------ journal + stats
def _tabs(svc, s):
    t1, t2, t3 = st.tabs(["📒 Journal & review", "📊 My paper performance", "⚙️ Paper account"])
    closed = svc.book.positions("closed")
    df = trades_frame(closed)
    with t1:
        _journal(svc, s, closed, df)
    with t2:
        _performance(svc, s, df)
    with t3:
        bal = balance_summary().balance
        st.markdown(f"Paper balance started at **{money(svc.book.start_balance, s.account_currency)}**. Your real "
                    f"balance is {money(bal, s.account_currency)}.")
        start = st.number_input("Restart the paper account with", 100.0, 10_000_000.0,
                                clamp(round(bal, 2), 100.0, 10_000_000.0, 10_000.0), 500.0)
        if st.button("Reset paper account (clears paper trades and paper journal)"):
            svc.book.reset(start, s.account_currency)
            st.success("Paper account reset.")
            st.rerun()


def _journal(svc, s, closed, df):
    if df.empty:
        st.info("Closed paper trades appear here. Review each one - that's where the learning happens.")
        return
    ccy = s.account_currency
    view = df.sort_values("id", ascending=False)
    show = view[["id", "symbol", "side", "units", "opened", "closed", "reason", "pnl", "r", "setup", "emotion",
                 "followed_plan", "grade", "flags"]].copy()
    show["opened"] = show["opened"].dt.tz_convert(s.display_tz).dt.strftime("%d %b %H:%M")
    show["closed"] = show["closed"].dt.tz_convert(s.display_tz).dt.strftime("%d %b %H:%M")
    st.dataframe(show.style.format({"pnl": "{:+,.2f}", "r": "{:+.2f}", "units": "{:,.0f}"}, na_rep="-"), hide_index=True)
    pid = st.selectbox("Review a trade", view["id"].tolist(),
                       format_func=lambda i: f"#{i} " + ("(not reviewed)" if view.set_index('id').loc[i, 'followed_plan'] == 'not reviewed' else ""))
    p = next(x for x in closed if x["id"] == pid)
    plan, rev = p["plan"], p["review"]
    inst = get_instrument(p["symbol"])
    st.markdown(f"**#{p['id']} {p['symbol']} {p['side']}**: {inst.fmt_price(p['open_price'])} -> "
                f"{inst.fmt_price(p['close_price'])} ({p['close_reason']}), {money(p['pnl'], ccy)}"
                + (f", {p['r']:+.2f} R" if p['r'] is not None else ", no stop so no R") +
                f". Best point {p.get('best') or 0:+.1f} pips, worst {p.get('worst') or 0:+.1f} pips.")
    st.caption(f"Your plan: {plan.get('setup', '-')} | {plan.get('why') or 'no notes'} | confidence "
               f"{plan.get('confidence', '-')} | felt {plan.get('emotion', '-')} | checklist "
               f"{len(plan.get('checklist', []))}/{len(CHECKLIST)}"
               + (f" | warnings you saw: {'; '.join(plan['warnings'])}" if plan.get("warnings") else ""))
    for e in p["events"]:
        if e.get("what") in ("stop moved", "target moved"):
            st.caption(f"{pd.Timestamp(e['t']).tz_convert(s.display_tz):%H:%M} {e['what']}: {e.get('from')} -> {e.get('to')}"
                       + (f" ({', '.join(e['flags'])})" if e.get("flags") else ""))
    with st.form(f"review_{pid}"):
        c1, c2 = st.columns(2)
        fol = c1.radio("Did you follow your plan?", ["yes", "partly", "no"], horizontal=True,
                       index=["yes", "partly", "no"].index(rev["followed"]) if rev.get("followed") in ("yes", "partly", "no") else 0)
        grade = c2.select_slider("Grade the decision (not the result)", ["F", "E", "D", "C", "B", "A"],
                                 value=rev.get("grade", "C"))
        mistakes = st.multiselect("Mistakes", MISTAKES, default=[m for m in rev.get("mistakes", []) if m in MISTAKES])
        lesson = st.text_area("Lesson for next time", rev.get("lesson", ""))
        if st.form_submit_button("Save review", type="primary"):
            svc.book.save_review(pid, {"followed": fol, "grade": grade, "mistakes": mistakes, "lesson": lesson})
            st.success("Saved.")
            st.rerun()


def _performance(svc, s, df):
    if df.empty:
        st.info("No closed paper trades yet.")
        return
    ccy = s.account_currency
    c = st.columns(5)
    c[0].metric("Trades", len(df))
    c[1].metric("Win rate", f"{(df['pnl'] > 0).mean() * 100:.0f}%")
    c[2].metric("Total P&L", money(df["pnl"].sum(), ccy))
    c[3].metric("Average R", f"{df['r'].mean():+.2f}" if df["r"].notna().any() else "-")
    c[4].metric("Average win / loss", f"{df.loc[df['pnl'] > 0, 'pnl'].mean():,.0f} / {-df.loc[df['pnl'] < 0, 'pnl'].mean():,.0f}"
                if (df["pnl"] > 0).any() and (df["pnl"] < 0).any() else "-")
    if len(df) < 30:
        st.caption(f"{len(df)} trades - judge your process, not your P&L, until you have 30+.")
    curve = svc.book.start_balance + df.sort_values("closed")["pnl"].cumsum()
    fig = go.Figure(go.Scatter(x=df.sort_values("closed")["closed"], y=curve, mode="lines+markers",
                               line=dict(color="#1c5cab", width=2), marker=dict(size=6),
                               hovertemplate="%{x|%d %b %H:%M}<br>%{y:,.2f}<extra></extra>"))
    fig.add_hline(y=svc.book.start_balance, line=dict(color="#c3c2b7", dash="dot"))
    fig.update_layout(height=280, margin=dict(l=10, r=10, t=30, b=10), plot_bgcolor="white",
                      title=dict(text="Paper balance after each closed trade", font=dict(size=14)))
    st.plotly_chart(fig, key="pp_curve")
    for tip in behaviour_insights(df):
        st.warning(tip)
    a, b = st.columns(2)
    agg = lambda g: pd.Series({"Trades": len(g), "Win rate %": (g["pnl"] > 0).mean() * 100,  # noqa: E731
                               "Total P&L": g["pnl"].sum(), "Avg R": g["r"].mean()})
    with a:
        st.markdown("**By setup**")
        st.dataframe(df.groupby("setup").apply(agg, include_groups=False).reset_index(), hide_index=True)
    with b:
        st.markdown("**By how you felt**")
        st.dataframe(df.groupby("emotion").apply(agg, include_groups=False).reset_index(), hide_index=True)
    r = df["r"].dropna().to_numpy()
    if len(r) >= 5:
        from edgelab.ui import mc_view

        st.markdown("#### 🎲 Monte Carlo on your paper trades")
        cfg = mc_view.controls("pp_mc", svc.book.balance(), s.risk_pct, None, ccy, default_trades=100)
        if st.button("🎲 Run Monte Carlo on my paper trades"):
            mc_view.show_results(r, cfg, ccy, key="pp", compact=True)
