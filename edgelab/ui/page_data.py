import pandas as pd
import streamlit as st

from edgelab.data.loader import build_ohlcv, guess_mapping, guess_symbol, read_table
from edgelab.data.providers import coinbase
from edgelab.data.tradelog import ALL_FIELDS, REQUIRED, build_trades, guess_trade_mapping
from edgelab.data.validate import clean_ohlcv, validate_ohlcv
from edgelab.instruments import get_instrument, normalize_symbol
from edgelab.timeutil import bar_minutes, timeframe_label
from edgelab.ui.state import datasets, get_converter, get_settings, get_store, severity_box

TZ_CHOICES = {
    "UTC": "UTC / GMT (most APIs, many exports)",
    "Europe/London": "UK time (London, with daylight saving)",
    "America/New_York": "US Eastern (New York, with daylight saving)",
    "Europe/Athens": "MetaTrader broker 'server time' GMT+2/+3 (follows EU daylight saving)",
    "Europe/Berlin": "Central Europe (CET/CEST)",
    "Asia/Tokyo": "Tokyo",
    "Australia/Sydney": "Sydney",
}
DATE_ORDER = {"Auto-detect": None, "Day first (31/01/2026)": True, "Month first (01/31/2026)": False}


def render():
    st.title("Data")
    st.caption("Bring in price data and trade logs. Everything is stored locally on this computer.")
    t0, t1, t2, t3, t4 = st.tabs(["🏦 Download forex (OANDA)", "📄 Import price CSV", "⬇️ Download crypto",
                                  "📒 Import trade log", "🗂️ My datasets"])
    with t0:
        _oanda_download()
    with t1:
        _csv_wizard()
    with t2:
        _crypto_download()
    with t3:
        _trade_log_wizard()
    with t4:
        _dataset_list()


# --------------------------------------------------------------------------- CSV price import
def _csv_wizard():
    st.markdown(
        "**Step 1 - choose a file.** Works with exports from MetaTrader 4/5 (History Center or 'Export bars'), "
        "TradingView ('Export chart data', paid plans), cTrader, Dukascopy, crypto exchanges, and most other "
        "tools. A sample file is in the `sample_data` folder if you want to try the wizard.")
    up = st.file_uploader("CSV or TXT file", type=["csv", "txt"], key="price_csv")
    if up is None:
        return
    try:
        raw = read_table(up.getvalue(), up.name)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not read this file as a table: {e}")
        return
    st.markdown(f"Read **{len(raw):,} rows** and **{raw.shape[1]} columns**. First rows:")
    st.dataframe(raw.head(8), hide_index=True)

    st.markdown("**Step 2 - tell me which column is which.** I've guessed; fix anything that's wrong.")
    guess = guess_mapping(raw.columns)
    cols = ["(none)"] + list(raw.columns)
    mapping = {}
    c = st.columns(4)
    labels = {"timestamp": "Date + time (one column)", "date": "Date (if separate)", "time": "Time (if separate)",
              "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume (optional)"}
    for i, f in enumerate(["timestamp", "date", "time", "open", "high", "low", "close", "volume"]):
        g = guess.get(f)
        sel = c[i % 4].selectbox(labels[f], cols, index=cols.index(g) if g in cols else 0, key=f"map_{f}")
        mapping[f] = None if sel == "(none)" else sel

    st.markdown("**Step 3 - describe the data.**")
    c1, c2, c3, c4 = st.columns(4)
    symbol = normalize_symbol(c1.text_input("Symbol", value=guess_symbol(up.name), help="e.g. EURUSD, BTCUSD"))
    inst = get_instrument(symbol or "X")
    asset_class = c2.selectbox("Market", ["forex", "crypto"], index=0 if inst.asset_class == "forex" else 1)
    tz_keys = list(TZ_CHOICES)
    broker_tz = get_settings().broker().csv_timezone
    tz = c3.selectbox("Timezone of the timestamps", tz_keys, index=tz_keys.index(broker_tz) if broker_tz in tz_keys else 0,
                      format_func=lambda k: f"{k} - {TZ_CHOICES[k]}",
                      help="Which clock the file was written in. Pre-selected from your broker profile. If "
                           "unsure, pick UTC; the checker will tell you if the forex weekly open looks shifted.")
    order = c4.selectbox("Date order", list(DATE_ORDER))

    if st.button("Check data", type="primary"):
        try:
            res = build_ohlcv(raw, mapping, tz, DATE_ORDER[order])
        except ValueError as e:
            st.error(str(e))
            return
        st.session_state["csv_build"] = (res, symbol, asset_class, up.name)

    if "csv_build" not in st.session_state or st.session_state["csv_build"][3] != up.name:
        return
    res, symbol, asset_class, fname = st.session_state["csv_build"]
    cfg = get_settings().session_for(asset_class)
    report = validate_ohlcv(res.df, asset_class, cfg)
    st.markdown("**Step 4 - data check report**")
    st.markdown(f"**{report.headline()}**")
    for n in res.notes:
        st.info(n)
    for issue in report.issues:
        severity_box(issue)

    cleaned, log = clean_ohlcv(res.df)
    if log:
        st.markdown("**'Fix automatically' will:** " + " ".join(log) + " Prices are never invented or interpolated.")
    if cleaned.empty:
        st.error("Nothing usable left after cleaning. Check the column mapping and date settings.")
        return
    tf = timeframe_label(bar_minutes(cleaned.index))
    name = st.text_input("Dataset name", value=f"{symbol} {tf} ({fname})")
    if st.button(f"Fix automatically and save ({len(cleaned):,} bars)", disabled=not symbol):
        ds_id = get_store().save_dataset(cleaned, name=name, symbol=symbol, asset_class=asset_class, timeframe=tf,
                                         source=f"csv:{fname}")
        st.session_state.pop("csv_build", None)
        st.session_state["chart_id"] = ds_id
        st.success(f"Saved as dataset #{ds_id}. Open **Chart** to view it.")


