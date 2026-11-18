from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from edgelab.config import user_dir
from edgelab.instruments import FOREX_SYMBOLS, get_instrument
from edgelab.journal import analytics as J
from edgelab.paper.behaviour import EMOTIONS, MISTAKES
from edgelab.patterns.base import PATTERNS, TRADEABLE
from edgelab.ui.page_settings import money, num
from edgelab.ui.state import get_converter, get_settings, get_store

BASE_TAGS = [PATTERNS[k].name for k in TRADEABLE if k != "bear_flag"] + ["Trend pullback", "Range fade", "News",
                                                                       "Breakout", "Reversal"]


def render():
    s = get_settings()
    st.title("Trade journal")
    st.caption("Every trade in one place - imported, logged by hand, and paper - with your notes, tags and "
               "screenshots. The insights turn your own history into lessons and feed your edges and Monte Carlo.")
    df = J.prepare(get_store().journal_frame(), s.display_tz)
    t1, t2, t3, t4 = st.tabs(["📒 Trades", "➕ Log a trade", "📊 Insights", "🎯 Edge feedback & Monte Carlo"])
    with t1:
        _trades(df, s)
    with t2:
        _add(s)
    with t3:
        _insights(df, s)
    with t4:
        _feedback(df, s)


def _known_tags(df) -> list:
    tags = set(BASE_TAGS)
    if not df.empty:
        for v in df["tags"]:
            tags.update(v or [])
        tags.update(t for t in df["setup"].dropna().astype(str) if t)
    return sorted(tags)


def _filter(df, key):
    if df.empty:
        return df
    c1, c2, c3 = st.columns(3)
    groups = c1.multiselect("Show", list(J.SOURCES), default=list(J.SOURCES), format_func=J.SOURCES.get, key=f"{key}_src")
    syms = c2.multiselect("Pairs", sorted(df["symbol"].unique()), key=f"{key}_sym", placeholder="All pairs")
    tags = c3.multiselect("Tags", _known_tags(df), key=f"{key}_tag", placeholder="Any tag")
    out = df[df["group"].isin(groups)]
    if syms:
        out = out[out["symbol"].isin(syms)]
    if tags:
        out = out[out["tags"].map(lambda v: bool(set(v or []) & set(tags)))]
    return out


# ------------------------------------------------------------------------------------------------ trades
def _trades(df, s):
    if df.empty:
        st.info("No trades yet. Log one by hand, import a log (Data page), or place paper trades - they all land here.")
        return
    view = _filter(df, "jt").sort_values("exit_time", ascending=False)
    ccy = s.account_currency
    show = view[["id", "group", "symbol", "side", "entry_time", "exit_time", "entry_price", "exit_price", "size", "pnl",
                 "r_multiple", "tags", "grade", "followed"]].copy()
    show["entry_time"] = show["entry_time"].dt.tz_convert(s.display_tz).dt.strftime("%d %b %y %H:%M")
    show["exit_time"] = show["exit_time"].dt.tz_convert(s.display_tz).dt.strftime("%d %b %y %H:%M")
    show["tags"] = show["tags"].map(lambda v: ", ".join(v or []))
    st.dataframe(show.rename(columns={"group": "type", "r_multiple": "R"}).style.format(
        {"pnl": "{:+,.2f}", "R": "{:+.2f}", "size": "{:,.0f}"}, na_rep="-"), hide_index=True)
    st.download_button("Download journal (CSV)", view.drop(columns=["tags", "mistakes"]).assign(
        tags=view["tags"].map(lambda v: "; ".join(v or [])), mistakes=view["mistakes"].map(lambda v: "; ".join(v or [])))
        .to_csv(index=False).encode(), "journal.csv", "text/csv")
    if view.empty:
        return
    tid = st.selectbox("Open a trade", view["id"].tolist(),
                       format_func=lambda i: _label(view.set_index("id").loc[i], s.display_tz, ccy), key="jt_pick")
    _detail(int(tid), view.set_index("id").loc[tid], df, s)


def _label(r, tz, ccy) -> str:
    rev = "" if isinstance(r.get("followed"), str) and r.get("followed") else " - not reviewed"
    return (f"#{r.name} {r['symbol']} {r['side']} {r['exit_time'].tz_convert(tz):%d %b %H:%M} "
            f"{money(r['pnl'], ccy)}{rev}")


