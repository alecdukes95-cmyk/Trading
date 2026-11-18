import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from edgelab.instruments import FOREX_SYMBOLS, get_instrument
from edgelab.learning.replay import MOVE_ATR, Decision, evaluate_decision, pick_start
from edgelab.patterns.base import PATTERNS
from edgelab.timeutil import timeframe_minutes
from edgelab.ui.charts import ChartOptions, build_chart
from edgelab.ui.page_settings import num
from edgelab.ui.state import balance_summary, get_converter, get_settings, get_store, pattern_analysis

HORIZON = {"5m": 36, "15m": 24}
HISTORY = {"5m": 120, "15m": 96}


def _analysis(sym: str, tf: str):
    from edgelab.live.scanner import pick_dataset

    s = get_settings()
    ds = pick_dataset(get_store(), sym)
    if ds is None:
        return None, None
    a = pattern_analysis(int(ds["id"]), sym, ds["timeframe"], tf, s.session_for("forex").to_dict(),
                         s.broker().to_dict(), float(s.target_r), int(s.order_expiry_bars))
    return a, ds


def render():
    st.title("Learn: replay trainer")
    st.caption("You're dropped at a moment in history with the future hidden. Step forward bar by bar if you like, "
               "then decide: long, short or no trade - with a stop and a target. Reveal what happened and get "
               "scored on your read, your result and your risk discipline.")
    tab_play, tab_prog = st.tabs(["🎮 Replay", "📊 My progress"])
    with tab_play:
        _play()
    with tab_prog:
        _progress()


# ------------------------------------------------------------------------------------------------ replay
def _new(sym, tf, mode):
    a, ds = _analysis(sym, tf)
    if a is None:
        st.session_state["rp_error"] = f"No data for {sym}."
        return
    rng = np.random.default_rng()
    i = pick_start(a.ctx, rng, "pattern" if mode.startswith("Pattern") else "random", a.signals,
                   history=HISTORY[tf], future=HORIZON[tf] + 2, settings=get_settings())
    if i is None:
        st.session_state["rp_error"] = "Couldn't find a suitable moment in this data."
        return
    st.session_state["rp"] = {"sym": sym, "tf": tf, "mode": mode, "start": i, "cur": i, "phase": "watch",
                              "hint": False, "ds_name": ds["name"], "synthetic": bool(ds["is_sample"]), "saved": False}


def _play():
    s = get_settings()
    c1, c2, c3, c4 = st.columns([2, 1, 2, 1])
    sym = c1.selectbox("Pair", s.watchlist or FOREX_SYMBOLS, key="rp_sym")
    tf = c2.selectbox("Timeframe", ["5m", "15m"], index=1, key="rp_tf")
    mode = c3.radio("Moment", ["Random moment", "Pattern drill"], horizontal=True, key="rp_mode",
                    help="Random: any moment in your trading window - often the right answer is 'no trade'. "
                         "Pattern drill: a bar where one of the app's patterns just completed - can you read it?")
    if c4.button("New replay", type="primary", width="stretch"):
        _new(sym, tf, mode)
    if err := st.session_state.pop("rp_error", None):
        st.error(err)
    rp = st.session_state.get("rp")
    if not rp:
        st.info("Press **New replay** to start. Tip: there's no penalty for 'no trade' when nothing is happening - "
                "learning to sit out is half the skill.")
        return
    a, _ = _analysis(rp["sym"], rp["tf"])
    if a is None:
        st.error("The data for this replay is no longer available.")
        return
    ctx, inst = a.ctx, get_instrument(rp["sym"])
    df = ctx.df
    cur = rp["cur"]
    if rp["synthetic"]:
        st.caption("⚠️ Synthetic sample data - fine for practising the process; download OANDA data to practise on "
                   "real price action.")

    if rp["phase"] == "watch":
        c = st.columns([1, 1, 1, 3])
        if c[0].button("Next bar ▶", width="stretch") and cur + 1 < len(df) - HORIZON[rp["tf"]] - 1:
            rp["cur"] += 1
            st.rerun()
        if c[1].button("+6 bars ⏩", width="stretch") and cur + 6 < len(df) - HORIZON[rp["tf"]] - 1:
            rp["cur"] += 6
            st.rerun()
        if c[2].button("💡 Hint", width="stretch"):
            rp["hint"] = True
        when = df.index[cur].tz_convert(s.display_tz)
        c[3].caption(f"**{rp['sym']} {rp['tf']}**, now: close of the {when:%a %d %b %Y %H:%M} bar ({s.display_tz}). "
                     f"{cur - rp['start']} bars stepped. Data: {rp['ds_name']}.")
        view = df.iloc[max(0, cur - HISTORY[rp["tf"]]):cur + 1]
        sigs = [sg for sg in a.signals if sg.i <= cur] if rp["hint"] else None
        fig = build_chart(view, ChartOptions(overlays={"sessions", "vwap", "ema_fast", "ema_slow", "or"},
                                             panels=["volume"], decimals=inst.decimals, height=620),
                          tz=s.display_tz, signals=sigs, offset=int(df.index.get_indexer([view.index[0]])[0]))
        st.plotly_chart(fig, config={"displaylogo": False, "scrollZoom": True}, key="rp_chart")
        if rp["hint"]:
            _rules_saw(a, cur, s)
        _decision_form(rp, ctx, inst, s)
    else:
        _reveal(rp, a, inst, s)