# --------------------------------------------------------------------------- OANDA download
def _oanda_download():
    from dataclasses import replace

    from edgelab.config import api_key
    from edgelab.data.providers import oanda
    from edgelab.data.sessions import in_window
    from edgelab.ui.state import oanda_client, update_settings

    s = get_settings()
    st.markdown(
        "Download forex candles straight from **your OANDA account** (practice or live). These are the same prices "
        "your OANDA charts show, so the analysis matches what you trade. Read-only: EdgeLab only sends GET requests "
        "and cannot place, change or cancel orders.")
    if not api_key("OANDA_API_KEY"):
        st.info(
            "**One-time setup:**\n"
            "1. Log in to OANDA and open *My Account -> Manage API Access* (practice accounts have this too). "
            "Generate a personal access token.\n"
            "2. In the EdgeLab folder, copy `.env.example` to a new file called `.env` (if you haven't already).\n"
            "3. Put your token after `OANDA_API_KEY=` and save. Set `Account type` under Settings -> Account -> "
            "Sync from OANDA to *practice* or *live* to match the token.\n"
            "4. Close and re-open EdgeLab (run.bat).\n\n"
            "Treat the token like a password: it never leaves your computer except to talk to OANDA.")
        return
    c1, c2, c3 = st.columns(3)
    sym = c1.selectbox("Pair", list(oanda.INSTRUMENTS), key="oanda_sym")
    tf = c2.selectbox("Timeframe", ["5m", "15m", "1h", "1d"], key="oanda_tf")
    default_days = {"5m": 365, "15m": 730, "1h": 1095, "1d": 3650}[tf]
    days = c3.number_input("Days of history", 1, 7300, default_days, key=f"oanda_days_{tf}")
    measure = st.checkbox("Also measure the real spread (bid/ask) - recommended", value=True,
                          help="Downloads bid and ask prices too, so EdgeLab can tell you the typical spread "
                               "during your trading window and use it for cost calculations.")
    n = days * 1440 / {"5m": 5, "15m": 15, "1h": 60, "1d": 1440}[tf] * 5 / 7
    st.caption(f"About {n:,.0f} candles in {max(1, n / oanda.MAX_COUNT):,.0f} requests. Environment: "
               f"**{s.oanda_environment}**.")
    if st.button("Download from OANDA", type="primary"):
        end = pd.Timestamp.now(tz="UTC").floor("min")
        bar = st.progress(0.0, text="Connecting to OANDA...")
        try:
            df = oanda_client().candles(sym, tf, end - pd.Timedelta(days=int(days)), end,
                                        price="MBA" if measure else "M",
                                        progress=lambda f, m: bar.progress(min(1.0, f), text=m))
        except Exception as e:  # noqa: BLE001
            bar.empty()
            st.error(f"Download failed: {e}")
            return
        bar.empty()
        if df.empty:
            st.error("OANDA returned no candles for that period.")
            return
        inst = get_instrument(sym)
        cfg = s.session_for("forex")
        stats = oanda.spread_stats(df, inst.pip, in_window(df.index, cfg)) if measure else None
        prices = df[["open", "high", "low", "close", "volume"]]
        report = validate_ohlcv(prices, "forex", cfg)
        st.markdown(f"**{report.headline()}**")
        for issue in report.issues:
            severity_box(issue)
        cleaned, _ = clean_ohlcv(prices)
        note = ""
        if stats:
            note = (f"Spread median {stats['median_all']:.2f} pips overall"
                    + (f", {stats['median_window']:.2f} pips in your window" if "median_window" in stats else ""))
        ds_id = get_store().save_dataset(cleaned, name=f"{sym} {tf} (OANDA {s.oanda_environment})", symbol=sym,
                                         asset_class="forex", timeframe=tf, source=f"oanda:{s.oanda_environment}",
                                         notes=note)
        st.cache_data.clear()
        st.success(f"Saved {len(cleaned):,} candles as dataset #{ds_id}. Open **Chart** to view them.")
        if stats:
            st.session_state["oanda_spread"] = (sym, stats)
    if "oanda_spread" in st.session_state:
        sym_s, stats = st.session_state["oanda_spread"]
        win = stats.get("median_window", stats["median_all"])
        st.markdown(f"**Measured spread on {sym_s}:** median **{win:.2f} pips** during your trading window "
                    f"(90% of the time below {stats.get('p90_window', stats['p90_all']):.2f}); "
                    f"{stats['median_all']:.2f} pips across all hours. Spreads are widest around the 22:00 UK "
                    "rollover and in quiet hours.")
        b = s.broker()
        if st.button(f"Use {win:.2f} pips for {sym_s} in my '{b.name}' broker profile"):
            spreads = dict(b.spreads_pips)
            spreads[sym_s] = round(win, 2)
            prof = replace(b, spreads_pips=spreads,
                           notes="Spreads measured from OANDA data where available; other costs are your inputs.")
            s.brokers[b.name] = prof.to_dict()
            update_settings(s)
            st.session_state.pop("oanda_spread")
            st.success("Spread saved to your broker profile.")


