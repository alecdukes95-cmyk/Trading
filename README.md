# EdgeLab

A local, private app for learning to read day-trading charts, spotting price and volume signals, and testing trading ideas honestly. It covers forex and crypto on daily, 15-minute and 5-minute charts.

> **Educational tool only.** EdgeLab never connects to a broker's order system and cannot place trades. Most day traders lose money. This app tries to show you why, and to stop you from fooling yourself with a backtest.

---

## Install and run (Windows, about 5 minutes the first time)

1. **Install Python** (one time only)
   - Go to <https://www.python.org/downloads/> and download Python 3.12 or newer.
   - Run the installer. On the first screen, **tick "Add python.exe to PATH"**, then click *Install Now*.
2. **Get EdgeLab**
   - On the GitHub page of this repository, click the green **Code** button, then **Download ZIP**.
   - Unzip it somewhere easy, such as `Documents\EdgeLab`.
3. **Start it**
   - Double-click **`run.bat`** in that folder.
   - The first run sets everything up (a few minutes). After that it starts in seconds.
   - Your browser opens at <http://localhost:8501>. Keep the black window open while you use the app, and close it when you're done.

On the very first launch, EdgeLab creates its sample data, which takes 10–20 seconds.

**Mac or Linux:** run `./run.sh` in a terminal instead.

## Learn: replay trainer (Stage 3, in progress)

1. **Start a replay.** Pick a pair and timeframe and press **New replay**. You're dropped at a moment in history with the future hidden. Choose *Random moment* (often the right answer is "no trade") or *Pattern drill* (a bar where one of the app's patterns just completed).
2. **Look and decide.** Step forward with **Next bar** or **+6 bars**, or ask for a **💡 Hint** (what the pattern rules see). Then decide long, short or no trade, with a stop and a target.
3. **Get scored.** The app reveals what happened and scores three things out of 100:
   - your **read**: did the market move 1.5 ATR in your direction first, or go nowhere?
   - the **trade result** in R after costs;
   - your **risk discipline**: stop distance against normal noise, reward:risk, and whether 1 mini lot fits your 0.65% rule.
4. **Track your progress.** **My progress** shows accuracy, average R, a table of your calls against what actually happened, the setups you most often misread, and tips (for example, if you keep trading when the market is going nowhere).

Coming next in Stage 3: quizzes from real charts, and a concept library linked to chart examples.

## Backtests & Monte Carlo

**Backtests & Monte Carlo** replays every past occurrence of every pattern across your pairs. It uses your OANDA data where you've downloaded it, otherwise the clearly flagged synthetic samples.

**What the backtest shows:**

- **Headline numbers:** trades, win rate, average R after costs, total R, profit factor and worst drawdown.
- **Charts:** a cumulative-result curve and a histogram of trade results.
- **Breakdowns:** by pattern, by pair, by session, and in-sample (first 70%) vs out-of-sample (last 30%).
- **An edge check:** a bootstrap 95% range for the average, and the chance that luck alone produced it. The bar is raised for the number of variations you're comparing (the multiple-testing correction).

**🎲 Run Monte Carlo** simulates thousands of possible futures from those trade results. You can choose:

- **Method:**
  - *bootstrap:* future trades drawn at random from past results;
  - *parameter uncertainty:* the win rate and average win/loss also vary within their error margins, which is the most honest view for short histories;
  - *shuffle:* the same trades in random order.
- **Sizing:** 0.65% of current balance per trade, or a fixed 1 mini lot.
- **Horizon, starting balance (your current balance) and loss limit.**

**What it shows:**

- **Odds and outcomes:** the chance of being up, the typical and bad-case (worst 5%) balance, and the chance of hitting your loss limit (risk of ruin).
- **Drawdowns:** typical and bad-case drawdown, plus the longest losing streak.
- **Recovery:** how long it takes to climb back from the worst drop, in trades and trading days.
- **Charts:** an equity fan chart and a drawdown histogram.
- **A sizing table:** your rule against 0.25–2% risk and quarter, half and full Kelly.
- **A reality check:** when the edge isn't statistically proven, it also shows what the same futures look like if the true edge is zero.

The same **🎲 Monte Carlo for this setup** button appears on every alert card and every pattern explanation on the chart. It simulates from that pattern's own history, on that pair or across your whole watchlist. You can also simulate manual stats or your imported trade log.

## Live pattern alerts (Stage 2)

**Live signals** watches your forex pairs and alerts you whenever a pattern completes on a closed candle. Each alert includes:

- **The order idea:**
  - entry as a buy-stop or sell-stop just beyond the signal candle, so price must move your way before you're in;
  - stop-loss where the pattern would be proven wrong;
  - take-profit at 2R by default;
  - cancellation of the entry if it isn't filled within 3 bars.
