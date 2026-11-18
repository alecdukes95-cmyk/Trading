"""Concept library: the ideas behind trading, each linked to a live example on your own charts or a calculator."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Concept:
    key: str
    title: str
    body: str
    example: str          # which example the UI shows (see ui/page_concepts.py)
    terms: tuple = ()     # glossary terms used for the 'test yourself' questions
    quiz_topic: str | None = None   # pattern key for chart questions, if any


CONCEPTS = [
    Concept("structure", "Market structure: trends are a staircase",
            "Price rarely moves in a straight line. It swings: up-move, pullback, up-move. Joining the turning points "
            "(swing highs and lows) gives a staircase. **Higher highs and higher lows** (HH, HL) = uptrend. **Lower "
            "highs and lower lows** (LH, LL) = downtrend. Anything else = range.\n\n"
            "Why it matters: it is the simplest objective definition of trend, and it tells you where a trade idea is "
            "proven wrong. In an uptrend, a break below the last higher low breaks the staircase.\n\n"
            "Two signals come from it: a **break of structure** (the trend makes another step) and a **change of "
            "character** (the first step the wrong way - a possible, but often false, reversal).",
            "structure", ("Market structure", "Swing high / low"), "bos"),
    Concept("sr", "Support and resistance: where decisions were made",
            "A level where price repeatedly turned is a place many participants acted. Some will act there again: "
            "buyers who missed it last time, sellers defending a position, stops resting just beyond it.\n\n"
            "Treat levels as **zones**, not lines. The more touches and the more recent, the more it matters. A clean "
            "break with a close beyond the zone often turns support into resistance (and vice versa).",
            "sr", ("Support", "Resistance"), "range_breakout"),
    Concept("trend_range", "Trend days vs range days",
            "Most days are one of two kinds. **Trend days** travel one way and close near an extreme; buying pullbacks "
            "and breakouts works. **Range days** rotate around the middle; fading the edges works and breakouts fail.\n\n"
            "Clues early in the day: a narrow opening range breaking with volume (trend), or price crossing VWAP again "
            "and again (range). Using the wrong playbook for the day type is a classic way to lose.",
            "trend_range", ("Trend day vs range day",)),
    Concept("vwap", "VWAP: the day's fair price",
            "VWAP is the average price paid today, weighted by volume. If price is above it, the average buyer today is "
            "in profit and tends to defend; below it, sellers are in control.\n\n"
            "A **reclaim** (back above after a spell below) or **loss** (the reverse) shows control changing hands. A "
            "**rejection** (touch and turn away) shows the side in control defending the average. In forex VWAP uses "
            "tick volume, so treat it as a guide.",
            "vwap", ("VWAP",), "vwap_reclaim"),
    Concept("orb", "The opening range and the London open",
            "The first 30 minutes after 08:00 UK set the London session's initial balance. A break out of that range, "
            "especially with rising volume, can set the direction for the morning. A break that quickly fails "
            "back inside traps traders and often runs the other way.",
            "orb", ("Opening range", "Session"), "orb"),
    Concept("sessions", "Sessions and overlaps: when the market is awake",
            "Forex volume follows the business day: Asia, then London (08:00 UK), then New York (13:00 UK). The "
            "London/New York overlap (13:00-17:00 UK) is usually the most active part of the day; Asia-only hours "
            "are quiet, spreads wider and breakouts weaker.",
            "sessions", ("Session", "Session overlap")),
    Concept("liquidity", "Liquidity and trapped traders",
            "Obvious highs and lows collect resting orders: stop-losses of those on the wrong side and breakout entries "
            "of those waiting. When price pokes through and comes straight back, those traders are trapped and their "
            "exits push price the other way - the logic behind the **failed breakout**.\n\n"
            "Be sceptical of stories told after the fact; the backtest page tells you whether it actually paid on your data.",
            "liquidity", ("Liquidity", "Breakout"), "failed_breakout"),
    Concept("volume", "Volume: participation behind a move",
            "A move on rising volume has participants behind it; a move on thin volume is easier to reverse. An "
            "unusually large volume bar at the end of a long move (a **climax**) can mark exhaustion - or the start of "
            "something bigger. Forex volume is tick volume (number of price changes), a rough but useful proxy.",
            "volume", ("Volume", "Tick volume", "Volume climax"), "volume_climax"),
    Concept("rr", "Risk:reward and expectancy",
            "Every trade has a planned loss (entry to stop = 1R) and a planned gain (entry to target, e.g. 2R). Your "
            "long-run result per trade is the **expectancy**: win rate x average win - loss rate x average loss.\n\n"
            "A 35% win rate with 2R winners has positive expectancy (0.35 x 2 - 0.65 x 1 = +0.05R) before costs. A 70% "
            "win rate with 0.3R winners loses money. Win rate alone means nothing.",
            "expectancy", ("R-multiple", "Expectancy", "Win rate")),
    Concept("sizing", "Position sizing: let the stop set the size",
            "Decide where you are wrong (the stop) first, then size the position so that being wrong costs a fixed "
            "share of the account - your 0.65%. Size = risk amount / (stop distance x pip value).\n\n"
            "A fixed 1 mini lot means your risk changes with every stop; that's fine as long as you check it fits your "
            "rule every time.",
            "sizing", ("Position sizing", "Pip value", "Lot")),
    Concept("costs", "Costs: the silent tax",
            "You pay the spread on every trade, plus slippage on stops and commission. On a 5-pip stop a 1-pip spread is "
            "20% of your risk before the trade even starts. Small stops and small targets need a much higher win rate "
            "to survive costs.",
            "costs", ("Spread", "Slippage", "Commission")),
    Concept("drawdown", "Drawdowns and losing streaks are normal",
            "Even a genuinely profitable strategy goes through long losing streaks and deep drawdowns. With a 40% win "
            "rate, 8-10 losses in a row will happen. Traders usually abandon strategies during perfectly normal "
            "drawdowns - or worse, increase size to win it back. Monte Carlo shows you what 'normal' looks like before "
            "you live through it.",
            "drawdown", ("Drawdown", "Position sizing")),
    Concept("overfitting", "Overfitting: how backtests fool you",
            "Try enough variations of a rule and one will look brilliant by luck. The more you tune, the better the past "
            "looks and the worse the future does. Defences: keep data aside (out-of-sample), test on it once, use "
            "walk-forward testing, prefer simple rules, and count how many things you tried.",
            "overfitting", ("Overfitting", "Out-of-sample", "Sample size", "Look-ahead bias")),
]
BY_KEY = {c.key: c for c in CONCEPTS}
