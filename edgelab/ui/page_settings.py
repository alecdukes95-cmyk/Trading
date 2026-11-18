from dataclasses import replace

import pandas as pd
import streamlit as st

from edgelab.brokers import BUILTIN_BROKERS, COMMISSION_MODELS, BrokerProfile
from edgelab.data.sessions import DEFAULT_SESSIONS, SessionConfig, session_times_today
from edgelab.fx import DEFAULT_REFERENCE_RATES
from edgelab.learning.glossary import help_text
from edgelab.ui.state import get_settings, update_settings

DISPLAY_TZ = ["Europe/London", "UTC", "America/New_York", "Europe/Berlin", "Asia/Tokyo", "Australia/Sydney",
              "Europe/Athens", "America/Chicago", "Asia/Singapore", "Asia/Dubai"]
CURRENCY_SYMBOL = {"GBP": "£", "USD": "$", "EUR": "€"}


def money(x, ccy: str) -> str:
    """'£1,234.56' - or '-' when the value is unknown (e.g. no stop-loss, no exchange rate)."""
    if x is None or (isinstance(x, float) and x != x):
        return "-"
    return f"{CURRENCY_SYMBOL.get(ccy, ccy + ' ')}{x:,.2f}"


def num(x, spec: str = ".2f", suffix: str = "", default: str = "-") -> str:
    """Format a number that may be missing: num(None, '.2f', '%') -> '-'."""
    if x is None or (isinstance(x, float) and x != x):
        return default
    return f"{x:{spec}}{suffix}"


def render():
    s = get_settings()
    st.title("Settings")
    st.caption("Saved to userdata/settings.json on this computer. API keys go in the .env file, never here.")
    t1, t5, t2, t3, t4 = st.tabs(["💷 Account & risk", "🔔 Alerts", "🏦 Broker", "💱 Exchange rates",
                                  "🕒 Sessions & time"])
    with t1:
        _account(s)
    with t5:
        _alerts(s)
    with t2:
        _broker(s)
    with t3:
        _rates(s)
    with t4:
        _sessions(s)


# ----------------------------------------------------------------------------------- account
def _account(s):
    _balance(s)
    st.divider()
    with st.form("account_form"):
        st.markdown("**Currency and risk rule**")
        c1, c2 = st.columns(2)
        ccy = c1.selectbox("Account currency", ["GBP", "USD", "EUR"], index=["GBP", "USD", "EUR"].index(s.account_currency)
                           if s.account_currency in ("GBP", "USD", "EUR") else 0,
                           help="All P&L, margin and costs are shown in this currency.")
        risk = c2.number_input("Risk per trade (% of current balance)", 0.05, 5.0, float(s.risk_pct), 0.05,
                               help=help_text("Position sizing"))
        st.markdown("**Forex position size**")
        c1, c2 = st.columns(2)
        mode = c1.radio("Sizing method", ["fixed", "risk"], index=0 if s.forex_sizing == "fixed" else 1, horizontal=True,
                        format_func=lambda m: {"fixed": "Fixed size every trade", "risk": "Size from my stop (risk-based)"}[m])
        units = c2.number_input("Fixed size (units)", 1_000, 1_000_000, int(s.forex_fixed_units), 1_000,
                                help="10,000 units = 1 mini lot = 0.1 standard lot. 1,000 units = 1 micro lot.")
        st.caption(f"{units:,} units = {units / 100_000:.2f} lots. With a fixed size, the risk rule limits how wide "
                   "your stop can be; the Position & margin page shows that limit for any trade. With risk-based "
                   "sizing, the size is calculated so that hitting the stop loses exactly your risk amount.")
        cmode = st.radio("Crypto sizing", ["risk", "fixed"], index=0 if s.crypto_sizing == "risk" else 1, horizontal=True,
                         format_func=lambda m: {"fixed": "Fixed", "risk": "Size from my stop (recommended)"}[m])
        if st.form_submit_button("Save", type="primary"):
            s.account_currency, s.risk_pct = ccy, float(risk)
            s.forex_sizing, s.forex_fixed_units, s.crypto_sizing = mode, int(units), cmode
            update_settings(s)
            st.success("Saved.")


