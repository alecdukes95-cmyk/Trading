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


def money(x: float, ccy: str) -> str:
    return f"{CURRENCY_SYMBOL.get(ccy, ccy + ' ')}{x:,.2f}"


def render():
    s = get_settings()
    st.title("Settings")
    st.caption("Saved to userdata/settings.json on this computer. API keys go in the .env file, never here.")
    t1, t2, t3, t4 = st.tabs(["💷 Account & risk", "🏦 Broker", "💱 Exchange rates", "🕒 Sessions & time"])
    with t1:
        _account(s)
    with t2:
        _broker(s)
    with t3:
        _rates(s)
    with t4:
        _sessions(s)


# ----------------------------------------------------------------------------------- account
def _account(s):
    with st.form("account_form"):
        c1, c2, c3 = st.columns(3)
        ccy = c1.selectbox("Account currency", ["GBP", "USD", "EUR"], index=["GBP", "USD", "EUR"].index(s.account_currency)
                           if s.account_currency in ("GBP", "USD", "EUR") else 0,
                           help="All P&L, margin and costs are shown in this currency.")
        bal = c2.number_input(f"Account balance ({ccy})", min_value=50.0, value=float(s.account_balance), step=250.0)
        risk = c3.number_input("Risk per trade (% of balance)", 0.05, 5.0, float(s.risk_pct), 0.05,
                               help=help_text("Position sizing"))
        st.markdown(f"Your risk rule: lose at most **{money(bal * risk / 100, ccy)}** per trade.")
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
            s.account_currency, s.account_balance, s.risk_pct = ccy, float(bal), float(risk)
            s.forex_sizing, s.forex_fixed_units, s.crypto_sizing = mode, int(units), cmode
            update_settings(s)
            st.success("Saved.")


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


def _valid(hhmm: str) -> bool:
    try:
        h, m = hhmm.split(":")
        return 0 <= int(h) <= 24 and 0 <= int(m) < 60 and len(m) == 2
    except ValueError:
        return False