def _risk_checker(inst, s):
    from edgelab.risk import analyse_position

    bal = balance_summary().balance
    conv = get_converter()

    def check(entry, stop):
        try:
            r = analyse_position(inst, s.forex_fixed_units, entry, balance=bal, risk_pct=s.risk_pct, broker=s.broker(),
                                 conv=conv, ccy=s.account_currency, stop_distance=abs(entry - stop))
        except ValueError:
            return True, "Risk check unavailable (no exchange rate)."
        ok = r.total_risk <= r.risk_limit * 1.0001
        if r.total_risk is None:
            return True, "Risk check unavailable."
        return ok, (f"1 mini lot risks £{r.total_risk:.2f} = {num(r.risk_pct, '.2f', '%')} of your balance - "
                    + ("within" if ok else "BREAKS") + f" your {s.risk_pct:.2f}% rule.")
    return check


def _decision_form(rp, ctx, inst, s):
    cur = rp["cur"]
    atr = float(ctx.atr[cur])
    price = float(ctx.c[cur])
    st.markdown("#### Your decision (you'd enter at the next bar's open)")
    with st.form("rp_decide"):
        c1, c2, c3 = st.columns(3)
        side = c1.radio("Action", ["long", "short", "none"], horizontal=True,
                        format_func={"long": "🟢 Long (buy)", "short": "🔴 Short (sell)", "none": "⏸ No trade"}.get)
        atr_p = atr / inst.pip
        stop_p = c2.number_input("Stop-loss distance (pips)", 0.5, 500.0, float(round(max(1.0, 1.5 * atr_p), 1)), 0.5,
                                 help=f"Current ATR is {atr_p:.1f} pips: the typical size of one bar.")
        tgt_p = c3.number_input("Target distance (pips)", 0.5, 2000.0, float(round(max(2.0, 3.0 * atr_p), 1)), 0.5)
        sub = st.form_submit_button("🔒 Lock in decision and reveal", type="primary")
    st.caption(f"Last close {inst.fmt_price(price)}. A long with these settings: stop ~{inst.fmt_price(price - stop_p * inst.pip)}, "
               f"target ~{inst.fmt_price(price + tgt_p * inst.pip)}; reward:risk {tgt_p / stop_p:.1f}:1.")
    if sub:
        sgn = 1 if side == "long" else -1
        entry = float(ctx.o[cur + 1])
        d = Decision(side, entry - sgn * stop_p * inst.pip if side != "none" else None,
                     entry + sgn * tgt_p * inst.pip if side != "none" else None)
        rp["decision"] = d
        rp["phase"] = "revealed"
        st.rerun()


def _rules_saw(a, cur, s):
    recent = [sg for sg in a.signals if cur - 2 <= sg.i <= cur]
    if not recent:
        st.info("💡 Hint: none of the app's patterns completed in the last 3 bars. Is there really a setup here?")
        return
    for sg in recent:
        st.info(f"💡 Hint: **{sg.title}** completed {cur - sg.i} bar(s) ago. "
                + " ".join(f"✅ {r}." for r in sg.reasons[:3]) + (" " + " ".join(f"⚠️ {c}." for c in sg.cautions)
                                                                   if sg.cautions else ""))