def _balance(s):
    from edgelab.account import balance_history
    from edgelab.config import api_key
    from edgelab.ui.state import balance_summary, get_store, sync_oanda_balance

    store = get_store()
    ccy = s.account_currency
    summ = balance_summary()
    st.subheader("Account balance")
    c1, c2, c3 = st.columns(3)
    c1.metric("Current balance", money(summ.balance, ccy))
    c2.metric(f"Risk per trade ({s.risk_pct:.2f}%)", money(summ.balance * s.risk_pct / 100, ccy))
    c3.metric("Closed trades counted", summ.n_trades)
    st.caption(summ.describe() + ". Your balance updates automatically as trades are recorded; enter a new "
               "figure, or sync from OANDA, whenever you want to re-anchor it.")
    if summ.n_skipped:
        st.warning(f"{summ.n_skipped} recorded trades closed after your balance figure have no {ccy} P&L, so they "
                   "were not counted. Re-import them with the account currency set correctly.")
    if err := st.session_state.pop("_oanda_sync_error", None):
        st.warning(f"Automatic OANDA sync failed: {err}")

    t_set, t_flow, t_oanda, t_hist = st.tabs(["Enter balance", "Deposit / withdrawal", "Sync from OANDA", "History"])
    with t_set:
        with st.form("set_balance"):
            c1, c2, c3 = st.columns(3)
            amt = c1.number_input(f"Balance ({ccy})", min_value=0.0, value=float(round(summ.balance, 2)), step=100.0)
            d = c2.date_input("As of date", value=pd.Timestamp.now(tz="Europe/London").date())
            t = c3.time_input("Time (UK)", value=pd.Timestamp.now(tz="Europe/London").floor("min").time())
            note = st.text_input("Note (optional)", "")
            st.caption("Use the balance shown by your broker at that moment. Trades that close after this time are "
                       "added to it automatically; earlier trades are assumed to be included already.")
            if st.form_submit_button("Set balance", type="primary"):
                ts = _event_time(d, t)
                store.add_balance_event(ts, "set", amt, ccy, note)
                s.account_balance = float(amt)
                update_settings(s)
                st.success("Balance updated.")
                st.rerun()
    with t_flow:
        with st.form("flow"):
            c1, c2, c3 = st.columns(3)
            kind = c1.selectbox("Type", ["deposit", "withdrawal", "adjustment"],
                                format_func=lambda k: {"deposit": "Deposit (+)", "withdrawal": "Withdrawal (-)",
                                                       "adjustment": "Adjustment (+/-, e.g. fees, interest)"}[k])
            amt = c2.number_input(f"Amount ({ccy})", value=0.0, step=50.0)
            d = c3.date_input("Date", value=pd.Timestamp.now(tz="Europe/London").date(), key="flow_date")
            note = st.text_input("Note", "", key="flow_note")
            if st.form_submit_button("Add") and amt:
                ts = _event_time(d, None)
                if summ.baseline_time is not None and ts <= summ.baseline_time:
                    st.warning("That date is before your current balance figure, so it is treated as already "
                               "included and won't change the balance. Enter a new balance figure instead.")
                store.add_balance_event(ts, kind, amt, ccy, note)
                st.rerun()
    with t_oanda:
        has_key = bool(api_key("OANDA_API_KEY"))
        st.markdown("Reads your **balance, equity (NAV) and margin in use** from OANDA. Read-only: EdgeLab only "
                    "sends GET requests and has no ability to place or change orders.")
        if not has_key:
            st.info("No OANDA token found. To enable this: in OANDA go to *My Account -> Manage API Access* and "
                    "generate a token, then open the file `.env` in the EdgeLab folder (copy `.env.example` if "
                    "it doesn't exist) and add a line `OANDA_API_KEY=your-token`. Restart EdgeLab.")
        with st.form("oanda_form"):
            c1, c2, c3 = st.columns(3)
            env = c1.selectbox("Account type", ["practice", "live"], index=0 if s.oanda_environment == "practice" else 1,
                               format_func=lambda e: {"practice": "Practice (demo)", "live": "Live"}[e])
            acct = c2.text_input("Account ID (optional)", s.oanda_account_id,
                                 help="Looks like 101-004-1234567-001. Leave blank to use the first account.")
            auto = c3.checkbox("Sync automatically when EdgeLab starts", value=bool(s.oanda_auto_sync))
            save = st.form_submit_button("Save OANDA settings")
            go = st.form_submit_button("Sync balance now", type="primary", disabled=not has_key)
        if save or go:
            s.oanda_environment, s.oanda_account_id, s.oanda_auto_sync = env, acct.strip(), bool(auto)
            update_settings(s)
        if go:
            try:
                res = sync_oanda_balance()
                if res.currency != ccy:
                    st.warning(f"Your OANDA account is in {res.currency} but EdgeLab is set to {ccy}. Change the "
                               "account currency below so the numbers match.")
                st.success(f"Synced: balance {res.balance:,.2f} {res.currency}, NAV {res.nav:,.2f}, margin used "
                           f"{res.margin_used:,.2f}, available {res.margin_available:,.2f}, {res.open_trades} open trades.")
            except Exception as e:  # noqa: BLE001
                st.error(f"Could not sync from OANDA: {e}")
        last = s.oanda_last_summary
        if last:
            st.caption(f"Last sync {pd.Timestamp(last['fetched_at']).tz_convert('Europe/London'):%d %b %Y %H:%M} UK: "
                       f"balance {last['balance']:,.2f}, NAV {last['nav']:,.2f}, margin used {last['margin_used']:,.2f} "
                       f"{last['currency']}.")
    with t_hist:
        ev = store.load_balance_events()
        from edgelab.ui.state import real_trades

        hist = balance_history(ev, real_trades(store), ccy, s.account_balance)
        if len(hist) > 1:
            st.line_chart(hist.rename("Balance"), height=220)
        if ev.empty:
            st.caption(f"No balance entries yet - using {money(s.account_balance, ccy)} from settings.")
        else:
            view = ev.assign(when=ev["time"].dt.tz_convert("Europe/London").dt.strftime("%Y-%m-%d %H:%M"))
            st.dataframe(view[["id", "when", "kind", "amount", "currency", "note"]], hide_index=True)
            c1, c2 = st.columns([3, 1])
            victim = c1.selectbox("Delete an entry", ev["id"].tolist(), key="del_bal")
            if c2.button("Delete entry"):
                store.delete_balance_event(int(victim))
                st.rerun()