# --------------------------------------------------------------------------- crypto download
def _crypto_download():
    st.markdown(
        "Download crypto candles from **Coinbase's public market data** - no account or API key needed, and it "
        "works from the UK. GBP markets (BTCGBP, ETHGBP) are priced directly in pounds. Read-only: this cannot "
        "place orders.")
    c1, c2, c3 = st.columns(3)
    sym = c1.selectbox("Market", list(coinbase.PRODUCTS))
    tf = c2.selectbox("Timeframe", ["5m", "15m", "1h", "1d"])
    days = c3.number_input("Days of history", 1, 1500, 90 if tf in ("5m", "15m") else 365)
    est = days * 1440 / {"5m": 5, "15m": 15, "1h": 60, "1d": 1440}[tf]
    st.caption(f"About {est:,.0f} candles, {est / 300:,.0f} requests (~{est / 300 * 0.4:,.0f} seconds).")
    if st.button("Download", type="primary"):
        end = pd.Timestamp.now(tz="UTC").floor("min")
        start = end - pd.Timedelta(days=int(days))
        bar = st.progress(0.0, text="Starting download...")
        try:
            df = coinbase.fetch_candles(coinbase.PRODUCTS[sym], tf, start, end,
                                        progress=lambda f, m: bar.progress(min(1.0, f), text=m))
        except Exception as e:  # noqa: BLE001
            bar.empty()
            st.error(f"Download failed: {e}. Check your internet connection; Coinbase may also be unavailable "
                     "in your region.")
            return
        bar.empty()
        report = validate_ohlcv(df, "crypto", get_settings().session_for("crypto"))
        st.markdown(f"**{report.headline()}**")
        for issue in report.issues:
            severity_box(issue)
        cleaned, _ = clean_ohlcv(df)
        if cleaned.empty:
            st.error("No candles returned.")
            return
        ds_id = get_store().save_dataset(cleaned, name=f"{sym} {tf} (Coinbase)", symbol=sym, asset_class="crypto",
                                         timeframe=tf, source="coinbase")
        st.success(f"Saved {len(cleaned):,} candles as dataset #{ds_id}.")


