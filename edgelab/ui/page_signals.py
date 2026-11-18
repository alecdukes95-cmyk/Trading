import pandas as pd
import streamlit as st

from edgelab.config import api_key
from edgelab.instruments import get_instrument
from edgelab.live.alerts import DISCLAIMER, headline, payload_frame_and_signal
from edgelab.live.notify import beep_wav
from edgelab.ui.explain import render_signal
from edgelab.ui.state import get_scanner, get_settings, get_store, load_bars


def render():
    s = get_settings()
    sc = get_scanner()
    store = get_store()
    st.title("Live signals")
    st.caption("Watches your forex pairs and alerts you when a pattern completes, with an order idea and an annotated "
               f"chart showing why. {DISCLAIMER}")

    has_key = bool(api_key("OANDA_API_KEY"))
    with st.container(border=True):
        c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
        c1.markdown(f"**Watching:** {', '.join(s.watchlist)}  \n**Timeframes:** {', '.join(s.scan_timeframes)}  \n"
                    f"**Alert quality:** {s.alert_quality} | only in window: {'yes' if s.notify_only_in_window else 'no'}")
        c1.caption("Change these under Settings -> Alerts.")
        if c2.button("▶ Start live scanning (OANDA)", type="primary", disabled=not has_key, width="stretch"):
            sc.start("live")
            st.rerun()
        if not has_key:
            c2.caption("Needs your OANDA token in .env (see Data -> Download forex).")
        days = c3.number_input("Simulation: replay the last N days", 1, 60, 10)
        speed = c3.select_slider("Seconds per simulated step", [0.2, 0.5, 1.0, 2.0, 5.0], value=0.5,
                                 help="Nights and weekends are skipped instantly when alerts are limited to your "
                                      "trading window.")
        if c3.button("▶ Start simulation", width="stretch"):
            sc.start("simulation", sim_days=int(days), sim_seconds=float(speed))
            st.rerun()
        if c4.button("■ Stop", width="stretch", disabled=not sc.status.running):
            sc.stop()
            st.rerun()

    _status_and_new_alerts(s.notify_sound)

    st.subheader("Alerts")
    c1, c2, c3 = st.columns([2, 1, 1])
    mode = c1.radio("Show", ["all", "live", "simulation"], horizontal=True, label_visibility="collapsed")
    if c2.button("Mark all as read"):
        store.mark_alerts_seen()
        st.rerun()
    if c3.button("Clear simulation alerts"):
        store.clear_alerts("simulation")
        st.rerun()
    alerts = store.list_alerts(30, None if mode == "all" else mode)
    if not alerts:
        st.info("No alerts yet. Start live scanning, or run a simulation to see what alerts look like.")
    for k, a in enumerate(alerts):
        _alert_card(a, s, expanded=(k == 0))
    if alerts:
        store.mark_alerts_seen([a["id"] for a in alerts if not a["seen"]])

    st.subheader("Your watchlist")
    _watchlist_grid(s)


@st.fragment(run_every="4s")
def _status_and_new_alerts(sound: bool):
    sc = get_scanner()
    stt = sc.status
    if stt.running:
        clock = (f" | simulated time {stt.sim_clock.tz_convert('Europe/London'):%a %d %b %H:%M} UK"
                 if stt.sim_clock is not None else "")
        last = f"{stt.last_check.tz_convert('Europe/London'):%H:%M:%S}" if stt.last_check is not None else "starting..."
        st.success(f"Scanner running ({stt.mode}). Last check {last}{clock}. Alerts this run: {stt.alerts}.")
    else:
        st.info("Scanner stopped." + (f" Last run found {stt.alerts} alerts." if stt.started_at else ""))
    if stt.errors:
        with st.expander(f"Messages ({len(stt.errors)})"):
            for e in stt.errors[-10:]:
                st.text(e)
    # new alerts since this browser last looked -> toast (+ beep), then refresh the page to show the cards
    seen = st.session_state.setdefault("_toasted", set())
    new = [a for a in get_store().list_alerts(10, unseen_only=True) if a["id"] not in seen]
    if new:
        for a in reversed(new[:3]):
            st.toast(headline(a["payload"]), icon="🔔")
            seen.add(a["id"])
        for a in new[3:]:
            seen.add(a["id"])
        if sound:
            st.audio(beep_wav(), format="audio/wav", autoplay=True)
        st.rerun(scope="app")


def _alert_card(a: dict, s, expanded: bool):
    pl = a["payload"]
    inst = get_instrument(pl["symbol"])
    p = pl["plan"]
    d = pl["decimals"]
    tag = "🧪 SIMULATION" if pl.get("mode") == "simulation" else "🟢 LIVE"
    when = pd.Timestamp(pl["bar_ts"], unit="s", tz="UTC").tz_convert(s.display_tz)
    pos = pl.get("position") or {}
    risk = (f" | risk £{pos['risk']:.2f} ({pos['risk_pct']:.2f}%)" if pos.get("risk") and pos.get("currency") == "GBP"
            else "")
    new = "🔔 " if not a["seen"] else ""
    with st.expander(f"{new}{tag} | {when:%a %d %b %H:%M} | {pl['symbol']} {pl['timeframe']} | {pl['title']} -> "
                     f"{p['order_type']} {p['entry']:.{d}f}, SL {p['stop']:.{d}f}, TP {p['target']:.{d}f}{risk}",
                     expanded=expanded):
        df, sig = payload_frame_and_signal(pl)
        render_signal(sig, df, inst, tz=s.display_tz, timeframe=pl["timeframe"], stats=pl.get("stats"),
                      stats_source=pl.get("stats_source", ""), is_synthetic="SYNTHETIC" in pl.get("stats_source", ""),
                      position=pos, expiry_minutes=pl.get("expiry_minutes"), outcome=None, allow_reveal=False,
                      key=f"alert_{a['id']}")


def _watchlist_grid(s):
    import plotly.graph_objects as go

    from edgelab.live.scanner import pick_dataset

    store = get_store()
    cols = st.columns(4)
    for k, sym in enumerate(s.watchlist):
        ds = pick_dataset(store, sym)
        with cols[k % 4].container(border=True):
            if ds is None:
                st.markdown(f"**{sym}**")
                st.caption("No data yet - download it from OANDA (Data page).")
                continue
            df = load_bars(int(ds["id"]))
            last = df.iloc[-300:]
            inst = get_instrument(sym)
            chg = last["close"].iloc[-1] - last["close"].iloc[-289] if len(last) > 289 else 0.0
            st.markdown(f"**{sym}** {inst.fmt_price(last['close'].iloc[-1])}  \n"
                        f"<span style='color:{'#1b5e20' if chg >= 0 else '#b71c1c'}'>{inst.fmt_move(chg)} (24h)</span>",
                        unsafe_allow_html=True)
            fig = go.Figure(go.Scatter(y=last["close"].to_numpy(), mode="lines",
                                       line=dict(width=1.5, color="#1b5e20" if chg >= 0 else "#b71c1c")))
            fig.update_layout(height=80, margin=dict(l=0, r=0, t=0, b=0), xaxis_visible=False, yaxis_visible=False,
                              showlegend=False)
            st.plotly_chart(fig, config={"displayModeBar": False, "staticPlot": True}, key=f"spark_{sym}")
            st.caption(("SYNTHETIC sample" if ds["is_sample"] else ds["source"]) +
                       f" - data to {ds['end']:%d %b %Y}")