# ----------------------------------------------------------------------------------- alerts
def _alerts(s):
    from edgelab.instruments import FOREX_SYMBOLS
    from edgelab.patterns.base import PATTERNS, TRADEABLE

    st.markdown("Which markets to watch and when to alert you. Alerts are educational order ideas - EdgeLab never "
                "places orders.")
    with st.form("alerts_form"):
        wl = st.multiselect("Watchlist (forex pairs)", FOREX_SYMBOLS, default=[x for x in s.watchlist if x in FOREX_SYMBOLS])
        tfs = st.multiselect("Timeframes to scan", ["5m", "15m", "1h"], default=s.scan_timeframes,
                             help="5m gives many more (noisier) alerts; 15m is a calmer default.")
        keys = [k for k in TRADEABLE if k not in ("bear_flag",)]
        names = {k: PATTERNS[k].name.replace("Bull flag", "Bull / bear flags") for k in keys}
        cur = s.enabled_patterns or keys
        pats = st.multiselect("Patterns that trigger alerts", keys, default=[k for k in cur if k in keys],
                              format_func=names.get)
        q = st.radio("Alert quality", ["strict", "normal", "all"], index=["strict", "normal", "all"].index(s.alert_quality),
                     horizontal=True, format_func={"strict": "Strict - only signals with no warning flags",
                                                   "normal": "Normal - at most one warning flag",
                                                   "all": "All signals"}.get)
        src = st.radio("Live candle source for the scanner", ["oanda", "mt5"], index=0 if s.live_source == "oanda" else 1,
                       horizontal=True, format_func={"oanda": "OANDA (free practice-account token)",
                                                     "mt5": "MetaTrader 5 terminal on this PC (free demo account)"}.get)
        c1, c2, c3 = st.columns(3)
        only_win = c1.checkbox("Only during my trading window", value=s.notify_only_in_window)
        desk = c2.checkbox("Desktop notifications (Windows pop-up)", value=s.notify_desktop)
        snd = c3.checkbox("Sound in the browser", value=s.notify_sound)
        c1, c2 = st.columns(2)
        tr = c1.number_input("Take-profit at (R multiples of the risk)", 0.5, 10.0, float(s.target_r), 0.5,
                             help=help_text("R-multiple"))
        ex = c2.number_input("Cancel an unfilled entry after (bars)", 1, 20, int(s.order_expiry_bars))
        if st.form_submit_button("Save", type="primary"):
            if "bull_flag" in pats:
                pats = pats + ["bear_flag"]
            s.watchlist, s.scan_timeframes = wl or s.watchlist, tfs or ["15m"]
            s.enabled_patterns = [] if set(pats) >= set(TRADEABLE) else pats
            s.alert_quality, s.notify_only_in_window, s.notify_desktop, s.notify_sound = q, only_win, desk, snd
            s.target_r, s.order_expiry_bars = float(tr), int(ex)
            s.live_source = src
            update_settings(s)
            st.success("Saved. Restart the scanner for changes to take effect.")
    if st.button("Send a test desktop notification"):
        from edgelab.live.notify import desktop_notify

        ok = desktop_notify("EdgeLab test", "If you can read this, desktop alerts work.")
        (st.success if ok else st.warning)("Sent - check your screen's corner." if ok else
                                           "Desktop notifications aren't available on this computer; alerts will "
                                           "still appear in the app.")


