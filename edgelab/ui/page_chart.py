import numpy as np
import pandas as pd
import streamlit as st

from edgelab.analysis import IndicatorParams
from edgelab.indicators.levels import support_resistance
from edgelab.instruments import get_instrument
from edgelab.learning.glossary import GLOSSARY, help_text
from edgelab.learning.narrative import session_story
from edgelab.timeutil import timeframe_minutes
from edgelab.ui.charts import ChartOptions, build_chart
from edgelab.ui.page_glossary import term_expanders
from edgelab.patterns.base import PATTERNS, TRADEABLE
from edgelab.ui.state import get_settings, get_store, indicator_frame, pattern_analysis, pick_dataset

OVERLAYS = {
    "patterns": ("Detected patterns (click a triangle to explain it)", None),
    "structure": ("Market structure labels (HH / HL / LH / LL)", "Market structure"),
    "sessions": ("Market sessions (Asia / London / New York)", "Session"),
    "vwap": ("VWAP", "VWAP"),
    "ema_fast": ("EMA 9", "Moving average"),
    "ema_slow": ("EMA 20", "Moving average"),
    "sma_mid": ("SMA 50", "Moving average"),
    "sma_long": ("SMA 200", "Moving average"),
    "or": ("Opening range", "Opening range"),
    "pdhl": ("Prior-day high/low/close", "Prior-day high / low"),
    "sr": ("Support / resistance zones", "Support"),
    "trades": ("My imported trades", None),
}
PANELS = {"volume": ("Volume", "Volume"), "rsi": ("RSI", "RSI"), "macd": ("MACD", "MACD"), "atr": ("ATR", "ATR")}
TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]


def _shift_day(delta: int, n_days: int):
    st.session_state["chart_day_pos"] = max(0, min(n_days - 1, st.session_state.get("chart_day_pos", n_days - 1) + delta))


