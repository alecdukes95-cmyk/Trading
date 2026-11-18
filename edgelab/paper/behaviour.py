"""The human side: pre-trade discipline checks and post-trade behaviour analytics for paper trades."""
from __future__ import annotations

import pandas as pd

EMOTIONS = ["calm", "confident", "anxious", "FOMO (fear of missing out)", "revenge (winning it back)", "bored"]
RISKY_EMOTIONS = {"anxious", "FOMO (fear of missing out)", "revenge (winning it back)", "bored"}
CHECKLIST = [
    "I have a stop-loss at the point where my idea is proven wrong",
    "The loss at my stop fits my risk-per-trade rule",
    "I'm not chasing a move that has already happened",
    "This matches a setup I've studied (not a hunch)",
    "I've checked for big news in the next 30 minutes",
]
MISTAKES = ["moved my stop further away", "exited too early", "held too long", "chased the entry", "too big a size",
            "no stop-loss", "traded out of boredom", "revenge trade", "ignored my plan", "none"]


def pre_trade_checks(*, has_sl: bool, risk_pct: float | None, rule_pct: float, rr: float | None, emotion: str,
                     ticked: list, closed: list, open_count: int, now, units: float,
                     max_trades_per_day: int = 6) -> list[tuple[str, str]]:
    """List of (level, message): level is 'stop' (strong warning), 'warn' or 'ok'."""
    out = []
    if not has_sl:
        out.append(("stop", "No stop-loss. One bad move can wipe out many good trades - decide where you're wrong first."))
    if risk_pct is not None:
        if risk_pct > rule_pct * 1.0001:
            out.append(("stop", f"This risks {risk_pct:.2f}% of the paper balance, above your {rule_pct:.2f}% rule."))
        else:
            out.append(("ok", f"Risk {risk_pct:.2f}% of balance - within your {rule_pct:.2f}% rule."))
    if rr is not None:
        out.append(("ok" if rr >= 1.5 else "warn", f"Reward:risk {rr:.1f}:1" + ("" if rr >= 1.5 else
                                                                              " - below 1.5:1 you need a high win rate.")))
    if emotion in RISKY_EMOTIONS:
        out.append(("warn", f"You marked your state as '{emotion}'. Trades taken in this state are the ones most "
                            "traders regret. Is this really your setup?"))
    missing = [c for c in CHECKLIST if c not in ticked]
    if missing:
        out.append(("warn", f"{len(missing)} of {len(CHECKLIST)} checklist items not ticked"
                            + (f" - e.g. '{missing[0]}'." if missing else ".")))
    now = pd.Timestamp(now)
    recent_loss = [t for t in closed if t.get("pnl") is not None and t["pnl"] < 0 and t.get("close_ts")
                   and now - pd.Timestamp(t["close_ts"]) < pd.Timedelta(minutes=15)]
    if recent_loss:
        out.append(("warn", "You closed a losing trade less than 15 minutes ago. Pause: is this a plan, or an urge "
                            "to win it back?"))
    today = [t for t in closed if t.get("open_ts") and pd.Timestamp(t["open_ts"]).date() == now.date()]
    if len(today) + open_count >= max_trades_per_day:
        out.append(("warn", f"That would be trade number {len(today) + open_count + 1} today. Over-trading is one of "
                            "the most common ways to give back profits."))
    sizes = [t["units"] for t in closed[-20:]]
    if len(sizes) >= 5 and units > 2 * pd.Series(sizes).median():
        out.append(("warn", f"{units:,.0f} units is more than twice your usual size. Size up only by plan, never by feel."))
    return out


def trades_frame(closed: list) -> pd.DataFrame:
    rows = []
    for p in closed:
        plan, rev = p.get("plan") or {}, p.get("review") or {}
        flags = sorted({f for e in p.get("events", []) for f in (e.get("flags") or [])})
        rows.append({"id": p["id"], "symbol": p["symbol"], "side": p["side"], "units": p["units"],
                     "opened": pd.Timestamp(p["open_ts"]), "closed": pd.Timestamp(p["close_ts"]),
                     "entry": p["open_price"], "exit": p["close_price"], "reason": p["close_reason"],
                     "pnl": p["pnl"], "r": p["r"], "best_pips": p.get("best"), "worst_pips": p.get("worst"),
                     "setup": plan.get("setup") or "-", "emotion": plan.get("emotion") or "-",
                     "confidence": plan.get("confidence"), "checklist_done": len(plan.get("checklist", [])),
                     "had_stop": p.get("initial_sl") is not None, "followed_plan": rev.get("followed") or "not reviewed",
                     "grade": rev.get("grade") or "-", "mistakes": ", ".join(rev.get("mistakes", [])) or "-",
                     "flags": ", ".join(flags) or "-"})
    return pd.DataFrame(rows)


def behaviour_insights(df: pd.DataFrame) -> list[str]:
    """Plain-English observations about HOW you trade (needs a handful of trades)."""
    if len(df) < 5:
        return []
    out = []
    no_stop = df[~df["had_stop"]]
    if len(no_stop):
        out.append(f"{len(no_stop)} trade(s) had no stop-loss; together they made {no_stop['pnl'].sum():+,.2f}.")
    widened = df[df["flags"].str.contains("stop widened")]
    if len(widened):
        out.append(f"You moved a stop further away on {len(widened)} trade(s) (average {widened['pnl'].mean():+,.2f}). "
                   "Widening a stop turns a small planned loss into a bigger unplanned one.")
    risky = df[df["emotion"].isin(RISKY_EMOTIONS)]
    calm = df[~df["emotion"].isin(RISKY_EMOTIONS) & (df["emotion"] != "-")]
    if len(risky) >= 3 and len(calm) >= 3:
        out.append(f"Trades in a risky state (FOMO/revenge/anxious/bored) averaged {risky['pnl'].mean():+,.2f}; calm or "
                   f"confident trades averaged {calm['pnl'].mean():+,.2f}.")
    reviewed = df[df["followed_plan"] != "not reviewed"]
    if len(reviewed) >= 5:
        yes, no = reviewed[reviewed["followed_plan"] == "yes"], reviewed[reviewed["followed_plan"] != "yes"]
        if len(yes) and len(no):
            out.append(f"When you followed your plan: average {yes['pnl'].mean():+,.2f} per trade; when you didn't: "
                       f"{no['pnl'].mean():+,.2f}.")
    wins, losses = df[df["pnl"] > 0], df[df["pnl"] < 0]
    if len(wins) >= 3 and len(losses) >= 3:
        aw, al = wins["pnl"].mean(), -losses["pnl"].mean()
        if aw < al:
            out.append(f"Your average win ({aw:,.2f}) is smaller than your average loss ({al:,.2f}). Common causes: "
                       "taking profits early and letting losers run.")
    unrev = (df["followed_plan"] == "not reviewed").sum()
    if unrev >= 3:
        out.append(f"{unrev} closed trades haven't been reviewed yet. The review is where the learning happens.")
    return out
