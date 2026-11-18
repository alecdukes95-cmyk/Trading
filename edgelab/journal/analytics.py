"""Journal analytics: what your own trades say about your edges and your habits."""
from __future__ import annotations

import numpy as np
import pandas as pd

from edgelab.patterns.base import PATTERNS

SOURCES = {"real": "Imported (real)", "manual": "Logged by hand (real)", "paper": "Paper"}


def source_group(src: str) -> str:
    if src == "paper":
        return "paper"
    if src == "manual":
        return "manual"
    return "real"


def prepare(df: pd.DataFrame, tz: str = "Europe/London") -> pd.DataFrame:
    if df.empty:
        return df
    d = df.copy()
    d["group"] = d["source"].map(source_group)
    local = pd.DatetimeIndex(d["entry_time"]).tz_convert(tz)
    d["weekday"] = local.strftime("%a")
    d["hour"] = local.hour
    d["date"] = pd.DatetimeIndex(d["exit_time"]).tz_convert(tz).normalize().tz_localize(None)
    d["won"] = d["pnl"] > 0
    return d


def summary(d: pd.DataFrame, by: str, explode: bool = False) -> pd.DataFrame:
    if d.empty:
        return pd.DataFrame()
    x = d.explode(by) if explode else d
    x = x[x[by].notna() & (x[by].astype(str) != "")]
    if x.empty:
        return pd.DataFrame()
    g = x.groupby(by)
    out = pd.DataFrame({"Trades": g.size(), "Win rate %": g["won"].mean() * 100, "Avg R": g["r_multiple"].mean(),
                        "Total P&L": g["pnl"].sum(), "Avg P&L": g["pnl"].mean()}).reset_index()
    return out.sort_values("Trades", ascending=False)


def headline(d: pd.DataFrame) -> dict:
    if d.empty:
        return {"trades": 0}
    w, lo = d[d["pnl"] > 0], d[d["pnl"] < 0]
    gl = -lo["pnl"].sum()
    return {"trades": len(d), "win_rate": float(d["won"].mean()), "total": float(d["pnl"].sum()),
            "avg_r": float(d["r_multiple"].mean()) if d["r_multiple"].notna().any() else np.nan,
            "avg_win": float(w["pnl"].mean()) if len(w) else np.nan,
            "avg_loss": float(lo["pnl"].mean()) if len(lo) else np.nan,
            "profit_factor": float(w["pnl"].sum() / gl) if gl > 0 else np.nan,
            "with_r": int(d["r_multiple"].notna().sum())}


def daily_pnl(d: pd.DataFrame) -> pd.Series:
    return d.groupby("date")["pnl"].sum() if not d.empty else pd.Series(dtype=float)


def calendar_grid(daily: pd.Series) -> pd.DataFrame:
    """Weeks (rows) x weekdays (columns) of daily P&L, for a heatmap."""
    if daily.empty:
        return pd.DataFrame()
    idx = pd.DatetimeIndex(daily.index)
    frame = pd.DataFrame({"pnl": daily.to_numpy(), "week": (idx - pd.to_timedelta(idx.weekday, unit="D")).normalize(),
                          "wd": idx.weekday})
    return frame.pivot_table(index="week", columns="wd", values="pnl", aggfunc="sum").reindex(columns=range(7))


def pattern_for_tag(tag: str) -> str | None:
    t = str(tag).lower()
    hits = [(len(p.name), k) for k, p in PATTERNS.items() if p.tradeable and p.name.lower() in t]
    if hits:   # longest match wins: "Opening range breakout" is not a "Range breakout"
        k = max(hits)[1]
        return "bull_flag" if k == "bear_flag" else k
    if "flag" in t:
        return "bull_flag"
    return None


def execution_gap(d: pd.DataFrame, backtest_trades: pd.DataFrame) -> pd.DataFrame:
    """Your average R per pattern-tagged setup vs the same pattern's backtest average R on your data."""
    if d.empty or backtest_trades is None or backtest_trades.empty:
        return pd.DataFrame()
    x = d.explode("tags")
    x = x[x["tags"].notna()]
    x["pattern"] = x["tags"].map(pattern_for_tag)
    x = x[x["pattern"].notna() & x["r_multiple"].notna()]
    if x.empty:
        return pd.DataFrame()
    bt = backtest_trades.copy()
    bt["pattern"] = bt["pattern"].replace({"bear_flag": "bull_flag"})
    rows = []
    for pat, g in x.groupby("pattern"):
        b = bt[bt["pattern"] == pat]
        rows.append({"Setup": PATTERNS[pat].name if pat != "bull_flag" else "Bull / bear flag", "Your trades": len(g),
                     "Your avg R": g["r_multiple"].mean(), "Backtest trades": len(b),
                     "Backtest avg R": b["r"].mean() if len(b) else np.nan})
    out = pd.DataFrame(rows)
    out["Gap (you - rules)"] = out["Your avg R"] - out["Backtest avg R"]
    return out


def lessons(d: pd.DataFrame) -> list[str]:
    """Plain-English observations; needs a handful of journalled trades."""
    out = []
    if len(d) < 5:
        return out
    m = summary(d, "mistakes", explode=True)
    m = m[m["mistakes"] != "none"] if not m.empty else m
    if not m.empty:
        worst = m.sort_values("Total P&L").iloc[0]
        if worst["Total P&L"] < 0:
            out.append(f"Your costliest mistake: **{worst['mistakes']}** - {int(worst['Trades'])} trades, "
                       f"{worst['Total P&L']:+,.2f} in total.")
    f = summary(d, "followed")
    if not f.empty and {"yes"} <= set(f["followed"]) and len(set(f["followed"]) - {"yes"}):
        yes = f[f["followed"] == "yes"]["Avg P&L"].iloc[0]
        other = d[d["followed"].isin(["partly", "no"])]["pnl"].mean()
        out.append(f"Following your plan: {yes:+,.2f} per trade on average; not following it: {other:+,.2f}.")
    wd = summary(d, "weekday")
    if len(wd) >= 3:
        b = wd.sort_values("Avg P&L").iloc[0]
        if b["Trades"] >= 3 and b["Avg P&L"] < 0:
            out.append(f"{b['weekday']} is your weakest day ({int(b['Trades'])} trades, {b['Avg P&L']:+,.2f} average).")
    no_stop = d[d["stop_price"].isna()]
    if len(no_stop) >= 2:
        out.append(f"{len(no_stop)} trades have no stop recorded, so their risk (and R) is unknown.")
    unrev = d[d["followed"].isna() | (d["followed"] == "")]
    if len(unrev) >= 3:
        out.append(f"{len(unrev)} trades have no review yet - the review is where improvement comes from.")
    return out