def render():
    s = get_settings()
    st.title("Chart reader")
    side = st.sidebar
    side.header("Chart controls")
    ds = pick_dataset("chart", side)
    if ds is None:
        return
    inst = get_instrument(ds["symbol"], ds["asset_class"])
    cfg = s.session_for(ds["asset_class"])
    base_m = timeframe_minutes(ds["timeframe"])
    tfs = [t for t in TIMEFRAMES if timeframe_minutes(t) >= base_m and timeframe_minutes(t) % base_m == 0]
    tf = side.selectbox("Timeframe", tfs, index=tfs.index("5m") if "5m" in tfs else 0,
                        help=help_text("Timeframe"))
    daily = timeframe_minutes(tf) >= 1440

    default_ov = ["patterns", "ema_fast", "ema_slow", "sma_mid"] if daily else \
        ["patterns", "sessions", "vwap", "ema_fast", "ema_slow", "or", "pdhl"]
    ov_options = [k for k in OVERLAYS if not (daily and k in ("vwap", "or", "pdhl", "sessions"))]
    overlays = side.multiselect("Overlays", ov_options, default=[d for d in default_ov if d in ov_options],
                                format_func=lambda k: OVERLAYS[k][0],
                                help="Lines drawn on top of the price chart. Open 'What am I looking at?' below "
                                     "the chart for an explanation of each one.")
    panels = side.multiselect("Lower panels", list(PANELS), default=["volume", "rsi"],
                              format_func=lambda k: PANELS[k][0])

    params = IndicatorParams(or_minutes=int(s.opening_range_minutes))
    df = indicator_frame(int(ds["id"]), ds["timeframe"], tf, cfg.to_dict(), params)
    if df.empty:
        st.warning("This dataset has no bars.")
        return

    # ---- window selection
    days = pd.DatetimeIndex(df["day"].unique()).sort_values()
    if daily:
        n_show = side.slider("Bars to show", 30, min(500, len(df)), min(150, len(df)))
        view = df.iloc[-n_show:]
        end_day = days[-1]
    else:
        n_days_show = side.slider("Days to show", 1, 10, 2 if timeframe_minutes(tf) <= 5 else 4)
        if st.session_state.get("chart_ds_tf") != (ds["id"], tf):
            st.session_state["chart_ds_tf"] = (ds["id"], tf)
            # start on the most recent *complete* day (the last day in a file is often partial)
            counts = df.groupby("day").size().reindex(days)
            full = np.flatnonzero((counts >= 0.7 * counts.median()).to_numpy())
            st.session_state["chart_day_pos"] = int(full[-1]) if len(full) else len(days) - 1
        pos = min(st.session_state.get("chart_day_pos", len(days) - 1), len(days) - 1)
        c1, c2, c3, c4 = st.columns([1, 1, 2, 3])
        c1.button("◀ Previous day", on_click=_shift_day, args=(-1, len(days)), width="stretch")
        c2.button("Next day ▶", on_click=_shift_day, args=(1, len(days)), width="stretch")
        picked = c3.date_input("Jump to day", value=days[pos].date(), min_value=days[0].date(),
                               max_value=days[-1].date(), label_visibility="collapsed")
        if pd.Timestamp(picked) != days[pos]:
            pos = int(min(len(days) - 1, days.searchsorted(pd.Timestamp(picked))))
            st.session_state["chart_day_pos"] = pos
        end_day = days[pos]
        start_day = days[max(0, pos - n_days_show + 1)]
        view = df[(df["day"] >= start_day) & (df["day"] <= end_day)]
        c4.caption(f"Trading day **{end_day:%A %d %b %Y}** ({pos + 1} of {len(days)}). Times shown in "
                   f"**{s.display_tz}**. {cfg.describe()}.")

    if ds["is_sample"]:
        st.caption("⚠️ Synthetic sample data: realistic-looking but random. It contains no real edge.")

    # ---- levels and trades
    levels = []
    if "sr" in overlays and len(view) > 30:
        scale = (view["high"].max() - view["low"].min()) / 20
        k = 5 if daily else 8
        levels = support_resistance(view, scale, left=k, right=k)
    trades = None
    if "trades" in overlays:
        trades = get_store().load_trades(ds["symbol"])
        if trades.empty:
            st.info("No imported trades for this symbol yet (Data -> Trade log).")

    # ---- patterns
    analysis, vis, offset = None, [], 0
    if "patterns" in overlays or "structure" in overlays:
        b = s.broker()
        analysis = pattern_analysis(int(ds["id"]), ds["symbol"], ds["timeframe"], tf, cfg.to_dict(), b.to_dict(),
                                    float(s.target_r), int(s.order_expiry_bars))
        offset = int(analysis.ctx.df.index.get_indexer([view.index[0]])[0])
        fams = sorted({PATTERNS[p].family for p in TRADEABLE})
        show_fams = side.multiselect("Pattern families", fams, default=fams, help="Which kinds of pattern to mark.")
        match_alerts = side.checkbox("Only patterns that match my alert rules", value=True,
                                     help="Same filter as the live scanner: your chosen patterns, alert quality and "
                                          "trading window (Settings -> Alerts). Untick to see every detection.")
        if "patterns" in overlays:
            from edgelab.live.alerts import passes_filters

            vis = [sg for sg in analysis.signals if offset <= sg.i < offset + len(view)
                   and PATTERNS[sg.pattern].family in show_fams
                   and (not match_alerts or passes_filters(sg, analysis.ctx, s))]

    opts = ChartOptions(overlays=set(overlays), panels=panels, levels=levels, decimals=inst.decimals,
                        height=820 if len(panels) >= 2 else 700)
    title = f"{ds['symbol']} {tf}"
    fig = build_chart(view, opts, tz=s.display_tz, title=title, daily=daily, trades=trades, signals=vis,
                      swings=analysis.swings if analysis is not None and "structure" in overlays else None,
                      offset=offset)
    event = st.plotly_chart(fig, config={"scrollZoom": True, "displaylogo": False}, on_select="rerun",
                            selection_mode="points", key=f"main_chart_{ds['id']}_{tf}")
    clicked = None
    try:
        pts = event.selection.points if event else []
        for ptn in pts:
            cd = ptn.get("customdata")
            if cd is not None:
                clicked = int(cd[0] if isinstance(cd, (list, tuple)) else cd)
    except (AttributeError, KeyError, TypeError, ValueError):
        clicked = None
    st.caption("Drag to pan, scroll to zoom, double-click to reset. Hover for values. Gaps such as the forex "
               "weekend are removed so candles sit side by side."
               + (" Shading: orange = Asia, blue = London, green = New York; where two overlap the colours mix."
                  if "sessions" in overlays and not daily else ""))

    if vis:
        _pattern_panel(vis, clicked, analysis, ds, inst, s, tf)
    elif "patterns" in overlays:
        st.caption("No patterns completed in this window - step to another day, or show more days.")

    # ---- explanations
    left, right = st.columns([3, 2])
    with left:
        if not daily:
            story = session_story(df, end_day, inst, s.display_tz)
            if story:
                st.subheader("What happened today?")
                st.markdown(f"**{story.headline}**")
                for b in story.bullets:
                    st.markdown(f"- {b}")
                st.caption("A description of the past, not a prediction.")
                term_expanders(story.terms)
        else:
            _daily_summary(view, inst)
    with right:
        st.subheader("What am I looking at?")
        shown = []
        for k in overlays:
            t = OVERLAYS[k][1]
            if t and t not in shown:
                shown.append(t)
        for k in panels:
            t = PANELS[k][1]
            if t not in shown:
                shown.append(t)
        if "sr" in overlays:
            shown.append("Resistance")
        for t in ["Candlestick"] + shown:
            with st.expander(t):
                st.markdown(GLOSSARY[t].markdown())
        last = view.iloc[-1]
        st.markdown("**Latest bar in view**")
        rows = {"Close": inst.fmt_price(last["close"]), "ATR (14)": inst.fmt_move(last["atr"], last["close"]).lstrip("+")
                if pd.notna(last["atr"]) else "-", "RSI (14)": f"{last['rsi']:.1f}" if pd.notna(last["rsi"]) else "-"}
        if not daily and pd.notna(last.get("vwap")):
            rows["vs VWAP"] = inst.fmt_move(last["close"] - last["vwap"], last["close"])
        st.table(pd.DataFrame(rows, index=[""]).T)