- **Size and risk in pounds:** 1 mini lot by default, showing the loss if stopped as a % of balance, whether it fits your 0.65% rule, and the margin needed.
- **Why it fired:** an annotated chart marks the pattern, the key levels, the signal candle and the risk/reward zones. A checklist gives the visual cues to look for, such as volume, session, higher-timeframe trend and the level broken, plus warnings.
- **The pattern's history on your data:** sample size, win rate and average R after costs, with a 95% range and a plain verdict. When the data is synthetic or the sample is too small, the card says so.
- **Notifications:** a Windows desktop notification, an in-app pop-up and an optional sound.

**Two ways to run it:**

- **Live (OANDA):** checks every few seconds for newly closed candles on your watchlist and timeframes. It needs your token in `.env`.
- **Simulation:** replays stored data through exactly the same pipeline at high speed, skipping nights and weekends. Use it to see how the alerts behave, with no live connection needed. The detectors only ever see bars up to the simulated "now".

**Patterns detected:**

- engulfing candles and pin bars;
- prior-day high/low breakouts, range (box) breakouts, opening-range breakouts and failed breakouts;
- VWAP reclaims, losses and rejections;
- bull and bear flags;
- break of structure and change of character, with HH/HL/LH/LL swing labels;
- volume climaxes;
- weekly gap-and-go.

**On the Chart page,** triangles mark completed patterns. Click one, or pick it from the list, for the full explanation. **Show what happened next** stays hidden until you choose to reveal it, so you can decide what you'd do first. A scoreboard lists every pattern's track record on the loaded data.

**Choose what alerts you under Settings → Alerts:**

- watchlist and timeframes (15m by default; 5m is noisier);
- which patterns;
- alert quality: *strict* only alerts on signals with no warning flags;
- whether to alert only during your trading window;
- notifications, the R target and order expiry.

**Honesty notes:**

- These are fixed-rule educational ideas, not advice, and EdgeLab never places orders.
- The history stats are **in-sample and descriptive**. They use conservative fills: if a candle touches both the stop and the target, the stop is assumed hit first, and spread and slippage are charged.
- Proper out-of-sample backtests come in Stage 4.
- On the synthetic sample data no pattern shows an edge after costs, as it shouldn't.

## What you can do now (Stage 1)

| Page | What it does |
|---|---|
| **Start here** | Overview, build progress, quick-start steps. |
| **Chart** | Candlestick charts with volume, VWAP, EMA 9/20, SMA 50/200, ATR, RSI, MACD, opening range, prior-day high/low/close, and support/resistance zones. Step through history with **Previous day / Next day**. Under the chart, **What happened today?** explains the day in plain English: trend or range day, gaps, VWAP behaviour, breakouts and failed breakouts of the prior day's levels, opening-range breaks, and volume. Each explanation links to the glossary terms it uses. |
| **Data** | Import a price CSV through a step-by-step wizard. It guesses your columns, then a data check explains any problems in plain English and offers safe fixes. You can also download crypto candles from Coinbase without an API key, and import a trade log with a column-mapping step. |
| **Glossary** | Every term in plain English: what it is, why traders care, and the common trap. |
| **Position & margin** | Shows what a trade means in pounds before you take it. It covers pip value, position value, **required margin** (30:1 on major pairs), **free margin**, margin level, and the loss that would trigger the broker's 50% margin close-out. It also shows the round-trip costs and whether your stop fits the 0.65% risk rule, with the widest stop and largest size that do. |
| **Settings** | Five areas:<br>• **Account:** currency (GBP by default), balance, risk % and sizing (fixed 1 mini lot = 10,000 units, or risk-based).<br>• **Broker:** switch between profiles, or create your own, each holding spreads per pair, commission model, slippage, leverage and margin close-out.<br>• **Exchange rates:** fallback rates for currency conversion.<br>• **Timezone:** UK time by default.<br>• **Trading window:** default 08:00–17:00 UK. |

### The sample data is synthetic

EdgeLab ships with one year of 5-minute data for 10 forex pairs: EURUSD, GBPUSD, USDJPY, USDCHF, USDCAD, AUDUSD, NZDUSD, EURGBP, EURJPY and GBPJPY. EURCHF, GBPCHF, AUDJPY and EURAUD can also be downloaded from OANDA. Crypto is parked for now. It is generated by a random process designed to look like real markets: busy London and New York hours, volatility clusters, trend and range days, and weekend gaps. **It contains no real edge.** Use it to learn the tools, not to judge strategies.

The `sample_data` folder also contains:

