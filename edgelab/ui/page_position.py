import pandas as pd
import streamlit as st

from edgelab.instruments import INSTRUMENTS, get_instrument
from edgelab.learning.glossary import help_text
from edgelab.risk import analyse_position, units_for_risk, value_per_price_unit
from edgelab.ui.page_glossary import term_expanders
from edgelab.ui.page_settings import money
from edgelab.ui.state import balance_summary, get_converter, get_settings


def render():
    s = get_settings()
    b = s.broker()
    ccy = s.account_currency
    conv = get_converter()
    balance = balance_summary().balance
    risk_amt = balance * s.risk_pct / 100
    st.title("Position & margin calculator")
    st.caption(f"Current balance {money(balance, ccy)} | risk rule {s.risk_pct:.2f}% = {money(risk_amt, ccy)} "
               f"| broker: **{b.name}** (change in Settings). Calculations only - nothing is sent anywhere.")

    c1, c2, c3 = st.columns(3)
    syms = [k for k, v in INSTRUMENTS.items() if s.show_crypto or v.asset_class == "forex"]
    sym = c1.selectbox("Instrument", syms, index=0)
    inst = get_instrument(sym)
    ref_price = _latest_price(conv, inst)
    price = c2.number_input("Entry price", min_value=0.0, value=float(ref_price), format=f"%.{inst.decimals}f",
                            step=inst.pip * 10 if inst.is_forex else 10.0)
    side = c3.radio("Direction", ["Long (buy)", "Short (sell)"], horizontal=True)

    unit_word = "pips" if inst.is_forex else "price points ($)"
    c1, c2, c3 = st.columns(3)
    stop_in = c1.number_input(f"Stop-loss distance ({unit_word})", min_value=0.0,
                              value=20.0 if inst.is_forex else round(price * 0.01, 0), step=1.0,
                              help=help_text("Position sizing"))
    tgt_in = c2.number_input(f"Target distance ({unit_word})", min_value=0.0,
                             value=40.0 if inst.is_forex else round(price * 0.02, 0), step=1.0,
                             help=help_text("R-multiple"))
    to_price = inst.pip if inst.is_forex else 1.0
    stop_d, tgt_d = stop_in * to_price, tgt_in * to_price

    sizing = s.forex_sizing if inst.is_forex else s.crypto_sizing
    vpu = value_per_price_unit(inst, 1.0, price, conv, ccy)
    if vpu is None:
        st.error(f"No exchange rate from {inst.quote} to {ccy}. Add one under Settings -> Exchange rates.")
        return
    if sizing == "fixed" and inst.is_forex:
        default_units = int(s.forex_fixed_units)
        how = f"fixed size from Settings ({default_units:,} units = {default_units / 100_000:.2f} lots)"
    else:
        default_units = max(units_for_risk(risk_amt, stop_d, vpu, step=b.min_units if inst.is_forex else 1), 0)
        if not inst.is_forex:
            default_units = round(risk_amt / (stop_d * vpu), 4) if stop_d > 0 else 0.0
        how = "risk-based size from your stop"
    units = c3.number_input("Position size (units)" if inst.is_forex else "Position size (coins)", min_value=0.0,
                            value=float(default_units), step=1_000.0 if inst.is_forex else 0.001,
                            format="%.0f" if inst.is_forex else "%.4f", help=help_text("Lot"))
    c3.caption(f"Default: {how}." + (f" Current: {units / 100_000:.2f} lots." if inst.is_forex and units != default_units else ""))
    if units <= 0:
        st.warning("Position size is zero - widen the target or check the stop.")
        return

    other_margin, unreal = 0.0, 0.0
    last = s.oanda_last_summary or {}
    fresh = last and last.get("currency") == ccy and \
        pd.Timestamp.now(tz="UTC") - pd.Timestamp(last["fetched_at"]) < pd.Timedelta(hours=12)
    with st.expander("Positions you already have open (affects free margin)", expanded=bool(fresh and last.get("margin_used"))):
        c1, c2 = st.columns(2)
        other_margin = c1.number_input(f"Margin already in use ({ccy})", min_value=0.0,
                                       value=float(last.get("margin_used", 0.0)) if fresh else 0.0, step=50.0)
        unreal = c2.number_input(f"Unrealised P&L on open positions ({ccy})",
                                 value=float(last.get("unrealized_pl", 0.0)) if fresh else 0.0, step=10.0)
        if fresh:
            st.caption(f"Pre-filled from your OANDA sync at "
                       f"{pd.Timestamp(last['fetched_at']).tz_convert('Europe/London'):%H:%M} UK time.")

    try:
        r = analyse_position(inst, units, price, balance=balance, risk_pct=s.risk_pct, broker=b, conv=conv,
                             ccy=ccy, stop_distance=stop_d, target_distance=tgt_d, other_margin_used=other_margin,
                             unrealised_pnl=unreal)
    except ValueError as e:
        st.error(str(e))
        return

    for w in r.warnings:
        st.warning(w)
    if not r.warnings:
        st.success("This trade fits your risk rule and margin comfortably.")

    st.subheader("Risk and reward")
    m = st.columns(4)
    m[0].metric("Loss if stopped (incl. costs)", money(r.total_risk, ccy))
    m[0].caption(f"{r.risk_pct:.2f}% of your balance")
    m[1].metric("Profit at target (after costs)", money(r.reward, ccy) if r.reward is not None else "-")
    m[2].metric("Reward : risk", f"{r.rr:.2f} : 1" if r.rr is not None else "-")
    m[3].metric("Round-trip costs", money(r.costs.total, ccy))
    if r.cost_in_r is not None:
        m[3].caption(f"= {r.cost_in_r:.2f} R (fraction of the amount risked)")
    m = st.columns(4)
    if inst.is_forex:
        m[0].metric("Pip value", money(r.pip_value, ccy), help=help_text("Pip value"))
    else:
        m[0].metric("Value of a $1 move", money(vpu * units, ccy))
    m[1].metric("Risk limit", money(r.risk_limit, ccy))
    m[1].caption(f"{s.risk_pct:.2f}% of balance")
    mx = r.max_stop_for_limit / to_price if r.max_stop_for_limit is not None else None
    m[2].metric(f"Widest stop for your rule", f"{max(mx, 0):,.1f} {unit_word.split(' ')[0]}" if mx is not None else "-",
                help="At this size, the widest stop that keeps the loss (including costs) within your risk limit.")
    m[3].metric("Size that risks exactly the limit", f"{r.units_for_limit:,} units" if inst.is_forex and r.units_for_limit
                else (f"{risk_amt / (stop_d * vpu + r.costs.total / units):.4f}" if stop_d else "-"))

    st.subheader("Leverage and margin")
    m = st.columns(4)
    m[0].metric("Position value", money(r.notional, ccy), help=help_text("Notional value"))
    m[1].metric("Required margin", money(r.margin_required, ccy), help=help_text("Margin"))
    m[1].caption(f"{r.margin_pct:.2f}% of position value at {r.leverage:.0f}:1")
    m[2].metric("Free margin after opening", money(r.free_margin, ccy), help=help_text("Free margin"))
    m[3].metric("Margin level", f"{r.margin_level_pct:,.0f}%" if r.margin_level_pct else "-",
                help=help_text("Margin level"))
    dist = r.closeout_distance / to_price if r.closeout_distance else None
    st.markdown(
        f"- With **{units:,.0f} units** you control **{money(r.notional, ccy)}** of {inst.base} using "
        f"**{money(r.margin_required, ccy)}** of margin: leverage of {r.notional / r.margin_required:.0f}:1 on the "
        f"position, and {r.notional / balance:.2f}x your whole balance.\n"
        f"- The broker starts closing positions when equity falls to {b.margin_closeout_pct:.0f}% of the margin in "
        f"use: here after a loss of **{money(r.closeout_loss, ccy)}**"
        + (f", i.e. a move of about **{dist:,.0f} {unit_word.split(' ')[0]}** against you." if dist else ".")
        + "\n- Your stop-loss should always be hit long before that. Margin is a deposit, not a cost - but "
          "leverage makes every pip count more.")

    st.subheader("Where the costs come from")
    c = r.costs
    rows = {"Spread": c.spread, "Slippage (both sides)": c.slippage, "Commission": c.commission, "Exchange fees": c.fees}
    st.table(pd.DataFrame({ccy: {k: f"{v:,.2f}" for k, v in rows.items() if v or k == "Spread"}}))
    srcs = conv.used_sources - {"identity", "hint"}
    if "reference" in srcs or "missing" in srcs:
        st.caption(f"Currency conversions used the reference rates from Settings ({conv.describe_reference()}). "
                   "Import your own GBPUSD/USDJPY data for date-exact conversions.")
    term_expanders(["Lot", "Leverage", "Margin", "Free margin", "Margin level", "Pip value", "Spread", "R-multiple"],
                   "What these words mean")


def _latest_price(conv, inst) -> float:
    key = inst.symbol
    if key in conv.series and len(conv.series[key]):
        return float(conv.series[key].iloc[-1])
    if key in conv.reference:
        return float(conv.reference[key])
    return float(inst.typical_price)
