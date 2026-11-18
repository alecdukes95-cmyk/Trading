"""Plain-English glossary. Every term: what it is, why traders care, and the common trap."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Term:
    name: str
    category: str
    what: str
    why: str
    trap: str = ""

    def short(self) -> str:
        return self.what.split(". ")[0].rstrip(".") + "."

    def markdown(self) -> str:
        s = f"**What it is:** {self.what}\n\n**Why traders care:** {self.why}"
        if self.trap:
            s += f"\n\n**Watch out:** {self.trap}"
        return s


_T = [
    # ---- chart basics
    Term("Candlestick", "Chart basics",
         "One bar showing four prices for a period: open, high, low and close (OHLC). The thick 'body' spans "
         "open to close; the thin 'wicks' (shadows) reach the high and low. Green/hollow = closed higher than it "
         "opened; red/filled = closed lower.",
         "The shape tells you who won the period: a big body means one side pushed hard; long wicks mean price "
         "went somewhere and got rejected.",
         "A single candle means little on its own. Context (where it forms, trend, volume) is what matters."),
    Term("OHLC", "Chart basics", "Open, High, Low, Close: the four prices recorded for every bar.",
         "Every indicator and pattern in this app is built from these four numbers plus volume."),
    Term("Wick", "Chart basics",
         "The thin line above or below a candle body, showing prices that traded but did not hold by the close.",
         "A long upper wick = sellers pushed price back down; a long lower wick = buyers pushed it back up.",
         "On low timeframes wicks are often just noise or a single large order."),
    Term("Timeframe", "Chart basics",
         "How much time each bar covers: 5m = five minutes, 15m, 1h, 1d = one trading day.",
         "Higher timeframes show the big picture and key levels; lower timeframes show the detail used for entries. "
         "Many traders find levels on the daily chart and time entries on the 5m.",
         "The same move can look like a trend on 5m and noise on daily. Always check one timeframe up."),
    Term("Bullish / Bearish", "Chart basics", "Bullish = expecting or showing rising prices. Bearish = falling.",
         "Used to describe candles, trends, and bias."),
    Term("Trading day (rollover)", "Chart basics",
         "Forex trades 24 hours, so the 'day' is a convention: it ends at 17:00 New York time. Crypto trades 24/7 "
         "and exchanges usually end the day at 00:00 UTC.",
         "Daily candles, the prior-day high/low and VWAP resets all depend on where the day starts.",
         "Different brokers use different day boundaries, so their daily candles can differ slightly."),
    Term("Session", "Chart basics",
         "Forex activity moves around the world: Asia (Tokyo), then London (from 08:00 UK), then New York "
         "(from 08:00 NY). The London/New York overlap (about 13:00-17:00 UK) is usually the busiest.",
         "Volatility and volume cluster in the London and New York sessions; many setups only work when the "
         "market is active.",
         "Asia-session ranges are often small; breakouts there fail more often."),
    # ---- volume
    Term("Volume", "Volume",
         "How much traded during a bar. For crypto it is the amount of coin exchanged on that exchange.",
         "Volume shows participation. A breakout on rising volume has more 'fuel' than one on thin volume; a "
         "volume spike at the end of a long move can mark exhaustion (a 'climax').",
         "Crypto volume is per exchange (not the whole market) and some exchanges inflate it."),
    Term("Tick volume", "Volume",
         "In forex there is no central exchange, so brokers report the number of price changes (ticks) per bar "
         "instead of traded amount.",
         "Tick volume rises and falls with real activity well enough to spot busy vs quiet periods.",
         "Different brokers give different numbers. Treat forex volume signals as rough evidence."),
    Term("Volume climax", "Volume",
         "An unusually large volume bar (several times normal) often after an extended move.",
         "Can mark capitulation (everyone who wanted out got out) and a turning point, or the start of a "
         "breakout if price holds the new level.",
         "Climaxes are only clear in hindsight; look for confirmation in the next bars."),
    Term("Relative volume", "Volume",
         "Current volume compared with the normal volume for that time of day.",
         "Tells you whether today is a 'something is happening' day or a quiet one."),
    # ---- indicators
    Term("VWAP", "Indicators",
         "Volume-Weighted Average Price: the average price paid today, weighting each price by how much traded "
         "there. It resets at the start of each trading day.",
         "It is the day's 'fair value'. Price above VWAP = buyers have been in control on average today; below = "
         "sellers. Many traders watch for price to 'reclaim' VWAP (cross back above and hold) or be 'rejected' at it.",
         "VWAP is most meaningful early in the day and in liquid markets. Forex VWAP uses tick volume."),
    Term("Moving average", "Indicators",
         "The average closing price over the last N bars. SMA = simple average. EMA = exponential, giving more "
         "weight to recent bars so it reacts faster.",
         "Shows trend direction and acts as dynamic support/resistance. Common: EMA 9/20 for intraday momentum, "
         "SMA 50/200 for the bigger trend.",
         "Moving averages lag. Crossovers whipsaw a lot in sideways markets."),
    Term("ATR", "Indicators",
         "Average True Range: how far price typically moves per bar, averaged over 14 bars. 'True' range "
         "includes gaps from the previous close.",
         "The best tool for sizing stops and targets to current volatility (e.g. stop = 1.5 x ATR) and for judging "
         "whether a move is big or ordinary.",
         "ATR says how much price moves, not which direction."),
    Term("RSI", "Indicators",
         "Relative Strength Index (0-100): compares the size of recent up-moves to down-moves over 14 bars.",
         "Above 70 = strong recent buying ('overbought'), below 30 = strong selling ('oversold'). Divergence "
         "(price makes a new high, RSI doesn't) can warn a move is tiring.",
         "In a strong trend RSI can stay 'overbought' for a long time. It is not a sell signal on its own."),
    Term("MACD", "Indicators",
         "Moving Average Convergence Divergence: the gap between a 12- and 26-period EMA (MACD line), a 9-period "
         "EMA of that line (signal), and the difference (histogram).",
         "Shows momentum: histogram growing = momentum increasing in that direction; crossing zero = trend shift.",
         "Lagging, and noisy on low timeframes."),
    # ---- levels
    Term("Support", "Levels",
         "A price area where falling price has repeatedly stopped and bounced, because buyers stepped in.",
         "Good places to look for long entries, or for a breakdown if price slices through with momentum.",
         "Levels are zones, not exact prices. Stops placed exactly at a level get hit by small overshoots."),
    Term("Resistance", "Levels",
         "A price area where rising price has repeatedly stalled, because sellers appeared.",
         "Targets for longs, or a breakout trigger if price closes above it convincingly.",
         "The more obvious a level is, the more likely it is to see stop-runs around it."),
    Term("Prior-day high / low", "Levels",
         "The highest and lowest prices of the previous trading day (PDH / PDL), plus its close (PDC).",
         "Widely watched by day traders. Breaking above PDH and holding suggests strength; poking above and "
         "falling back inside is a classic 'failed breakout'."),
    Term("Opening range", "Levels",
         "The high and low of the first N minutes (default 30) of the session window (London open for forex).",
         "An 'opening range breakout' (ORB) trades the break of this range. A narrow range often precedes a "
         "bigger move; a wide one may mean the day's range is already partly used up.",
         "Only valid once the range has finished forming. This app hides it until then to avoid look-ahead."),
    Term("Gap", "Levels",
         "A jump between one bar's close and the next bar's open with no trading in between. In forex this mostly "
         "happens over the weekend.",
         "Gaps show new information arrived while the market was closed. Some get 'filled' (price returns to the "
         "prior close), some keep going ('gap and go')."),
    # ---- structure
    Term("Market structure", "Price action",
         "The sequence of swing highs and lows. Uptrend = higher highs (HH) and higher lows (HL). Downtrend = "
         "lower highs (LH) and lower lows (LL). Mixed = range.",
         "The simplest objective definition of trend. A 'break of structure' (e.g. first lower low in an uptrend) "
         "warns the trend may be changing.",
         "Which swings count depends on the timeframe and the swing size you use."),
    Term("Swing high / low", "Price action",
         "A bar whose high (low) is higher (lower) than the bars on either side of it.",
         "Swings mark where one side ran out of steam. They define structure and support/resistance.",
         "A swing can only be confirmed after the following bars have formed. Anything that uses it in real time "
         "must wait for that."),
    Term("Breakout", "Price action",
         "Price moves through a level (resistance, range high, opening range) and keeps going.",
         "Breakouts start trends. Volume and a close beyond the level make them more convincing.",
         "Most breakouts fail. A 'failed breakout' (breaks then closes back inside) is itself a common setup the "
         "other way."),
    Term("Trend day vs range day", "Price action",
         "Trend day: price moves mostly in one direction and closes near an extreme. Range day: price rotates "
         "back and forth and closes near the middle.",
         "Strategies that work on trend days (breakouts, buying pullbacks) lose on range days, and vice versa. "
         "Recognising which kind of day you are in is a core skill."),
    Term("Liquidity", "Price action",
         "How easily you can trade without moving the price. Also used for clusters of orders, such as stop "
         "losses sitting just beyond obvious highs and lows.",
         "Thin liquidity means wider spreads, more slippage and erratic moves. Price is often drawn to areas "
         "with many resting orders.",
         "'Liquidity grab' stories are easy to tell after the fact; test them on data before believing them."),
    # ---- costs
    Term("Pip", "Costs & sizing",
         "The standard unit of forex price movement: 0.0001 for most pairs (EURUSD 1.0850 -> 1.0851), 0.01 for "
         "JPY pairs.",
         "Stops, targets, spreads and P&L are usually discussed in pips. On EURUSD, 1 pip on 10,000 units is about $1."),
    Term("Spread", "Costs & sizing",
         "The difference between the buy (ask) and sell (bid) price. You pay it every time you open a trade.",
         "On small intraday targets the spread can be a large fraction of the profit. A 1-pip spread on a 5-pip "
         "target is 20% of the win.",
         "Spreads widen at the daily rollover, around news, and in quiet hours."),
    Term("Slippage", "Costs & sizing",
         "The difference between the price you wanted and the price you actually got.",
         "Stops and market orders often fill worse than planned, especially in fast markets. Backtests that ignore "
         "it look better than reality."),
    Term("Commission", "Costs & sizing",
         "The broker's or exchange's fee per trade (some forex brokers charge it instead of a wider spread; "
         "crypto exchanges charge a % of trade value).",
         "Costs are charged on every trade, win or lose. High trade frequency multiplies them."),
    Term("Position sizing", "Costs & sizing",
         "Choosing how big each trade is so that hitting your stop loses a fixed amount (e.g. 0.65% of the account).",
         "Size = risk amount / stop distance. This keeps every loss the same size regardless of where the stop is.",
         "Increasing size after losses to 'win it back' is the fastest way to blow up an account."),
    # ---- statistics
    Term("R-multiple", "Statistics",
         "Profit or loss measured in units of what you risked. Risk $65 and make $130 = +2R; lose $65 = -1R.",
         "Lets you compare trades and strategies regardless of position size. A strategy's average R per trade "
         "is its expectancy."),
    Term("Expectancy", "Statistics",
         "The average result per trade: (win rate x average win) - (loss rate x average loss). In R or money.",
         "Positive expectancy after costs is the definition of an edge. Win rate alone tells you nothing: 30% "
         "winners at 3R beats 70% winners at 0.3R.",
         "An expectancy measured on 20 trades is mostly noise."),
    Term("Win rate", "Statistics", "The percentage of trades that made money.",
         "Matters only together with average win and loss size.",
         "High-win-rate strategies often hide rare, very large losses."),
    Term("Profit factor", "Statistics", "Gross profit divided by gross loss. Above 1 = profitable before anything else.",
         "A quick summary: 1.2 is thin, 1.5+ is good, 3+ on many trades is suspicious (check for errors)."),
    Term("Drawdown", "Statistics",
         "The drop from an account's peak to its lowest point before a new peak.",
         "Tells you how painful a strategy is to trade. Most traders abandon strategies during drawdowns that "
         "were statistically normal."),
    Term("Sample size", "Statistics",
         "How many examples (trades, patterns) a statistic is based on.",
         "Small samples produce extreme, unreliable numbers. This app warns below 30 and prefers 100+.",
         "A pattern that 'won 8 of 10 times' could easily be a coin flip."),
    Term("Look-ahead bias", "Statistics",
         "Accidentally using information in a test that would not have been known at the time (e.g. the day's "
         "high before the day is over).",
         "It makes backtests look amazing and then fail live. This app computes every value using only past bars."),
    Term("Overfitting", "Statistics",
         "Tuning a strategy so closely to past data that it captures noise instead of a real effect.",
         "The more variations you try, the more likely one looks great by luck. Out-of-sample tests and walk-forward "
         "tests are the defence."),
    Term("Out-of-sample", "Statistics",
         "Data held back and not used while designing a strategy, then used once to check it.",
         "The only honest estimate of how a strategy might do on data it hasn't seen."),
]

GLOSSARY: dict[str, Term] = {t.name: t for t in _T}
CATEGORIES = list(dict.fromkeys(t.category for t in _T))


def term(name: str) -> Term:
    return GLOSSARY[name]


def help_text(name: str) -> str:
    """Short tooltip text for UI widgets."""
    t = GLOSSARY[name]
    return f"{t.what}\n\nWhy it matters: {t.why}"


def search(query: str) -> list[Term]:
    q = query.strip().lower()
    if not q:
        return list(_T)
    return [t for t in _T if q in t.name.lower() or q in t.what.lower() or q in t.why.lower()]
