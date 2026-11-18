"""The 'explain this' panel: order idea, visual cues, what the pattern means, and its history on your data."""
from __future__ import annotations

from dataclasses import asdict

import streamlit as st

from edgelab.learning.glossary import GLOSSARY
from edgelab.patterns.base import PATTERNS
from edgelab.ui.charts import signal_figure

ARROW = {"long": "🟢 ▲", "short": "🔴 ▼", "neutral": "⚪"}


def _p(x, d):
    return f"{x:.{d}f}"


def order_box(plan, decimals: int, timeframe: str | None, expiry_minutes: int | None, pos: dict | None, inst) -> None:
    d = decimals
    risk_pips = plan.risk / inst.pip if inst.is_forex else plan.risk
    unit = "pips" if inst.is_forex else ""
    verb = "rises to" if plan.side == "long" else "falls to"
    st.markdown(
        f"**{plan.order_type} {_p(plan.entry, d)}**  \n"
        f"*Only enters if price {verb} {_p(plan.entry, d)} - so the market must move your way first.*  \n"
        f"**Stop-loss:** {_p(plan.stop, d)} ({risk_pips:.1f} {unit} away)  \n"
        f"**Take-profit:** {_p(plan.target, d)} ({plan.target_r:g}R = {plan.target_r * risk_pips:.1f} {unit})  \n"
        + (f"**Cancel if not filled within:** {plan.expiry_bars} bars"
           + (f" ({expiry_minutes} min)" if expiry_minutes else "") + "  \n" if plan.expiry_bars else ""))
    if pos and pos.get("units"):
        ccy = "£" if pos.get("currency") == "GBP" else pos.get("currency", "") + " "
        fits = "✅ fits" if pos.get("fits_rule") else "❌ breaks"
        st.markdown(
            f"**Size:** {pos['units']:,} units ({pos['lots']:.2f} lots)  \n"
            f"**Loss if stopped:** {ccy}{pos['risk']:,.2f} = {pos['risk_pct']:.2f}% of balance (incl. "
            f"{ccy}{pos['costs']:.2f} costs) → {fits} your {pos.get('rule_pct', 0):.2f}% rule  \n"
            f"**Profit at target:** {ccy}{pos['reward']:,.2f} after costs  \n"
            f"**Margin needed:** {ccy}{pos['margin']:,.2f}")
        if not pos.get("fits_rule") and pos.get("units_for_limit"):
            st.caption(f"To keep within your risk rule, use at most {pos['units_for_limit']:,} units for this stop.")


def history_box(stats: dict | None, source: str, is_synthetic: bool) -> None:
    st.markdown("**How this pattern has done on your data**")
    if is_synthetic or (source and "SYNTHETIC" in source):
        st.warning("These numbers come from SYNTHETIC sample data (random by design). They show how the stats work, "
                   "not how the pattern behaves in real markets. Download OANDA data for real history.")
    if not stats or not stats.get("n_filled"):
        st.info("No finished examples of this pattern in the history yet.")
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Past trades (filled)", stats["n_filled"])
    c2.metric("Win rate", f"{stats['win_rate'] * 100:.0f}%" if stats.get("win_rate") is not None else "-")
    c3.metric("Average result", f"{stats['avg_r']:+.2f} R" if stats.get("avg_r") is not None else "-",
              help="Average profit or loss per trade in multiples of the amount risked, after spread and slippage.")
    lo, hi = stats.get("ci_low"), stats.get("ci_high")
    rng = f" 95% range for the average: {lo:+.2f} to {hi:+.2f} R." if lo is not None and hi is not None else ""
    mfe, mae = stats.get("median_mfe"), stats.get("median_mae")
    st.caption(f"{stats['sample_note']}{rng}"
               + (f" Typical best point in a trade: {mfe:+.2f} R; typical worst: {mae:+.2f} R." if mfe is not None else "")
               + (f" Source: {source}." if source else ""))
    verdict = stats.get("verdict", "")
    (st.success if "above zero" in verdict else st.error if "below zero" in verdict else st.info)(verdict)


def render_signal(sig, df, inst, *, tz: str, timeframe: str | None, stats: dict | None, stats_source: str,
                  is_synthetic: bool, position: dict | None, expiry_minutes: int | None = None,
                  outcome=None, allow_reveal: bool = True, key: str = "", monte_carlo: bool = True) -> None:
    info = PATTERNS[sig.pattern]
    st.markdown(f"### {ARROW.get(sig.direction, '')} {sig.title}")
    st.caption(f"Signal bar closed {sig.ts.tz_convert(tz):%a %d %b %H:%M} ({tz}) at {_p(sig.price, inst.decimals)}. "
               "Educational idea from a fixed rule - not advice. EdgeLab never places orders.")
    reveal = False
    if allow_reveal and outcome is not None:
        reveal = st.toggle("Show what happened next", key=f"reveal_{key}",
                           help="Hidden by default so you can decide what you'd do first.")
    left, right = st.columns([3, 2])
    with left:
        after = 30 if reveal else 0
        fig = signal_figure(df, sig, tz, inst.decimals, after=after, height=480)
        st.plotly_chart(fig, config={"displaylogo": False}, key=f"sigfig_{key}")
        st.caption("Purple box/line = the pattern. Grey dashed = key levels. Blue = entry, red = stop-loss "
                   "(red zone = risk), green = take-profit (green zone = reward).")
    with right:
        st.markdown("**The order idea**")
        if sig.plan:
            order_box(sig.plan, inst.decimals, timeframe, expiry_minutes, position, inst)
        st.markdown("**Why it fired (look for these on the chart)**")
        for r in sig.reasons:
            st.markdown(f"- ✅ {r}")
        if sig.cautions:
            st.markdown("**Watch out**")
            for c in sig.cautions:
                st.markdown(f"- ⚠️ {c}")
        if info.confirm:
            st.markdown(f"**Confirms it:** {info.confirm}  \n**Proves it wrong:** {info.invalidate}")
    if reveal and outcome is not None:
        res = {"win": "✅ hit the take-profit", "loss": "❌ hit the stop-loss", "timeout": "⏱ timed out",
               "not filled": "↩️ never triggered (entry not reached - no trade)", "open": "… still open at the end of the data"}
        st.markdown(f"**What happened:** the plan {res.get(outcome.result, outcome.result)}"
                    + (f" for **{outcome.r:+.2f} R** after costs (best point {outcome.mfe_r:+.2f} R, worst "
                       f"{outcome.mae_r:+.2f} R)." if outcome.filled else "."))
    with st.expander(f"About this pattern: {info.name} - what it is and what traders read into it", expanded=False):
        st.markdown(f"**What it is:** {info.what}\n\n**What traders usually infer:** {info.meaning}")
        for t in info.terms:
            if t in GLOSSARY:
                st.markdown(f"*{t}:* {GLOSSARY[t].short()}")
    history_box(stats, stats_source, is_synthetic)
    if monte_carlo and sig.plan and timeframe:
        from edgelab.ui.mc_view import signal_mc_section

        signal_mc_section(sig.pattern, sig.direction, inst.symbol, timeframe, key)


def stats_to_dict(st_obj) -> dict | None:
    if st_obj is None:
        return None
    return st_obj if isinstance(st_obj, dict) else asdict(st_obj)