# --------------------------------------------------------------------------- trade log import
def _trade_log_wizard():
    st.markdown(
        "Import past trades from any broker or platform as CSV. You don't have a log yet - that's fine; the "
        "journal (Stage 6) will let you record trades here. `sample_data/sample_trade_log.csv` shows the format.")
    up = st.file_uploader("Trade log CSV", type=["csv", "txt"], key="trade_csv")
    if up is None:
        _trade_summary()
        return
    try:
        raw = read_table(up.getvalue(), up.name)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not read this file: {e}")
        return
    st.dataframe(raw.head(8), hide_index=True)
    guess = guess_trade_mapping(raw.columns)
    cols = ["(none)"] + list(raw.columns)
    mapping = {}
    c = st.columns(4)
    for i, f in enumerate(ALL_FIELDS):
        label = f.replace("_", " ").capitalize() + ("" if f in REQUIRED else " (optional)")
        g = guess.get(f)
        sel = c[i % 4].selectbox(label, cols, index=cols.index(g) if g in cols else 0, key=f"tmap_{f}")
        mapping[f] = None if sel == "(none)" else sel
    c1, c2, c3 = st.columns(3)
    default_symbol = c1.text_input("Symbol (if the file has no symbol column)", value=guess_symbol(up.name))
    tz = c2.selectbox("Timezone of the times", list(TZ_CHOICES), key="trade_tz")
    order = c3.selectbox("Date order", list(DATE_ORDER), key="trade_order")
    ccy = get_settings().account_currency
    st.caption(f"Size means units (10,000 units = 1 mini lot = 0.1 lot on forex; coins for crypto). P&L is converted "
               f"to {ccy} at the exit-time exchange rate, using your own imported price data where it covers the date "
               "and the reference rates in Settings otherwise. Fees/commission are assumed to be in "
               f"{ccy} already.")
    if st.button("Check trades", type="primary"):
        try:
            res = build_trades(raw, mapping, tz, default_symbol, DATE_ORDER[order], ccy, get_converter())
        except ValueError as e:
            st.error(str(e))
            return
        st.session_state["trade_build"] = (res, up.name)
    if "trade_build" not in st.session_state or st.session_state["trade_build"][1] != up.name:
        return
    res, fname = st.session_state["trade_build"]
    for issue in res.issues:
        severity_box(issue)
    t = res.trades
    st.markdown(f"**{len(t)} valid trades.**")
    if not t.empty:
        st.dataframe(t[["symbol", "side", "entry_time", "exit_time", "entry_price", "exit_price", "size", "fees",
                        "pnl", "pnl_currency", "r_multiple", "rate_source"]].head(50), hide_index=True)
        if st.button(f"Save {len(t)} trades"):
            n = get_store().save_trades(t, source=f"import:{fname}")
            st.session_state.pop("trade_build", None)
            st.success(f"Saved {n} trades. Turn on 'My imported trades' on the chart to see them.")


def _trade_summary():
    t = get_store().load_trades()
    if t.empty:
        return
    st.markdown("**Imported trades**")
    wins = (t["pnl"] > 0).mean() * 100
    c1, c2, c3 = st.columns(3)
    c1.metric("Trades", len(t))
    c2.metric("Win rate", f"{wins:.0f}%")
    ccys = t["pnl_currency"].dropna().unique() if "pnl_currency" in t else []
    c3.metric("Total P&L", f"{t['pnl'].sum():,.2f} {ccys[0] if len(ccys) == 1 else ''}")
    if len(ccys) > 1:
        st.warning("These trades were imported with different account currencies, so the total mixes currencies. "
                   "Delete and re-import them.")
    if len(t) < 30:
        st.caption(f"Only {len(t)} trades - far too few to judge a strategy. Treat these numbers as anecdotes.")
    if st.button("Delete all imported trades"):
        get_store().delete_trades()
        st.rerun()


# --------------------------------------------------------------------------- dataset list
def _dataset_list():
    ds = datasets()
    if ds.empty:
        st.info("No datasets.")
        return
    view = ds[["id", "name", "symbol", "asset_class", "timeframe", "n_bars", "start", "end", "source"]].copy()
    view["start"] = view["start"].dt.strftime("%Y-%m-%d")
    view["end"] = view["end"].dt.strftime("%Y-%m-%d")
    st.dataframe(view, hide_index=True)
    mine = ds[ds["is_sample"] == 0]
    if not mine.empty:
        c1, c2 = st.columns([3, 1])
        victim = c1.selectbox("Delete one of your datasets", mine["id"].tolist(),
                              format_func=lambda i: mine.set_index("id").loc[i, "name"])
        if c2.button("Delete", type="secondary"):
            get_store().delete_dataset(int(victim))
            st.cache_data.clear()
            st.rerun()