def _reveal(rp, a, inst, s):
    ctx = a.ctx
    df = ctx.df
    cur, d, tf = rp["cur"], rp["decision"], rp["tf"]
    b = s.broker()
    cost = (b.spread_for(inst.symbol) + 2 * b.slippage_pips) * inst.pip
    res = evaluate_decision(ctx, cur, d, HORIZON[tf], cost, _risk_checker(inst, s) if d.side != "none" else None)
    recent = [sg for sg in a.signals if cur - 2 <= sg.i <= cur]
    pattern = recent[-1].pattern if recent else "none"
    if not rp["saved"]:
        get_store().add_attempt({"symbol": rp["sym"], "timeframe": tf, "bar_ts": df.index[cur], "mode": rp["mode"],
                                 "decision": d.side, "entry": res.entry, "stop": d.stop, "target": d.target,
                                 "hindsight": res.hindsight, "read_correct": int(res.read_correct), "result": res.result,
                                 "r": res.r, "rr": res.rr, "stop_atr": res.stop_atr, "score": res.score,
                                 "pattern": pattern, "synthetic": int(rp["synthetic"])})
        rp["saved"] = True

    # ---- verdict
    word = {"up": f"rose {MOVE_ATR} ATR first", "down": f"fell {MOVE_ATR} ATR first",
            "chop": f"went nowhere decisive (neither +{MOVE_ATR} nor -{MOVE_ATR} ATR first)"}[res.hindsight]
    st.markdown(f"### Score: {res.score}/100")
    c = st.columns(3)
    c[0].metric("Your read", "✅ right" if res.read_correct else "❌ wrong",
                help=f"Over the next {HORIZON[tf]} bars price {word}.")
    c[1].metric("Trade result", "no trade" if d.side == "none" else f"{res.r:+.2f} R",
                help="After spread and slippage. Entry at the next bar's open; stop assumed hit first if both touched.")
    c[2].metric("Outcome", {"win": "🎯 target hit", "loss": "🛑 stopped out", "timeout": "⏱ still open - closed at the end",
                            "no trade": "⏸ sat out"}[res.result])
    st.markdown(f"**What happened:** over the next {HORIZON[tf]} bars price {word}.")
    for ok, msg in res.discipline:
        st.markdown(("✅ " if ok else "⚠️ ") + msg)
    if res.read_correct and res.result == "loss":
        st.info("Right idea, wrong execution: the move came, but your stop was hit first. Was it too tight for "
                "normal noise?")
    if not res.read_correct and res.result == "win":
        st.warning("You made money, but the move you bet on didn't really happen - be careful learning from this one; "
                   "luck rewards bad habits.")

    # ---- chart with the future revealed
    view = df.iloc[max(0, cur - HISTORY[tf]):min(len(df), cur + HORIZON[tf] + 1)]
    off = int(df.index.get_indexer([view.index[0]])[0])
    fig = build_chart(view, ChartOptions(overlays={"sessions", "vwap", "ema_fast", "ema_slow", "or"},
                                         panels=["volume"], decimals=inst.decimals, height=620),
                      tz=s.display_tz, signals=[sg for sg in a.signals if off <= sg.i <= cur], offset=off)
    xlab = list(view.index.tz_convert(s.display_tz).strftime("%a %d %b %H:%M"))
    k = cur - off
    fig.add_vrect(x0=xlab[k], x1=xlab[-1], fillcolor="#fff8e1", opacity=0.5, line_width=0, layer="below", row=1, col=1)
    fig.add_vline(x=xlab[k], line=dict(color="#6a1b9a", width=2), row="all", col=1)
    fig.add_annotation(x=xlab[k], y=1, yref="y domain", text="your decision", showarrow=False, xanchor="left",
                       font=dict(color="#6a1b9a", size=11), row=1, col=1)
    if d.side != "none" and res.entry is not None:
        for price, lab, colr in ((res.entry, "entry", "#0d47a1"), (d.stop, "stop", "#c62828"), (d.target, "target", "#2e7d32")):
            fig.add_shape(type="line", x0=xlab[k], x1=xlab[-1], y0=price, y1=price, line=dict(color=colr, width=2),
                          row=1, col=1)
            fig.add_annotation(x=xlab[-1], y=price, text=f"{lab} {inst.fmt_price(price)}", showarrow=False,
                               xanchor="right", yanchor="bottom", font=dict(color=colr, size=11), row=1, col=1)
    st.plotly_chart(fig, config={"displaylogo": False, "scrollZoom": True}, key="rp_reveal")
    st.caption("Shaded area = the future that was hidden from you.")

    # ---- what the rules saw
    st.markdown("#### What the pattern rules saw at your decision")
    if not recent:
        st.markdown("No pattern had just completed. Many good traders would simply have waited.")
    for sg in recent:
        o = a.outcomes[a.signals.index(sg)]
        out = {"win": "hit its target", "loss": "was stopped out", "timeout": "timed out", "not filled": "never triggered",
               "open": "was still open"}.get(o.result if o else "", "-")
        st.markdown(f"**{sg.title}** ({sg.direction}): the rule plan {out}"
                    + (f" for {o.r:+.2f} R." if o and o.filled else ".") + "  \n"
                    + " ".join(f"✅ {r}." for r in sg.reasons[:3]))
    if st.button("Next replay ▶", type="primary"):
        _new(rp["sym"], rp["tf"], rp["mode"])
        st.rerun()