def _pattern_panel(vis, clicked, analysis, ds, inst, s, tf):
    from edgelab.live.alerts import position_info
    from edgelab.ui.explain import render_signal, stats_to_dict
    from edgelab.ui.state import balance_summary, get_converter

    st.subheader(f"Patterns in view ({len(vis)})")
    labels = [f"{sg.ts.tz_convert(s.display_tz):%a %d %b %H:%M} - {sg.title}" for sg in vis]
    key = f"pat_pick_{ds['id']}_{tf}"
    if clicked is not None and 0 <= clicked < len(vis):
        st.session_state[key] = labels[clicked]
    if st.session_state.get(key) not in labels:
        st.session_state[key] = labels[-1]
    choice = st.selectbox("Explain this pattern (or click a triangle on the chart)", labels, key=key)
    sig = vis[labels.index(choice)]
    k = analysis.signals.index(sig)
    outcome = analysis.outcomes[k]
    stats = stats_to_dict(analysis.stats.get((sig.pattern, sig.direction)))
    pos = position_info(sig, inst, s, balance_summary().balance, get_converter()) if sig.plan else None
    render_signal(sig, analysis.ctx.df, inst, tz=s.display_tz, timeframe=tf, stats=stats,
                  stats_source=ds["name"] + " (all of this dataset, in-sample)", is_synthetic=bool(ds["is_sample"]),
                  position=pos, expiry_minutes=int(s.order_expiry_bars * timeframe_minutes(tf)), outcome=outcome,
                  key=f"{ds['id']}_{tf}_{k}")
    with st.expander("Scoreboard: every pattern on this dataset (in-sample, after costs)"):
        from edgelab.patterns.stats import stats_frame

        st.dataframe(stats_frame(analysis.stats), hide_index=True)
        st.caption("Each row replays the same plan (stop entry, pattern stop, 2R target, conservative fills, spread "
                   "and slippage charged) after every past occurrence. 'Avg R' is the average result per trade in "
                   "multiples of the risk. If the 95% range includes zero, there is no evidence of an edge. This is "
                   "in-sample and descriptive - Stage 4 adds proper out-of-sample backtests.")


def _daily_summary(view: pd.DataFrame, inst):
    st.subheader("The bigger picture")
    last = view.iloc[-1]
    msgs = []
    if pd.notna(last.get("sma200")) and pd.notna(last.get("sma50")):
        if last["close"] > last["sma50"] > last["sma200"]:
            msgs.append("Price is above the 50- and 200-day averages, and the 50 is above the 200: a **long-term uptrend**.")
        elif last["close"] < last["sma50"] < last["sma200"]:
            msgs.append("Price is below the 50- and 200-day averages, and the 50 is below the 200: a **long-term downtrend**.")
        else:
            msgs.append("The moving averages are mixed: no clean long-term trend (often a range or a transition).")
    elif pd.notna(last.get("sma50")):
        side = "above" if last["close"] > last["sma50"] else "below"
        msgs.append(f"Price is {side} its 50-day average (not enough history for the 200-day yet).")
    last20 = view.iloc[-20:]
    chg = last20["close"].iloc[-1] - last20["close"].iloc[0]
    msgs.append(f"Over the last {len(last20)} days price moved {inst.fmt_move(chg, last20['close'].iloc[0])}.")
    if pd.notna(last["atr"]):
        msgs.append(f"A typical day currently moves about {inst.fmt_move(last['atr'], last['close']).lstrip('+')} "
                    "(daily ATR). Intraday stops much smaller than a fraction of this are easily hit by noise.")
    for m in msgs:
        st.markdown(f"- {m}")
    term_expanders(["Moving average", "ATR", "Market structure"])
