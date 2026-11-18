"""Running account balance.

current balance = the latest balance figure you entered (or synced from OANDA)
                + deposits - withdrawals + adjustments made after that moment
                + realised P&L of every recorded trade that CLOSED after that moment.

Trades that closed before your balance figure are already included in it, so they are not added again.
Your risk-per-trade amount is always calculated from the current balance.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

BASELINE_KINDS = ("set", "sync")
FLOW_SIGN = {"deposit": 1.0, "withdrawal": -1.0, "adjustment": 1.0}


@dataclass
class BalanceSummary:
    currency: str
    baseline: float
    baseline_time: pd.Timestamp | None
    baseline_kind: str
    flows: float
    realised_pnl: float
    n_trades: int
    n_skipped: int           # trades after the baseline that couldn't be counted (other currency / no P&L)
    balance: float

    def describe(self) -> str:
        when = f"{self.baseline_time:%d %b %Y %H:%M} UTC" if self.baseline_time is not None else "settings"
        src = "synced from OANDA" if self.baseline_kind == "sync" else "entered by you"
        return (f"Balance figure {self.baseline:,.2f} ({src}, {when}) + deposits/withdrawals {self.flows:+,.2f} "
                f"+ P&L of {self.n_trades} closed trades {self.realised_pnl:+,.2f} = {self.balance:,.2f} {self.currency}")


def summarise(events: pd.DataFrame, trades: pd.DataFrame, currency: str, fallback_balance: float) -> BalanceSummary:
    ev = events[events["currency"].fillna(currency) == currency] if not events.empty else events
    base_ev = ev[ev["kind"].isin(BASELINE_KINDS)] if not ev.empty else ev
    if base_ev.empty:
        baseline, b_time, b_kind = float(fallback_balance), None, "settings"
    else:
        last = base_ev.iloc[-1]
        baseline, b_time, b_kind = float(last["amount"]), pd.Timestamp(last["time"]), str(last["kind"])
    flows = 0.0
    if not ev.empty:
        after = ev if b_time is None else ev[(ev["time"] > b_time) | ((ev["time"] == b_time) & (ev["id"] > base_ev.iloc[-1]["id"]))]
        for kind, amt in zip(after["kind"], after["amount"]):
            if kind in FLOW_SIGN:
                flows += FLOW_SIGN[kind] * abs(amt) if kind != "adjustment" else amt
    pnl, n, skipped = 0.0, 0, 0
    if trades is not None and not trades.empty:
        t = trades if b_time is None else trades[trades["exit_time"] > b_time]
        ok = t["pnl"].notna() & (t.get("pnl_currency", pd.Series(currency, index=t.index)).fillna(currency) == currency)
        pnl, n, skipped = float(t.loc[ok, "pnl"].sum()), int(ok.sum()), int((~ok).sum())
    return BalanceSummary(currency, baseline, b_time, b_kind, flows, pnl, n, skipped, baseline + flows + pnl)


def balance_history(events: pd.DataFrame, trades: pd.DataFrame, currency: str, fallback_balance: float) -> pd.Series:
    """Balance after each event and trade since the latest balance figure (for the equity chart)."""
    s = summarise(events, trades, currency, fallback_balance)
    start = s.baseline_time or (trades["exit_time"].min() - pd.Timedelta(seconds=1) if trades is not None and not trades.empty
                                else pd.Timestamp.now(tz="UTC"))
    steps = [(start, 0.0)]
    if not events.empty:
        ev = events[(events["currency"].fillna(currency) == currency) & (events["time"] > start) &
                    events["kind"].isin(list(FLOW_SIGN))]
        steps += [(t, FLOW_SIGN[k] * abs(a) if k != "adjustment" else a) for t, k, a in zip(ev["time"], ev["kind"], ev["amount"])]
    if trades is not None and not trades.empty:
        tr = trades[(trades["exit_time"] > start) & trades["pnl"].notna()]
        if "pnl_currency" in tr:
            tr = tr[tr["pnl_currency"].fillna(currency) == currency]
        steps += list(zip(tr["exit_time"], tr["pnl"]))
    ser = pd.Series([v for _, v in steps], index=pd.DatetimeIndex([t for t, _ in steps])).sort_index()
    return s.baseline + ser.cumsum()