- `EURUSD_5m_example_UTC.csv`: a clean CSV to practise importing.
- `EURUSD_M5_messy_broker_time.csv`: a deliberately broken MetaTrader-style export. Import it once with timezone **UTC** and once with **Europe/Athens** to see how the data check catches each problem: duplicates, swapped high/low, a gap, a bad tick, and broker server time.
- `sample_trade_log.csv`: the trade-log format, with illustrative trades.

## Your own data

### Your balance

- **Enter your balance once** under Settings → Account, using the figure your broker shows. From then on EdgeLab keeps it up to date by itself:
  - it adds **deposits, withdrawals and adjustments** you record;
  - it adds the P&L of every recorded trade that **closes after** that moment. Earlier trades are assumed to be in the figure already, so they're never counted twice.
- **Re-anchor whenever you like:** enter a new figure, or press **Sync balance now** to read your balance, equity and margin straight from OANDA.
- **Your 0.65% risk amount always follows the current balance.**

### Free market data: three options

| Option | Cost / account | History | Live candles for the scanner | Notes |
|---|---|---|---|---|
| **Dukascopy** (Data → Free history) | Free, no account, no key | Years of 1-minute data | No (a day appears after it ends) | Swiss bank's bid prices; best for backtests, Monte Carlo and the trainer |
| **MetaTrader 5** (Data → MetaTrader 5) | Free demo account at most UK brokers | Yes (demo 1-minute history may be limited) | Yes | Windows only; keep the MT5 terminal open; set the server timezone |
| **OANDA v20** (Data → OANDA) | Free *practice* account token | Yes | Yes | Also syncs balance and margin. OANDA's separately sold *Exchange Rates API* is the expensive product, and EdgeLab doesn't use it |

You can also import **CSV files** from any platform. That includes the free 1-minute files from HistData.com: choose the timezone "HistData.com files: New York standard time all year".

Choose the scanner's live source under **Settings → Alerts**: OANDA or MetaTrader 5.

### OANDA (optional)

1. In OANDA, go to *My Account → Manage API Access* and generate a personal access token. Practice (demo) accounts have this too.
2. Copy `.env.example` to `.env` in the EdgeLab folder and put the token after `OANDA_API_KEY=`. Restart EdgeLab.
3. Under Settings → Account → Sync from OANDA, pick **practice** or **live** to match the token.

What you can then do:

- **Data → Download forex (OANDA):** download up to years of 5m, 15m, 1h or daily candles. Tick *measure the real spread* to see the typical bid/ask spread in your trading window, then save it to your broker profile with one click.
- **Sync balance now:** reads balance, equity (NAV), margin used and margin available. The Position & margin page uses your open margin automatically.

The OANDA client is **read-only by design**. It can only send GET requests, and a test fails if code that could send orders is ever added. Your token still has trading rights at OANDA, so keep `.env` private.

### Money: GBP account, lots, leverage and margin

- **Everything is in your account currency (GBP by default).**
  - Profits on EURUSD, GBPUSD and BTCUSD are earned in USD and converted at the GBPUSD rate.
  - USDJPY profits are earned in yen and converted yen → USD → GBP.
  - EURGBP profits are already in pounds.
- **Which rate is used.** EdgeLab picks the first of these that's available:
  1. the trade's own price when that is the rate needed (GBPUSD);
  2. your own imported price data for the exact date (never the synthetic samples);
  3. the reference rates in Settings. Any conversion done this way is flagged.
- **UK (FCA) retail rules are built in:**
  - leverage up to **30:1 on major pairs** (made only of GBP, USD, EUR, JPY, CHF and CAD), 20:1 on other pairs;
  - margin **close-out at 50%**;
  - **no crypto derivatives for retail clients**, so crypto is treated as unleveraged spot.
- **Broker profiles.** Pick yours in Settings → Broker, or make a custom one. **The cost figures in the built-in profiles are placeholders, not those brokers' real prices**, so copy your broker's actual spreads and commission in.

### Times and sessions

- **Trading day.** Forex days roll at **17:00 New York** (the industry convention). Crypto days roll at **00:00 UTC**.
- **Session window.** This is the part of the day you trade, and it drives the opening range. The default for both forex and crypto is **08:00–17:00 UK time**. That covers the Asia/London overlap (about 08:00–09:00) and the London/New York overlap (13:00–17:00).
- **Session shading.** The chart can shade the Asia, London and New York sessions. The "What happened today?" panel says which session the day's high, low and most of the movement happened in.
- **Daylight saving.** Session times are computed in each city's own timezone. The weeks when US and UK clocks change on different dates (New York opens at 12:00 UK time instead of 13:00) are therefore handled automatically.
- You can change the window under **Settings → Sessions & time**.

