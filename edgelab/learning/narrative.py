"""'What happened today?' - a plain-English story of one trading day, built from the bars.

Everything here describes the past; it is not a prediction.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from edgelab.data.sessions import session_label
from edgelab.instruments import Instrument


@dataclass
class Story:
    headline: str
    bullets: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)  # glossary terms worth reading


def _t(ts: pd.Timestamp, tz: str) -> str:
    # weekday included because a forex trading day starts the previous evening (17:00 New York)
    return ts.tz_convert(tz).strftime("%a %H:%M")


def session_story(df: pd.DataFrame, day: pd.Timestamp, inst: Instrument, display_tz: str = "UTC",
                  lookback_days: int = 14) -> Story | None:
    """df must contain indicator columns from analysis.add_indicators (intraday)."""
    if "day" not in df:
        return None
    days = pd.DatetimeIndex(df["day"].unique()).sort_values()
    day = pd.Timestamp(day)
    if day not in days:
        return None
    d = df[df["day"] == day]
    if len(d) < 3:
        return None
    pos = days.get_loc(day)
    prev_days = days[max(0, pos - lookback_days):pos]
    daily = df[df["day"].isin(prev_days)].groupby("day").agg(high=("high", "max"), low=("low", "min"),
                                                            close=("close", "last"), volume=("volume", "sum"))
    o, h, l, c = d["open"].iloc[0], d["high"].max(), d["low"].min(), d["close"].iloc[-1]
    rng = h - l
    bullets, terms = [], ["Candlestick", "Trading day (rollover)"]
    counts = df.groupby("day").size()
    typical = counts.loc[prev_days].median() if len(prev_days) else counts.median()
    partial = bool(typical and len(d) < 0.7 * typical)
    if partial:
        bullets.append(f"⚠️ **Incomplete day:** only {len(d)} bars (a full day normally has about {typical:.0f}). The data "
                       "ends part-way through, so the range and volume comparisons below understate the day.")
    fm = lambda x, ref=None: inst.fmt_move(x, ref)  # noqa: E731

    # Net move
    direction = "up" if c > o else "down" if c < o else "flat"
    bullets.append(f"Price opened at **{inst.fmt_price(o)}** and closed at **{inst.fmt_price(c)}**, "
                   f"a move of {fm(c - o, o)} on the day. The full range (high minus low) was {fm(rng, o).lstrip('+')}.")

    # Gap from prior close
    if not daily.empty:
        pc = daily["close"].iloc[-1]
        gap = o - pc
        adr = (daily["high"] - daily["low"]).mean()
        if adr > 0 and abs(gap) > 0.15 * adr:
            word = "up" if gap > 0 else "down"
            bullets.append(f"It **gapped {word}** {fm(gap, pc)} from the previous close. A gap means new information "
                           "arrived while the market was closed (for forex, usually the weekend).")
            terms.append("Gap")
        # Range vs normal
        if adr > 0:
            ratio = rng / adr
            if ratio > 1.4:
                desc = f"a **wide-range day**: {ratio:.1f}x the average daily range of the previous {len(daily)} days. Something moved this market"
            elif ratio < 0.65:
                desc = (f"a **narrow-range day**: only {ratio:.1f}x the average daily range. Quiet days like this "
                        "often come before bigger moves (volatility tends to expand after it contracts)")
            else:
                desc = f"a normal-sized day ({ratio:.1f}x the recent average range)"
            bullets.append(f"This was {desc}.")
            terms.append("ATR")

    # Trend day vs range day
    if rng > 0:
        clv = (c - l) / rng
        body_share = abs(c - o) / rng
        if body_share > 0.6 and (clv > 0.8 or clv < 0.2):
            side = "buyers" if clv > 0.8 else "sellers"
            bullets.append(f"It behaved like a **trend day**: price travelled mostly one way and closed near the "
                           f"{'high' if clv > 0.8 else 'low'} ({clv * 100:.0f}% of the way up the range), so {side} "
                           "were in control into the close.")
        elif body_share < 0.25:
            bullets.append(f"It behaved like a **range (rotational) day**: lots of movement but the close ended near "
                           f"the open ({body_share * 100:.0f}% of the range was net progress). Breakouts tend to fail "
                           "on days like this.")
        else:
            bullets.append(f"Mixed day: price made {body_share * 100:.0f}% net progress relative to its range and "
                           f"closed {clv * 100:.0f}% of the way up the range.")
        terms.append("Trend day vs range day")

    # Time of high and low
    t_hi, t_lo = d["high"].idxmax(), d["low"].idxmin()
    first, second = ("low", "high") if t_lo < t_hi else ("high", "low")
    lab_first, lab_second = session_label([min(t_hi, t_lo), max(t_hi, t_lo)])
    bullets.append(f"The day's {first} came first (at {_t(min(t_hi, t_lo), display_tz)}, {lab_first}) and the "
                   f"{second} later (at {_t(max(t_hi, t_lo), display_tz)}, {lab_second}). Times in {display_tz}.")
    if "vwap" in d:  # how much of the day's movement happened in each session
        labels = pd.Series(session_label(d.index), index=d.index)
        moves = (d["high"] - d["low"]).groupby(labels).sum()
        if moves.sum() > 0:
            top = moves.idxmax()
            bullets.append(f"Most of the day's price action ({moves.max() / moves.sum() * 100:.0f}% of total bar "
                           f"ranges) happened during the **{top}**.")
            terms.append("Session overlap" if "overlap" in top else "Session")

    # VWAP
    if "vwap" in d and d["vwap"].notna().any():
        above = (d["close"] > d["vwap"]).mean()
        crosses = int((np.sign(d["close"] - d["vwap"]).diff().abs() > 0).sum())
        if above > 0.75:
            v = f"price spent **{above * 100:.0f}% of the day above VWAP** - buyers were paying up all day"
        elif above < 0.25:
            v = f"price spent **{(1 - above) * 100:.0f}% of the day below VWAP** - sellers were in control"
        else:
            v = f"price was above VWAP {above * 100:.0f}% of the time and crossed it {crosses} times - a two-sided, balanced day"
        bullets.append(f"Relative to **VWAP** (the day's volume-weighted average price), {v}.")
        terms.append("VWAP")

    # Prior-day levels
    if "pdh" in d and d["pdh"].notna().any():
        pdh, pdl = d["pdh"].iloc[0], d["pdl"].iloc[0]
        notes = []
        if h > pdh:
            notes.append(f"broke above the prior-day high ({inst.fmt_price(pdh)}) and "
                         + ("**held above it** at the close - a successful breakout" if c > pdh else
                            "**closed back below it** - a failed breakout (buyers above the high got trapped)"))
        if l < pdl:
            notes.append(f"broke below the prior-day low ({inst.fmt_price(pdl)}) and "
                         + ("**stayed below** - sellers in control" if c < pdl else
                            "**recovered back above it** - a failed breakdown"))
        if not notes:
            notes.append("stayed inside the previous day's range (an 'inside day') - the market was undecided")
        bullets.append("Price " + "; it also ".join(notes) + ".")
        terms.append("Prior-day high / low")
        if any("failed" in n for n in notes):
            terms.append("Breakout")

    # Opening range
    if "or_high" in d and d["or_high"].notna().any():
        orh, orl = d["or_high"].dropna().iloc[0], d["or_low"].dropna().iloc[0]
        after = d[d["or_high"].notna()]
        up = after.index[after["close"] > orh]
        dn = after.index[after["close"] < orl]
        first_break = min([x for x in (up.min() if len(up) else None, dn.min() if len(dn) else None) if x is not None],
                          default=None)
        if first_break is None:
            bullets.append(f"Price never closed outside the opening range ({inst.fmt_price(orl)}-{inst.fmt_price(orh)}).")
        else:
            way = "above" if len(up) and first_break == up.min() else "below"
            follow = "and the day closed beyond it" if (way == "above" and c > orh) or (way == "below" and c < orl) \
                else "but the day closed back inside or on the other side - the break did not follow through"
            bullets.append(f"The first close outside the opening range was **{way}** it at {_t(first_break, display_tz)}, {follow}.")
        terms.append("Opening range")

    # Volume
    if d["volume"].sum() > 0:
        vmax_t = d["volume"].idxmax()
        rel = d["volume"].max() / max(d["volume"].median(), 1e-12)
        bar = d.loc[vmax_t]
        move = "up" if bar["close"] > bar["open"] else "down"
        bullets.append(f"The busiest bar was at {_t(vmax_t, display_tz)} ({rel:.1f}x the day's median volume) and it "
                       f"moved {move}. Big-volume bars show where most participants acted.")
        if not daily.empty and daily["volume"].mean() > 0:
            rv = d["volume"].sum() / daily["volume"].mean()
            if rv > 1.3 or rv < 0.7:
                bullets.append(f"Total volume was {rv:.1f}x the recent daily average - "
                               + ("an unusually active day." if rv > 1.3 else "a quiet, low-participation day."))
        terms.append("Tick volume" if inst.is_forex else "Volume")

    headline = (f"{day:%A %d %b %Y}: {direction} {fm(abs(c - o), o).lstrip('+')}" if direction != "flat"
                else f"{day:%A %d %b %Y}: flat")
    if partial:
        headline += " (incomplete day)"
    return Story(headline, bullets, list(dict.fromkeys(terms)))