def _detail(tid: int, r, df, s):
    store = get_store()
    inst = get_instrument(r["symbol"])
    ccy = s.account_currency
    st.markdown(f"### #{tid} {r['symbol']} {r['side']} ({J.SOURCES[r['group']]})")
    c = st.columns(5)
    c[0].metric("Result", money(r["pnl"], ccy))
    c[1].metric("R", num(r["r_multiple"], "+.2f"))
    c[2].metric("Entry", inst.fmt_price(r["entry_price"]))
    c[3].metric("Exit", inst.fmt_price(r["exit_price"]))
    c[4].metric("Held", _held(r))
    _trade_chart(r, s)
    j = store.journal_get(tid) or {}
    with st.form(f"jform_{tid}"):
        st.markdown("**Journal**")
        known = _known_tags(df)
        tags = st.multiselect("Tags (setup, idea)", known, default=[t for t in (j.get("tags") or []) if t in known])
        new_tag = st.text_input("Add a new tag", "")
        c1, c2, c3 = st.columns(3)
        emo_opts = ["-"] + EMOTIONS
        emotion = c1.selectbox("How you felt", emo_opts, index=emo_opts.index(j["emotion"]) if j.get("emotion") in emo_opts else 0)
        fol_opts = ["-", "yes", "partly", "no"]
        followed = c2.selectbox("Followed your plan?", fol_opts,
                                index=fol_opts.index(j["followed"]) if j.get("followed") in fol_opts else 0)
        grade = c3.select_slider("Decision grade (not the result)", ["F", "E", "D", "C", "B", "A"], value=j.get("grade") or "C")
        mistakes = st.multiselect("Mistakes", MISTAKES, default=[m for m in (j.get("mistakes") or []) if m in MISTAKES])
        notes = st.text_area("Notes - what you saw, why you entered, how you managed it", j.get("notes") or (r.get("notes") or ""))
        lesson = st.text_area("Lesson for next time", j.get("lesson") or "")
        shots = st.file_uploader("Screenshots (charts, platform, anything)", type=["png", "jpg", "jpeg", "webp"],
                                 accept_multiple_files=True)
        caption = st.text_input("Caption for these screenshots", "")
        if st.form_submit_button("Save journal entry", type="primary"):
            all_tags = list(dict.fromkeys(tags + ([new_tag.strip()] if new_tag.strip() else [])))
            store.journal_upsert(tid, tags=all_tags, emotion=None if emotion == "-" else emotion,
                                 followed=None if followed == "-" else followed, grade=grade, mistakes=mistakes,
                                 notes=notes, lesson=lesson)
            folder = user_dir() / "screenshots"
            folder.mkdir(parents=True, exist_ok=True)
            for k, f in enumerate(shots or []):
                ext = Path(f.name).suffix.lower() or ".png"
                path = folder / f"trade{tid}_{pd.Timestamp.now():%Y%m%d%H%M%S}_{k}{ext}"
                path.write_bytes(f.getvalue())
                store.add_attachment(tid, str(path), caption)
            st.success("Saved.")
            st.rerun()
    atts = store.attachments(tid)
    if atts:
        st.markdown("**Screenshots**")
        cols = st.columns(3)
        for k, a in enumerate(atts):
            with cols[k % 3]:
                if Path(a["path"]).exists():
                    st.image(a["path"], caption=a["caption"] or Path(a["path"]).name)
                else:
                    st.caption(f"Missing file: {a['path']}")
                if st.button("Remove", key=f"att_del_{a['id']}"):
                    store.delete_attachment(a["id"])
                    st.rerun()
    if r["group"] == "manual":
        with st.expander("Delete this trade"):
            if st.button("Delete permanently", key=f"del_{tid}"):
                store.delete_trade(tid)
                st.rerun()


def _held(r) -> str:
    d = r["exit_time"] - r["entry_time"]
    mins = d / pd.Timedelta(minutes=1)
    return f"{mins:.0f} min" if mins < 120 else (f"{mins / 60:.1f} h" if mins < 2880 else f"{mins / 1440:.1f} days")


def _trade_chart(r, s):
    """The trade drawn on your stored price data (if any covers it)."""
    from edgelab.analysis import add_indicators
    from edgelab.live.scanner import pick_dataset
    from edgelab.ui.charts import ChartOptions, build_chart
    from edgelab.ui.state import load_bars

    ds = pick_dataset(get_store(), r["symbol"])
    if ds is None:
        return
    bars = load_bars(int(ds["id"]))
    a, b = r["entry_time"] - pd.Timedelta(hours=4), r["exit_time"] + pd.Timedelta(hours=2)
    view = bars[(bars.index >= a) & (bars.index <= b)]
    if len(view) < 5:
        st.caption("No stored price data covers this trade's time - download it on the Data page to see the trade on a chart.")
        return
    ind = add_indicators(bars[(bars.index >= a - pd.Timedelta(days=2)) & (bars.index <= b)], s.session_for("forex"))
    ind = ind[ind.index >= a]
    trades = pd.DataFrame([{"entry_time": r["entry_time"], "exit_time": r["exit_time"], "entry_price": r["entry_price"],
                            "exit_price": r["exit_price"], "side": r["side"], "pnl": r["pnl"]}])
    fig = build_chart(ind, ChartOptions(overlays={"vwap", "ema_fast", "ema_slow", "sessions"}, panels=[],
                                        decimals=get_instrument(r["symbol"]).decimals, height=380),
                      tz=s.display_tz, trades=trades, title=f"{r['symbol']} - {ds['name']}")
    if pd.notna(r.get("stop_price")):
        fig.add_hline(y=r["stop_price"], line=dict(color="#c62828", dash="dash"), annotation_text="initial stop",
                      row=1, col=1)
    st.plotly_chart(fig, key=f"jchart_{r.name}")
    if ds["is_sample"]:
        st.caption("Drawn on SYNTHETIC sample prices - download real data for an accurate picture.")


