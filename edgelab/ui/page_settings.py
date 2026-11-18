from dataclasses import replace

import streamlit as st

from edgelab.data.sessions import DEFAULT_SESSIONS, SessionConfig
from edgelab.learning.glossary import help_text
from edgelab.ui.state import get_settings, update_settings

DISPLAY_TZ = ["Europe/London", "UTC", "America/New_York", "Europe/Berlin", "Asia/Tokyo", "Australia/Sydney",
              "America/Chicago", "America/Los_Angeles", "Asia/Singapore", "Asia/Dubai", "Africa/Johannesburg"]


def render():
    s = get_settings()
    st.title("Settings")
    st.caption("Saved to userdata/settings.json on this computer. API keys go in the .env file, never here.")

    with st.form("settings_form"):
        st.subheader("Account and risk")
        c1, c2, c3 = st.columns(3)
        account = c1.number_input("Account size (USD)", min_value=100.0, value=float(s.account_size), step=500.0)
        risk = c2.number_input("Risk per trade (% of account)", 0.05, 5.0, float(s.risk_pct), 0.05,
                               help=help_text("Position sizing"))
        c3.metric("= risk per trade", f"${account * risk / 100:,.2f}")

        st.subheader("Costs (used by the backtester in Stage 4)")
        c1, c2, c3 = st.columns(3)
        comm = c1.number_input("Commission per trade (USD, round trip)", 0.0, 500.0, float(s.commission_per_trade), 0.5,
                               help=help_text("Commission"))
        spread = c2.number_input("Forex spread (pips)", 0.0, 20.0, float(s.forex_spread_pips), 0.1,
                                 help=help_text("Spread"))
        fx_slip = c3.number_input("Forex slippage per side (pips)", 0.0, 10.0, float(s.forex_slippage_pips), 0.1,
                                  help=help_text("Slippage"))
        c1, c2, _ = st.columns(3)
        cfee = c1.number_input("Crypto fee per side (%)", 0.0, 2.0, float(s.crypto_fee_pct), 0.01)
        cslip = c2.number_input("Crypto slippage per side (%)", 0.0, 2.0, float(s.crypto_slippage_pct), 0.01)

        st.subheader("Time and sessions")
        tz = st.selectbox("Show chart times in", DISPLAY_TZ,
                          index=DISPLAY_TZ.index(s.display_tz) if s.display_tz in DISPLAY_TZ else 0)
        orm = st.select_slider("Opening range length (minutes)", [15, 30, 45, 60, 90, 120],
                               value=int(s.opening_range_minutes), help=help_text("Opening range"))
        new_sessions = {}
        for ac in ("forex", "crypto"):
            cur = s.session_for(ac)
            st.markdown(f"**{ac.title()} session window** - the hours you actually trade. "
                        f"(Trading day starts {cur.day_start} {cur.day_tz}.)")
            c1, c2, c3 = st.columns(3)
            wtz = c1.selectbox("Window timezone", DISPLAY_TZ, key=f"wtz_{ac}",
                               index=DISPLAY_TZ.index(cur.window_tz) if cur.window_tz in DISPLAY_TZ else 0)
            ws = c2.text_input("Start (HH:MM)", cur.window_start, key=f"ws_{ac}")
            we = c3.text_input("End (HH:MM, 24:00 = midnight)", cur.window_end, key=f"we_{ac}")
            new_sessions[ac] = replace(cur, window_tz=wtz, window_start=ws.strip(), window_end=we.strip())
        reset = st.checkbox("Reset sessions to defaults")
        if st.form_submit_button("Save settings", type="primary"):
            bad = [f"{ac}: {v.window_start}-{v.window_end}" for ac, v in new_sessions.items()
                   if not (_valid(v.window_start) and _valid(v.window_end))]
            if bad:
                st.error("Times must look like 08:00. Problem with: " + ", ".join(bad))
                return
            s.account_size, s.risk_pct, s.commission_per_trade = account, risk, comm
            s.forex_spread_pips, s.forex_slippage_pips = spread, fx_slip
            s.crypto_fee_pct, s.crypto_slippage_pct = cfee, cslip
            s.display_tz, s.opening_range_minutes = tz, int(orm)
            src = DEFAULT_SESSIONS if reset else new_sessions
            s.sessions = {k: SessionConfig.to_dict(v) for k, v in src.items()}
            update_settings(s)
            st.success("Saved.")

    st.info("**Cost check:** on EURUSD, 10,000 units = 0.1 lot, so 1 pip is about $1. With a "
            f"{s.forex_spread_pips:.1f}-pip spread plus {s.forex_slippage_pips:.1f} pips slippage per side, every "
            f"0.1-lot trade starts about ${(s.forex_spread_pips + 2 * s.forex_slippage_pips):.2f} behind. "
            "Small targets are eaten quickly by costs.")


def _valid(hhmm: str) -> bool:
    try:
        h, m = hhmm.split(":")
        return 0 <= int(h) <= 24 and 0 <= int(m) < 60 and len(m) == 2
    except ValueError:
        return False
