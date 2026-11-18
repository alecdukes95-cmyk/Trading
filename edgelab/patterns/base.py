"""Core types for pattern detection: Signal, TradePlan and the plain-English pattern library.

Rules every detector follows:
* A signal at bar i uses only bars 0..i (bar i must be COMPLETE). Nothing from the future.
* Swing points are only used once they are confirmed (`right` bars after the swing).
* The trade plan's entry is a STOP order beyond the signal bar, so price must move in the
  expected direction before you are in. Entries are never at prices already in the past.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class TradePlan:
    side: str               # "long" or "short"
    order_type: str         # "BUY STOP" / "SELL STOP"
    entry: float
    stop: float
    target: float
    target_r: float
    expiry_bars: int

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)


@dataclass
class Signal:
    pattern: str                         # key in PATTERNS
    direction: str                       # "long", "short" or "neutral"
    i: int                               # position of the (completed) signal bar
    ts: pd.Timestamp                     # open time of the signal bar
    price: float                         # close of the signal bar
    stop_ref: float | None               # where the pattern is proven wrong (before buffer)
    entry_ref: float | None = None       # price to break for entry (default: signal-bar high/low)
    levels: dict = field(default_factory=dict)    # label -> price (lines drawn on the chart)
    zone: tuple | None = None                     # (i0, i1, low, high) shaded area, e.g. the flag
    points: list = field(default_factory=list)    # [(i, price, label)] extra markers
    reasons: list = field(default_factory=list)   # visual cues: why this fired
    cautions: list = field(default_factory=list)  # what weakens it
    plan: TradePlan | None = None

    @property
    def info(self) -> "PatternInfo":
        return PATTERNS[self.pattern]

    @property
    def title(self) -> str:
        arrow = {"long": "Bullish", "short": "Bearish"}.get(self.direction, "")
        name = self.info.name
        if not arrow or name.lower().startswith(("bull", "bear")):
            return name
        keep = len(name) > 1 and name[1].isupper()   # acronyms such as VWAP stay upper-case
        return f"{arrow} {name if keep else name[0].lower() + name[1:]}"


@dataclass(frozen=True)
class PatternInfo:
    key: str
    name: str
    code: str              # short label drawn on the chart
    family: str
    what: str              # what the move is
    meaning: str           # what traders usually infer
    confirm: str           # what would confirm it
    invalidate: str        # what would prove it wrong
    terms: tuple = ()      # glossary terms
    tradeable: bool = True


PATTERNS: dict[str, PatternInfo] = {p.key: p for p in [
    PatternInfo("engulfing", "Engulfing candle", "ENG", "Candles",
                "A candle whose body completely covers the previous candle's body, in the opposite colour, after a "
                "move the other way.",
                "One side took control decisively within a single bar: the previous bar's buyers (or sellers) are "
                "now all underwater. Often read as a short-term reversal, especially at a support/resistance level.",
                "The next bar breaks the engulfing candle's high (bullish) or low (bearish), ideally with rising volume.",
                "Price trades back beyond the far end of the engulfing candle.", ("Candlestick", "Support")),
    PatternInfo("pin_bar", "Pin bar (rejection candle)", "PIN", "Candles",
                "A candle with a long wick on one side (at least 60% of its range) and a small body at the other end, "
                "that pokes to a new short-term extreme.",
                "Price tried to go further, was rejected, and closed back where it started - a sign the push ran out "
                "of willing participants. A long lower wick = buyers rejected lower prices.",
                "A break of the pin bar's body end in the direction of the rejection.",
                "Price trades beyond the tip of the wick: the rejection failed.", ("Wick", "Candlestick")),
    PatternInfo("breakout_pd", "Prior-day level breakout", "BO", "Levels",
                "A close beyond yesterday's high (or low) - levels many day traders watch.",
                "Breaking the previous day's range suggests the market is accepting new prices and may extend; "
                "stops resting beyond the level often add fuel.",
                "Price holds beyond the level over the next bars rather than slipping back inside.",
                "A close back inside the prior day's range (that becomes a failed breakout).",
                ("Prior-day high / low", "Breakout")),
    PatternInfo("range_breakout", "Range breakout", "RB", "Levels",
                "After a tight, sideways period (a 'box'), a close beyond the top or bottom of the box.",
                "Volatility tends to expand after it contracts. The break shows which side won the balance; the box "
                "edge often becomes support/resistance.",
                "Follow-through in the breakout direction with above-average volume.",
                "A close back inside the box.", ("Breakout", "Support")),
    PatternInfo("orb", "Opening range breakout", "ORB", "Levels",
                "The first close beyond the high or low of the opening range (default: first 30 minutes of your "
                "trading window, i.e. from 08:00 UK).",
                "Early London trading sets the day's initial balance. Breaking it can signal the direction for the "
                "session.",
                "Price stays beyond the range and moves away from it.",
                "Price closes back inside the opening range.", ("Opening range", "Breakout")),
    PatternInfo("failed_breakout", "Failed breakout", "FBO", "Levels",
                "Price closed beyond a key level (prior-day high/low, opening range or box) but within a few bars "
                "closed back inside.",
                "Traders who bought the breakout (or sold the breakdown) are now trapped; their exits can push price "
                "the other way. One of the most reliable-feeling, and most over-traded, setups.",
                "Price continues back through the range towards the other side.",
                "Price closes beyond the level again.", ("Breakout", "Liquidity")),
    PatternInfo("vwap_reclaim", "VWAP reclaim / loss", "VWAP", "VWAP",
                "After spending several bars below VWAP, price closes back above it (reclaim) - or the reverse (loss).",
                "The average participant today has gone from losing to winning (or vice versa). Intraday traders "
                "often treat VWAP as the line between buyer and seller control.",
                "Price holds on the new side of VWAP and VWAP itself starts to turn.",
                "A quick close back across VWAP.", ("VWAP",)),
    PatternInfo("vwap_rejection", "VWAP rejection", "VWAPr", "VWAP",
                "Price rallies up to VWAP from below (or drops to it from above), touches it, and closes away with a wick.",
                "VWAP acted as resistance (or support): the side in control today defended the average price.",
                "The next bar breaks the rejection candle's low (or high).",
                "A close through VWAP.", ("VWAP", "Wick")),
    PatternInfo("bull_flag", "Bull flag", "FLAG", "Continuation",
                "A sharp rise (the 'pole'), followed by a small, orderly pullback or sideways drift (the 'flag'), "
                "then a close above the flag.",
                "Buyers paused rather than gave up: the pullback was shallow and quiet. A break of the flag suggests "
                "the move may continue, sometimes by a distance similar to the pole.",
                "A strong close above the flag with volume picking up.",
                "Price falls below the bottom of the flag.", ("Breakout", "Market structure")),
    PatternInfo("bear_flag", "Bear flag", "FLAG", "Continuation",
                "A sharp fall (the 'pole'), then a small, orderly bounce (the 'flag'), then a close below the flag.",
                "Sellers paused rather than gave up. A break of the flag suggests the decline may continue.",
                "A strong close below the flag with volume picking up.",
                "Price rises above the top of the flag.", ("Breakout", "Market structure")),
    PatternInfo("bos", "Break of structure (trend continuation)", "BOS", "Structure",
                "In an uptrend (higher highs and higher lows), price closes above the most recent swing high - or in a "
                "downtrend, below the most recent swing low.",
                "The trend has made another leg; trend-followers treat this as confirmation the trend is intact.",
                "The next pullback holds above the last higher low (uptrend) or below the last lower high (downtrend).",
                "Price closes beyond the last swing on the other side (the structure breaks).",
                ("Market structure", "Swing high / low")),
    PatternInfo("choch", "Change of character (possible reversal)", "CHoCH", "Structure",
                "In a downtrend, price closes above the most recent lower high - or in an uptrend, below the most "
                "recent higher low. The first crack in the trend's structure.",
                "Can be the first sign a trend is ending. On its own it is weak evidence; many changes of character "
                "fail and the old trend resumes.",
                "A new higher low forms (after a downtrend) / lower high (after an uptrend).",
                "Price makes a new extreme in the old trend direction.", ("Market structure", "Swing high / low")),
    PatternInfo("volume_climax", "Volume climax (possible exhaustion)", "CLX", "Volume",
                "An unusually large-volume, wide-range bar (3x+ normal volume) at the end of an extended move.",
                "Everyone who wanted to buy (or sell) may have just done so. Climaxes often mark at least a pause, "
                "sometimes a reversal - but sometimes the start of a bigger move.",
                "The next bars fail to extend the move and break the climax bar's opposite end.",
                "Price keeps going in the climax direction.", ("Volume climax", "Tick volume")),
    PatternInfo("gap_and_go", "Gap and go", "GAP", "Gaps",
                "The market opens the week with a gap, and the first half hour keeps moving in the gap's direction "
                "without filling it.",
                "News over the weekend repriced the market and early trading accepted the new price.",
                "Price extends beyond the early high (gap up) or low (gap down).",
                "Price fills the gap back to Friday's close.", ("Gap",)),
    PatternInfo("swing", "Swing point (HH / HL / LH / LL)", "", "Structure",
                "A confirmed swing high or low, labelled against the previous one: higher high (HH), higher low "
                "(HL), lower high (LH), lower low (LL).",
                "HH + HL = uptrend, LH + LL = downtrend, mixed = range.",
                "", "", ("Market structure", "Swing high / low"), tradeable=False),
]}

TRADEABLE = [k for k, v in PATTERNS.items() if v.tradeable]