# ------------------------------------------------------------------------------------------------ add
def _add(s):
    from edgelab.data.tradelog import compute_pnl

    st.markdown("Log a trade you took on your broker's platform. **Logged trades count as real trades** (they update "
                "your running balance if they closed after your last balance figure). Practise on the Paper page instead.")
    with st.form("jadd"):
        c1, c2, c3 = st.columns(3)
        sym = c1.selectbox("Pair", FOREX_SYMBOLS, index=FOREX_SYMBOLS.index(s.watchlist[0]) if s.watchlist else 0)
        side = c2.radio("Direction", ["long", "short"], horizontal=True)
        size = c3.number_input("Size (units)", 1_000, 10_000_000, int(s.forex_fixed_units), 1_000)
        now = pd.Timestamp.now(tz="Europe/London")
        c1, c2, c3, c4 = st.columns(4)
        ed = c1.date_input("Entry date", now.date())
        et = c2.time_input("Entry time (UK)", (now - pd.Timedelta(hours=1)).floor("min").time())
        xd = c3.date_input("Exit date", now.date())
        xt = c4.time_input("Exit time (UK)", now.floor("min").time())
        inst = get_instrument(sym)
        fmt = f"%.{inst.decimals}f"
        c1, c2, c3, c4 = st.columns(4)
        entry = c1.number_input("Entry price", 0.0, 1e6, float(inst.typical_price), inst.pip, format=fmt)
        exit_ = c2.number_input("Exit price", 0.0, 1e6, float(inst.typical_price), inst.pip, format=fmt)
        stop = c3.number_input("Initial stop (0 = none)", 0.0, 1e6, 0.0, inst.pip, format=fmt)
        fees = c4.number_input(f"Fees / commission ({s.account_currency})", 0.0, 10_000.0, 0.0, 0.5)
        tags = st.multiselect("Tags", BASE_TAGS)
        emotion = st.selectbox("How you felt", ["-"] + EMOTIONS)
        notes = st.text_area("Notes")
        if st.form_submit_button("Save trade", type="primary"):
            t0 = pd.Timestamp.combine(ed, et).tz_localize("Europe/London", ambiguous=True, nonexistent="shift_forward")
            t1 = pd.Timestamp.combine(xd, xt).tz_localize("Europe/London", ambiguous=True, nonexistent="shift_forward")
            if t1 <= t0:
                st.error("The exit must be after the entry.")
                return
            d = 1 if side == "long" else -1
            if stop and (stop - entry) * d >= 0:
                st.error("The stop is on the wrong side of the entry.")
                return
            row = pd.DataFrame([{"symbol": sym, "side": side, "entry_time": t0.tz_convert("UTC"),
                                 "exit_time": t1.tz_convert("UTC"), "entry_price": entry, "exit_price": exit_,
                                 "size": float(size), "fees": fees, "stop_price": stop or np.nan, "setup": ", ".join(tags),
                                 "notes": notes}])
            row = compute_pnl(row, s.account_currency, get_converter())
            rec = row.iloc[0].to_dict()
            rec.pop("rate_source", None)
            tid = get_store().add_trade(rec, "manual")
            get_store().journal_upsert(tid, tags=tags, emotion=None if emotion == "-" else emotion, notes=notes)
            st.success(f"Saved trade #{tid}: {money(rec['pnl'], s.account_currency)}"
                       + (f" ({rec['r_multiple']:+.2f} R)." if pd.notna(rec["r_multiple"]) else "."))