# ----------------------------------------------------------------------------------- broker
def _broker(s):
    profiles = s.broker_profiles()
    names = list(profiles)
    c1, c2 = st.columns([3, 2])
    active = c1.selectbox("Active broker", names, index=names.index(s.active_broker) if s.active_broker in names else 0,
                          help="Costs, leverage and margin rules for every calculation come from this profile.")
    if active != s.active_broker:
        s.active_broker = active
        update_settings(s)
    b = profiles[active]
    edited = active in s.brokers
    c2.markdown(f"**Platform:** {b.platform}  \n**Getting data:** {b.data_access or '-'}")
    if b.notes:
        st.warning(b.notes) if "placeholder" in b.notes.lower() and not edited else st.caption(b.notes)
    st.info("UK retail rules are pre-filled: leverage up to **30:1 on major pairs** and **20:1 on other pairs**, "
            "margin **close-out at 50%**, and **no crypto derivatives** for retail clients (crypto is treated as "
            "unleveraged spot). Change them only if your account is different, e.g. a professional account.")

    with st.form("broker_form"):
        st.markdown("**Costs**")
        c1, c2, c3 = st.columns(3)
        models = list(COMMISSION_MODELS)
        cm = c1.selectbox("Commission type", models, index=models.index(b.commission_model),
                          format_func=COMMISSION_MODELS.get, help=help_text("Commission"))
        cv = c2.number_input(f"Commission ({'% per side' if cm == 'percent_per_side' else s.account_currency})",
                             0.0, 1000.0, float(b.commission_value), 0.001 if cm == "percent_per_side" else 0.5,
                             format="%.3f" if cm == "percent_per_side" else "%.2f")
        slip = c3.number_input("Forex slippage per side (pips)", 0.0, 10.0, float(b.slippage_pips), 0.1,
                               help=help_text("Slippage"))
        st.markdown("Typical **spread** per pair (pips) - check your platform during the hours you trade:")
        sp = pd.DataFrame({"pair": list(b.spreads_pips), "spread_pips": list(b.spreads_pips.values())})
        sp_edit = st.data_editor(sp, num_rows="dynamic", hide_index=True, key=f"spreads_{active}",
                                 column_config={"pair": st.column_config.TextColumn("Pair"),
                                                "spread_pips": st.column_config.NumberColumn("Spread (pips)", min_value=0.0,
                                                                                             step=0.1, format="%.2f")})
        dsp = st.number_input("Spread for pairs not listed (pips)", 0.0, 50.0, float(b.default_spread_pips), 0.1)
        st.markdown("**Crypto costs**")
        c1, c2, c3 = st.columns(3)
        csp = c1.number_input("Spread (% of price)", 0.0, 5.0, float(b.crypto_spread_pct), 0.01)
        cfee = c2.number_input("Fee per side (%)", 0.0, 5.0, float(b.crypto_fee_pct), 0.01)
        cslip = c3.number_input("Slippage per side (%)", 0.0, 5.0, float(b.crypto_slippage_pct), 0.01)
        st.markdown("**Leverage and margin**")
        c1, c2, c3, c4 = st.columns(4)
        lmaj = c1.number_input("Major pairs (x:1)", 1.0, 500.0, float(b.leverage_fx_major), 1.0, help=help_text("Leverage"))
        lmin = c2.number_input("Other pairs (x:1)", 1.0, 500.0, float(b.leverage_fx_minor), 1.0)
        lcr = c3.number_input("Crypto (x:1)", 1.0, 100.0, float(b.leverage_crypto), 1.0)
        mco = c4.number_input("Margin close-out level (%)", 0.0, 200.0, float(b.margin_closeout_pct), 5.0,
                              help=help_text("Margin level"))
        c1, c2 = st.columns(2)
        plat = c1.text_input("Platform", b.platform)
        ctz = c2.selectbox("Timezone of this broker's CSV exports", DISPLAY_TZ,
                           index=DISPLAY_TZ.index(b.csv_timezone) if b.csv_timezone in DISPLAY_TZ else 1,
                           help="Pre-selected in the import wizard. MetaTrader brokers usually use GMT+2/+3 "
                                "server time (Europe/Athens). The import check will warn you if it looks wrong.")
        c1, c2 = st.columns(2)
        save = c1.form_submit_button(f"Save changes to '{active}'", type="primary")
        new_name = c2.text_input("...or save as a new profile named", "")
        save_new = c2.form_submit_button("Save as new profile")

    if save or save_new:
        spreads = {str(r["pair"]).upper().replace("/", ""): float(r["spread_pips"])
                   for _, r in sp_edit.dropna().iterrows() if str(r["pair"]).strip()}
        name = new_name.strip() if save_new else active
        if save_new and (not name or name in profiles):
            st.error("Choose a new, unused profile name.")
            return
        prof = replace(b, name=name, commission_model=cm, commission_value=float(cv), slippage_pips=float(slip),
                       spreads_pips=spreads, default_spread_pips=float(dsp), crypto_spread_pct=float(csp),
                       crypto_fee_pct=float(cfee), crypto_slippage_pct=float(cslip), leverage_fx_major=float(lmaj),
                       leverage_fx_minor=float(lmin), leverage_crypto=float(lcr), margin_closeout_pct=float(mco),
                       platform=plat, csv_timezone=ctz,
                       notes="Your own figures." if b.notes.startswith("Costs below are placeholders") else b.notes)
        s.brokers[name] = prof.to_dict()
        s.active_broker = name
        update_settings(s)
        st.success(f"Saved profile '{name}' and made it active.")
        st.rerun()

    if edited:
        label = "Reset to built-in values" if active in BUILTIN_BROKERS else f"Delete profile '{active}'"
        if st.button(label):
            s.brokers.pop(active, None)
            if active not in BUILTIN_BROKERS:
                s.active_broker = next(iter(BUILTIN_BROKERS))
            update_settings(s)
            st.rerun()