### Importing price data (CSV)

Most charting platforms can export bars, including MetaTrader 4/5, TradingView (paid plans), cTrader, Dukascopy and the crypto exchanges.

- The importer handles headerless MetaTrader files, separate date and time columns, unix timestamps, and day-first or month-first dates.
- Pick the **timezone the file was written in**. MetaTrader brokers usually use "server time" GMT+2/+3; choose `Europe/Athens` for that.
- If you get the timezone wrong, the data check tells you, because the forex weekly open appears in the wrong place.

### API keys

Copy `.env.example` to `.env` and paste keys there. `.env` is ignored by git, so keys stay on your computer and are never written into code. Keys are only used to *download* prices.

## Running the tests

Double-click `run_tests.bat`, or from a terminal: `.venv\Scripts\python -m pytest`.

There are 136 automated checks. They cover:

- **Indicators:** hand-computed values, plus a test proving no indicator uses future bars.
- **Sessions and DST:** trading-day boundaries and daylight-saving handling.
- **CSV parsing:** the different file formats above.
- **The data checker:** every problem type it reports.
- **Storage:** saving and reloading datasets and trades.
- **Money maths:** GBP currency conversions, pip values, margin at UK leverage, round-trip costs and position sizing, each hand-checked.
- **Trade logs:** P&L and R calculations, converted to GBP.
- **Free data sources:** decoding Dukascopy files (zero-based month URLs, either price order, weekend filler removed), the MetaTrader 5 client (server-time conversion, symbol suffixes) and the HistData format.
- **Downloaders and narrative:** the OANDA client (candles, spreads, account summary and the read-only guarantee) and the Coinbase downloader, both offline against fake servers, plus the "what happened today" text.
- **Running balance:** deposits, withdrawals, closed trades, OANDA sync, and no double counting.
- **Patterns:** a test proving no detector uses future bars, plus hand-built examples of each main pattern.
- **Trade plans and outcomes:** the stop-entry and minimum-stop rules, wins, losses, stop-first ties, gaps and costs.
- **Statistics:** the plain-English verdicts.
- **Monte Carlo maths:** exact equity paths, drawdown, recovery and streaks, shuffle conserving the total, parameter uncertainty widening the range, Kelly against its formula, and edge-test verdicts including the multiple-testing correction.
- **Replay scoring:** how the market's next move is labelled (up, down or going nowhere), wins and losses, wrong-side stops, discipline flags, and a full trainer round in the app.
- **Scanner and alerts:** the live scanner (only new candles alert, no duplicates), the simulation, notifications, and the alert page.
- **Every page of the app:** each one renders without errors.

## Project layout

```
app.py                 entry point (streamlit run app.py)
edgelab/
  data/                CSV loading, timestamp parsing, validation, sessions, resampling, SQLite store,
                       sample-data generator, trade-log import, providers/ (Dukascopy, MetaTrader 5, OANDA - all read-only - and Coinbase)
  indicators/          SMA/EMA/RMA, ATR, RSI, MACD, VWAP, opening range, prior-day levels, pivots, S/R
  patterns/            detectors, trade plans, outcome replay, pattern statistics
  backtest/            pattern backtests across pairs (trade tables, in/out-of-sample split)
  montecarlo/          bootstrap / shuffle / parameter-perturbation engine, edge test, Kelly, sizing table
  live/                background scanner (live OANDA / simulation), alerts, desktop notifications
  learning/            glossary, "what happened today" narratives
  ui/                  Streamlit pages and the Plotly chart builder
  analysis.py          adds all indicators to a price frame
  fx.py                currency conversion (hints -> your data -> reference rates)
  risk.py              pip value, notional, margin, costs, position sizing
  brokers.py           broker profiles (costs, leverage, margin close-out)
  account.py           running balance (balance figure + cash flows + closed-trade P&L)
tests/                 pytest suite
sample_data/           example CSVs (regenerate with scripts/make_sample_files.py)
userdata/              YOUR data: edgelab.db (SQLite) and settings.json - created on first run, never committed
```

## Roadmap

1. ✅ Data intake and chart reader
2. ✅ Pattern detection, the "explain this" panel and live alerts
3. 🔜 Learning mode (replay trainer done; quizzes and concept library next): bar-by-bar replay with scoring, quizzes from real charts, a concept library, and progress tracking.
4. Edge builder and backtester: no-code rules and realistic costs, with in-sample/out-of-sample splits, walk-forward testing and overfitting warnings.
5. ✅ Monte Carlo engine (built early, on request): bootstrap resampling, trade shuffling, parameter perturbation, risk of ruin and fractional Kelly.
6. Trade journal with notes and screenshots, feeding results back into the stats.