# ------------------------------------------------------------------------------------------------ insights
def _insights(df, s):
    if df.empty:
        st.info("No trades yet.")
        return
    d = _filter(df, "ji")
    if d.empty:
        st.info("No trades match.")
        return
    ccy = s.account_currency
    h = J.headline(d)
    c = st.columns(6)
    c[0].metric("Trades", h["trades"])
    c[1].metric("Win rate", f"{h['win_rate'] * 100:.0f}%")
    c[2].metric("Total", money(h["total"], ccy))
    c[3].metric("Average R", num(h["avg_r"], "+.2f"), help=f"{h['with_r']} trades have a stop recorded.")
    c[4].metric("Avg win / loss", f"{num(h['avg_win'], ',.0f')} / {num(h['avg_loss'], ',.0f')}")
    c[5].metric("Profit factor", num(h["profit_factor"], ".2f"))
    if h["trades"] < 30:
        st.caption(f"{h['trades']} trades - patterns below are hints, not conclusions, until you have 30+.")
    for tip in J.lessons(d):
        st.warning(tip)
    eq = d.sort_values("exit_time")
    fig = go.Figure(go.Scatter(x=eq["exit_time"], y=eq["pnl"].cumsum(), mode="lines+markers",
                               line=dict(color="#1c5cab", width=2), marker=dict(size=5),
                               hovertemplate="%{x|%d %b %H:%M}<br>%{y:+,.2f}<extra></extra>"))
    fig.add_hline(y=0, line=dict(color="#c3c2b7"))
    fig.update_layout(height=260, margin=dict(l=10, r=10, t=30, b=10), plot_bgcolor="white",
                      title=dict(text=f"Cumulative P&L ({ccy})", font=dict(size=14)))
    st.plotly_chart(fig, key="ji_eq")
    grid = J.calendar_grid(J.daily_pnl(d))
    if not grid.empty:
        lim = float(np.nanmax(np.abs(grid.to_numpy()))) or 1.0
        hm = go.Figure(go.Heatmap(z=grid.to_numpy(), x=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                                  y=[f"w/c {w:%d %b}" for w in grid.index], zmid=0, zmin=-lim, zmax=lim,
                                  colorscale=[[0, "#d03b3b"], [0.5, "#f0efec"], [1, "#1c5cab"]],
                                  hovertemplate="%{y} %{x}: %{z:+,.2f}<extra></extra>", colorbar=dict(title=ccy)))
        hm.update_layout(height=max(220, 26 * len(grid) + 80), margin=dict(l=10, r=10, t=30, b=10),
                         title=dict(text="P&L calendar (blue = up day, red = down day)", font=dict(size=14)))
        hm.update_yaxes(autorange="reversed")
        st.plotly_chart(hm, key="ji_cal")
    fmt = {"Win rate %": "{:.0f}", "Avg R": "{:+.2f}", "Total P&L": "{:+,.2f}", "Avg P&L": "{:+,.2f}"}
    tabs = st.tabs(["By tag", "By mistake", "By emotion", "By plan followed", "By grade", "By weekday", "By hour", "By pair"])
    specs = [("tags", True), ("mistakes", True), ("emotion", False), ("followed", False), ("grade", False),
             ("weekday", False), ("hour", False), ("symbol", False)]
    for tb, (col, ex) in zip(tabs, specs):
        with tb:
            t = J.summary(d, col, explode=ex)
            st.dataframe(t.style.format(fmt, na_rep="-") if not t.empty else t, hide_index=True)


# ------------------------------------------------------------------------------------------------ feedback
def _feedback(df, s):
    from edgelab.ui import mc_view

    if df.empty:
        st.info("No trades yet.")
        return
    d = _filter(df, "jf")
    st.markdown("#### Your execution vs the rules")
    st.caption("For trades tagged with a pattern name, compare your average R with the same pattern's backtest on "
               "your stored data (15m, your watchlist). A negative gap usually means execution: late entries, moved "
               "stops, early exits.")
    if st.button("Compare with backtests"):
        from edgelab.ui.page_backtest import pattern_trades

        with st.spinner("Running pattern backtests..."):
            bt, _ = pattern_trades(tuple(s.watchlist), "15m", False)
        gap = J.execution_gap(d, bt)
        if gap.empty:
            st.info("No pattern-tagged trades with a stop recorded yet. Tag trades with the pattern you traded.")
        else:
            st.dataframe(gap.style.format({"Your avg R": "{:+.2f}", "Backtest avg R": "{:+.2f}",
                                           "Gap (you - rules)": "{:+.2f}"}, na_rep="-"), hide_index=True)
    st.markdown("#### 🎲 Monte Carlo on your journal")
    tag = st.selectbox("Trades to simulate", ["All shown trades"] + _known_tags(d), key="jf_mc_tag")
    sub = d if tag == "All shown trades" else d[d["tags"].map(lambda v: tag in (v or []))]
    r = sub["r_multiple"].dropna().to_numpy()
    st.caption(f"{len(r)} trades with an R value (trades need a stop to have one).")
    if len(r) >= 5:
        from edgelab.ui.state import balance_summary

        cfg = mc_view.controls("jf_mc", balance_summary().balance, s.risk_pct, None, s.account_currency)
        if st.button("🎲 Run Monte Carlo on my trades"):
            mc_view.show_results(r, cfg, s.account_currency, key="jf", compact=True)
    else:
        st.info("At least 5 trades with a stop are needed.")