# ----------------------------------------------------------------------------------- rates
def _rates(s):
    st.markdown(
        f"Profits on most pairs are earned in USD or JPY and must be converted to **{s.account_currency}**. "
        "EdgeLab converts using, in order of preference:\n"
        "1. the trade's own price when that is the rate needed (e.g. GBPUSD trades);\n"
        "2. **your own imported price data** for the exact date (never the synthetic samples);\n"
        "3. the **reference rates** below, as a fallback. Anything converted this way is flagged.")
    df = pd.DataFrame({"pair": list(s.reference_rates), "rate": list(s.reference_rates.values())})
    with st.form("rates_form"):
        ed = st.data_editor(df, num_rows="dynamic", hide_index=True,
                            column_config={"rate": st.column_config.NumberColumn("Rate (quote per 1 base)", format="%.5f")})
        c1, c2 = st.columns(2)
        if c1.form_submit_button("Save rates", type="primary"):
            s.reference_rates = {str(r["pair"]).upper().replace("/", ""): float(r["rate"])
                                 for _, r in ed.dropna().iterrows() if r["rate"] > 0}
            update_settings(s)
            st.success("Saved.")
        if c2.form_submit_button("Restore defaults"):
            s.reference_rates = dict(DEFAULT_REFERENCE_RATES)
            update_settings(s)
            st.rerun()
    st.caption("Example: GBPUSD 1.27 means £1 = $1.27, so a $50 profit on EURUSD is £39.37.")


