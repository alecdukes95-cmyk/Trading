import streamlit as st

from edgelab.ui.page_settings import money
from edgelab.ui.state import balance_summary, datasets, get_settings, get_store

STAGES = [
    ("1. Data intake + chart reader", "Free data (Dukascopy, MT5, OANDA practice, CSV/HistData) with plain-English "
     "checks; charts with VWAP, MAs, ATR, RSI, MACD, opening range, prior-day levels, S/R, sessions; 'what happened "
     "today'; GBP account, broker profiles, position & margin calculator."),
    ("2. Patterns + live alerts", "14 detectors on completed bars; click any pattern for the order idea, the visual "
     "cues and its history on your data; live scanner with desktop notifications."),
    ("3. Learning mode", "Replay trainer, chart quizzes, concepts library, paper trading against live prices with a "
     "pre-trade plan and discipline checks, progress tracking."),
    ("4. Edge builder + backtester", "No-code rules, realistic costs, in- vs out-of-sample, walk-forward, robustness "
     "grid, overfitting check against the number of variations tried."),
    ("5. Monte Carlo engine", "Bootstrap, shuffle, parameter uncertainty; risk of ruin, drawdowns, streaks, recovery, "
     "Kelly sizing, zero-edge reality check - on backtests, alerts, paper trades and your journal."),
    ("6. Trade journal", "Every trade with tags, emotions, grades, mistakes, lessons and screenshots; insights, P&L "
     "calendar, execution gap vs the rules."),
]


def setup_checks(s) -> list[tuple[bool, str, str]]:
    """(done, item, where to fix it) - the inputs only you can give."""
    from edgelab.config import api_key

    store = get_store()
    ds = datasets()
    real = not ds.empty and (ds["is_sample"] == 0).any()
    ev = store.load_balance_events()
    balance_set = not ev.empty and (ev["note"].fillna("").str.startswith("Starting balance (default") == False).any()  # noqa: E712
    edited = s.active_broker in s.brokers
    return [
        (bool(api_key("OANDA_API_KEY")), "OANDA practice token in .env (live prices, scanner, paper trading)",
         "Data -> OANDA"),
        (balance_set, "Your real account balance entered or synced", "Settings -> Account"),
        (real, "Real price history downloaded (instead of synthetic samples)", "Data -> Free history"),
        (edited, f"Your broker's real spreads/commission in the '{s.active_broker}' profile (placeholders otherwise)",
         "Settings -> Broker, or Data -> OANDA 'measure the real spread'"),
        (s.risk_pct == 0.65 and s.forex_fixed_units == 10_000, "Risk 0.65% per trade and 1 mini lot (your stated rules)",
         "Settings -> Account"),
    ]


def render():
    st.title("EdgeLab")
    st.caption("Learn to read charts, test trading ideas honestly, and understand risk. Educational tool only - "
               "it never places trades.")
    s = get_settings()
    ds = datasets()
    bal = balance_summary().balance
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Balance", money(bal, s.account_currency))
    c2.metric("Risk per trade", f"{s.risk_pct:.2f}%", money(bal * s.risk_pct / 100, s.account_currency),
              delta_color="off")
    c3.metric("Your own datasets", int((ds["is_sample"] == 0).sum()) if not ds.empty else 0)
    c4.metric("Broker profile", s.broker().name)

    checks = setup_checks(s)
    todo = [c for c in checks if not c[0]]
    with st.expander(f"✅ Setup checklist - {len(checks) - len(todo)} of {len(checks)} done", expanded=bool(todo)):
        for done, item, where in checks:
            st.markdown(("✅ " if done else "⬜ ") + item + ("" if done else f"  \n&nbsp;&nbsp;&nbsp;&nbsp;*Where:* {where}"))
        st.caption("Assumptions EdgeLab made until you confirm them are listed in USER_INPUT_NEEDED.md in the app folder.")

    st.subheader("A good way to use EdgeLab")
    st.markdown(
        """
1. **Learn the language** - *Concepts library* and *Glossary*; then **Replay & quiz** to practise reading charts with
   the future hidden.
2. **Watch the market** - **Live signals** alerts you when a pattern completes, with the order idea, the risk in
   pounds and an annotated chart. On the **Chart** page, click any triangle for the explanation and track record.
3. **Practise the human side** - take ideas on **Paper trading** with a written plan, then review each trade in the
   **Trade journal**.
4. **Test ideas honestly** - turn an idea into rules in the **Edge builder**; judge it on the out-of-sample part
   and walk-forward, and watch the overfitting check as you tweak.
5. **Understand risk** - run **Monte Carlo** on any result to see realistic drawdowns, losing streaks and the
   chance of hitting your loss limit before you risk money.
"""
    )
    if not ds.empty and (ds["is_sample"] == 1).any() and not (ds["is_sample"] == 0).any():
        st.warning("**You're using synthetic sample data** - random by design, with no real edge. Fine for learning the "
                   "tools; download real data (Data -> Free history) before judging any strategy.")
    st.subheader("What's built")
    for name, desc in STAGES:
        st.markdown(f"✅ **{name}** - {desc}")
    with st.expander("A note on honesty"):
        st.markdown(
            "Most day traders lose money, mainly because costs and randomness are larger than they appear. "
            "EdgeLab is deliberately sceptical: it flags small samples, never calls a pattern profitable without "
            "out-of-sample evidence after costs, counts how many variations you've tried, and shows how wide the "
            "range of outcomes really is.")