# ------------------------------------------------------------------------------------------------ progress
def _progress():
    t = get_store().load_attempts()
    if t.empty:
        st.info("No replays yet. Your results will build up here: accuracy, average R, and the setups you misread most.")
        return
    c = st.columns(5)
    c[0].metric("Replays", len(t))
    c[1].metric("Average score", f"{t['score'].mean():.0f}/100")
    c[2].metric("Read accuracy", f"{t['read_correct'].mean() * 100:.0f}%",
                help="How often your long/short/no-trade call matched what the market did next.")
    trades = t[t["decision"] != "none"]
    c[3].metric("Average trade", f"{trades['r'].mean():+.2f} R" if len(trades) else "-")
    c[4].metric("Sat out", f"{(t['decision'] == 'none').mean() * 100:.0f}%")
    if len(t) < 20:
        st.caption(f"{len(t)} replays so far - patterns in your mistakes become meaningful after 20-30.")

    t = t.assign(setup=t["pattern"].map(lambda p: PATTERNS[p].name if p in PATTERNS else "No pattern"))
    by = t.groupby("setup").agg(Replays=("id", "count"), **{"Read accuracy %": ("read_correct", lambda x: x.mean() * 100)},
                                **{"Avg score": ("score", "mean")}).reset_index().sort_values("Read accuracy %")
    st.markdown("**Setups you most often misread** (worst first)")
    st.dataframe(by.style.format({"Read accuracy %": "{:.0f}", "Avg score": "{:.0f}"}), hide_index=True)
    mx = pd.crosstab(t["decision"].map({"long": "You went long", "short": "You went short", "none": "You sat out"}),
                     t["hindsight"].map({"up": "Market went up", "down": "Market went down", "chop": "Market chopped"}))
    st.markdown("**Your calls vs what happened** (the diagonal is where you were right)")
    st.dataframe(mx)
    tips = []
    chop_trades = ((t["hindsight"] == "chop") & (t["decision"] != "none")).sum()
    if chop_trades >= 3 and chop_trades / max(1, (t["hindsight"] == "chop").sum()) > 0.6:
        tips.append("You trade a lot when the market goes nowhere. Before entering, ask: is there a clear level, "
                    "trend or catalyst? 'No trade' is often the best trade.")
    tight = (t["stop_atr"] < 0.5).sum()
    if tight >= 3:
        tips.append(f"{tight} of your stops were under half an ATR - inside normal noise. Give trades room or skip them.")
    low_rr = (t["rr"].fillna(9) < 1.5).sum()
    if low_rr >= 3:
        tips.append(f"{low_rr} trades had reward:risk below 1.5:1, which needs a high win rate to pay.")
    for tip in tips:
        st.warning(tip)
    fig = go.Figure(go.Scatter(y=t["score"].rolling(5, min_periods=1).mean(), mode="lines",
                               line=dict(color="#1c5cab", width=2), hovertemplate="Replay %{x}<br>%{y:.0f}<extra></extra>"))
    fig.update_layout(height=240, margin=dict(l=10, r=10, t=30, b=10), plot_bgcolor="white",
                      title=dict(text="Score (5-replay average)", font=dict(size=14)))
    fig.update_yaxes(range=[0, 100], gridcolor="#e1e0d9")
    st.plotly_chart(fig, key="rp_trend")
    if st.button("Reset my progress"):
        get_store().clear_attempts()
        st.rerun()