# ----------------------------------------------------------------------------------- sessions
def _sessions(s):
    today = session_times_today("Europe/London")
    rows = [{"Session": k, "Opens (UK time)": v[0], "Closes (UK time)": v[1]} for k, v in today.items()]
    st.markdown("**Main sessions today, in UK time** (daylight saving included)")
    st.table(pd.DataFrame(rows).set_index("Session"))
    asia_end, ny_start = today["Asia (Tokyo)"][1], today["New York"][0]
    st.markdown(f"- **Asia/London overlap:** 08:00-{asia_end}  \n- **London/New York overlap:** {ny_start}-17:00  \n"
                "For a few weeks each March and October/November the US and UK change clocks on different dates, so "
                "New York opens an hour earlier in UK time. EdgeLab handles this automatically.")
    with st.form("sessions_form"):
        tz = st.selectbox("Show chart times in", DISPLAY_TZ,
                          index=DISPLAY_TZ.index(s.display_tz) if s.display_tz in DISPLAY_TZ else 0)
        orm = st.select_slider("Opening range length (minutes)", [15, 30, 45, 60, 90, 120],
                               value=int(s.opening_range_minutes), help=help_text("Opening range"))
        new_sessions = {}
        for ac in ("forex", "crypto"):
            cur = s.session_for(ac)
            st.markdown(f"**{ac.title()} - your trading window** (the opening range starts here). "
                        f"Trading day starts {cur.day_start} {cur.day_tz}.")
            c1, c2, c3 = st.columns(3)
            wtz = c1.selectbox("Timezone", DISPLAY_TZ, key=f"wtz_{ac}",
                               index=DISPLAY_TZ.index(cur.window_tz) if cur.window_tz in DISPLAY_TZ else 0)
            ws = c2.text_input("Start (HH:MM)", cur.window_start, key=f"ws_{ac}")
            we = c3.text_input("End (HH:MM, 24:00 = midnight)", cur.window_end, key=f"we_{ac}")
            new_sessions[ac] = replace(cur, window_tz=wtz, window_start=ws.strip(), window_end=we.strip())
        reset = st.checkbox("Reset windows to defaults (08:00-17:00 UK time)")
        if st.form_submit_button("Save", type="primary"):
            bad = [f"{ac}: {v.window_start}-{v.window_end}" for ac, v in new_sessions.items()
                   if not (_valid(v.window_start) and _valid(v.window_end))]
            if bad:
                st.error("Times must look like 08:00. Problem with: " + ", ".join(bad))
                return
            src = DEFAULT_SESSIONS if reset else new_sessions
            s.display_tz, s.opening_range_minutes = tz, int(orm)
            s.sessions = {k: SessionConfig.to_dict(v) for k, v in src.items()}
            update_settings(s)
            st.success("Saved.")


def _event_time(d, t) -> pd.Timestamp:
    """UTC time for a balance entry. Today's date with the default time means 'right now' (to the second)."""
    now = pd.Timestamp.now(tz="UTC")
    local_now = now.tz_convert("Europe/London")
    if d == local_now.date() and (t is None or (t.hour, t.minute) == (local_now.hour, local_now.minute)):
        return now
    t = t or pd.Timestamp("12:00").time()
    ts = pd.Timestamp.combine(d, t).tz_localize("Europe/London", ambiguous=True, nonexistent="shift_forward")
    return ts.tz_convert("UTC")


def _valid(hhmm: str) -> bool:
    try:
        h, m = hhmm.split(":")
        return 0 <= int(h) <= 24 and 0 <= int(m) < 60 and len(m) == 2
    except ValueError:
        return False
