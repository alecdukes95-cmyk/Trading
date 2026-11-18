# EdgeLab

A private app that runs on your own computer, for learning to trade forex. It helps you:

- **read charts**;
- **spot price and volume signals** (with live alerts);
- **define and test trading edges honestly**;
- **run Monte Carlo analysis** on them;
- **practise with paper trades**;
- **keep a trade journal**.

It is built for a UK, GBP account and runs in your web browser.

> **Educational tool only.** EdgeLab never sends orders to a broker, not even a practice account. Most day traders lose money. The app is built to show you why, and to stop you fooling yourself with a backtest.

---

## Install and run (Windows)

1. **Install Python** (one time only). Download Python 3.12 or newer from <https://www.python.org/downloads/>. On the first installer screen, **tick "Add python.exe to PATH"**.
2. **Get EdgeLab.** On GitHub, open this branch, click **Code → Download ZIP**, and unzip it, for example into `Documents\EdgeLab`. To update later, download again and copy your `userdata` folder and `.env` file across.
3. **Start it.** Double-click **`run.bat`**. The first run sets everything up, which takes a few minutes. Your browser then opens at <http://localhost:8501>. Keep the black window open while you use the app (the live scanner and paper trading only run while it's open).

On Mac or Linux, run `./run.sh` instead. To run the automated checks, double-click `run_tests.bat`.

**First things to do:** open **Start here** and follow the setup checklist. The assumptions the app made until you set things up are listed in **[USER_INPUT_NEEDED.md](USER_INPUT_NEEDED.md)**.

---

## What's in the app

The sidebar is grouped into four sections.

### Trade

| Page | What it does |
|---|---|
| 🔔 **Live signals** | Watches your pairs and alerts you when a pattern completes on a closed candle. Each alert gives the order idea (buy/sell stop, stop-loss, take-profit, expiry), the size and risk in £ against your 0.65% rule, the margin needed, and an annotated chart showing *why* it fired. It also shows the pattern's history on your data. Alerts arrive as desktop and in-app notifications, with buttons for **🎲 Monte Carlo** and **🧾 Paper-trade this idea**. A **simulation** mode replays history so you can try it any time. |
| 🧾 **Paper trading** | Place fake trades against **live OANDA bid/ask prices**, or a replay at weekends. It fills like a broker: you pay the spread, stops that price jumps over fill worse, and margin and the 50% close-out apply. Before each trade you record a plan (setup, reason, confidence, how you feel, checklist). You get warnings for no stop, breaking your risk rule, revenge trading and over-trading. You can then manage, close and review each trade. |
| 📒 **Trade journal** | Every trade in one place: imported, logged by hand, and paper. Add tags, emotion, plan followed, a decision grade, mistakes, lessons and **screenshots**. Insights include your costliest mistake, plan vs no plan, a P&L calendar and breakdowns by tag, time and pair. The **execution gap** compares your results with the rules' backtest. Monte Carlo runs on your own trades. |
| 🧮 **Position & margin** | What a trade means in £ before you take it: pip value, position value, required and free margin, margin level, close-out distance, costs, and the widest stop and largest size that fit your rule. |

### Analyse

| Page | What it does |
|---|---|
| 📈 **Chart** | Candles with VWAP, moving averages, ATR, RSI, MACD, opening range, prior-day levels, support/resistance, session shading and HH/HL/LH/LL structure. **Click any pattern triangle** for its explanation, the order idea and its history (with "show what happened next" hidden until you choose). Also includes a "What happened today?" narrative and day-by-day stepping. |
| 🛠️ **Edge builder** | Turn an idea into rules without coding. Rules cover:<br>• **triggers:** patterns, line crosses, EMA crosses, RSI, breakouts;<br>• **filters:** UK hours, weekdays, sessions, trend, volume, volatility, RSI, daily cap;<br>• **entry, stop, target and exits.**<br>Every test reports in-sample vs **out-of-sample** results, with a verdict and an **overfitting check** against how many variations you've tried. You also get **walk-forward** testing, a **robustness grid**, breakdowns by hour, session and regime, and Monte Carlo. |
| 🎲 **Backtests & Monte Carlo** | Every built-in pattern replayed across your pairs, with an edge check and a split by time. **Monte Carlo** methods: bootstrap, parameter uncertainty and shuffle. It shows the chance of profit, ending-balance range, risk of hitting your loss limit, drawdowns, losing streaks, recovery time, and Kelly and risk-size comparisons. A zero-edge reality check appears whenever the edge isn't proven. |

### Learn

| Page | What it does |
|---|---|
| 🎓 **Replay & quiz** | **Replay:** you're dropped into history with the future hidden. Step forward bar by bar, decide long, short or no trade with a stop and target, then reveal what happened. You're scored on your read, the result and your risk discipline. **Quiz:** questions built from real charts ("spot the setup", "what happened next", "place the stop") plus concept questions. Progress shows your weakest topics, with one-click drills. |
| 💡 **Concepts library** | 13 core ideas, each with a live example from your charts or a calculator: structure, support/resistance, trend vs range days, VWAP, opening range, sessions, liquidity, volume, expectancy, position sizing, costs, drawdowns and overfitting. |
| 📖 **Glossary** | Every term in plain English, plus the pattern library. |

### Setup

| Page | What it does |
|---|---|
| 📥 **Data** | Free price data (see below), CSV import with a plain-English data check, and trade-log import. |
| ⚙️ **Settings** | Account balance (it updates itself from trades and deposits, or syncs from OANDA), risk and sizing, alerts, broker profile (spreads, commission, leverage), exchange rates, sessions and timezone. |

---

## Free market data

| Option | Needs | History | Live (scanner, paper) |
|---|---|---|---|
| **Dukascopy** (Data → Free history) | Nothing | Years of 1-minute data | No |
| **OANDA v20** (Data → OANDA) | A free **practice** account token | Yes | **Yes**, and balance sync |
| **MetaTrader 5** (Data → MetaTrader 5) | Any broker's free MT5 demo, on Windows | Yes | **Yes** |
| **CSV import** | An export from any platform, including the free HistData.com files | Yes | No |

**Connecting OANDA:** in OANDA, go to *My Account → Manage API Access → generate a token*. Copy `.env.example` to `.env`, paste the token after `OANDA_API_KEY=`, and restart EdgeLab. The OANDA client is **read-only by design** (GET requests only, enforced by a test), but the token itself can trade, so keep `.env` private.

EdgeLab ships with **synthetic sample data** for 10 pairs, so everything works on day one. It is random by design and has **no real edge**. Use it to learn the tools, not to judge strategies.

---

## How EdgeLab keeps you honest

- **No look-ahead.**
  - Indicators, patterns and strategy rules only use completed bars; tests prove it.
  - Entries happen after the signal bar closes.
  - The chart, replay and quiz never show the future until you ask.
- **Conservative fills.** A bar that touches both the stop and the target counts as a loss. A gap through a stop fills worse. Spread, slippage and commission are always charged.
- **Out-of-sample first.** Strategies are judged on data that wasn't used to design them. Walk-forward picks settings using only the past.
- **Multiple testing is counted.** Each variation you test raises the bar that luck alone could reach.
- **Small samples are flagged.** Verdicts are bootstrap-based, and they say "not distinguishable from zero" whenever that's the truth.
- **Your real balance is never touched by paper trades.**

---

## Your data and privacy

Everything is stored locally:

- the `userdata/` folder: `edgelab.db` (SQLite: prices, trades, journal, alerts, strategies, progress), `settings.json` and `screenshots/`;
- the `.env` file, for API keys.

Neither is ever committed to git.

## Project layout (for the curious)

```
app.py                 entry point (streamlit run app.py)
edgelab/
  data/                loading, validation, sessions, resampling, SQLite store, sample data, trade logs,
                       providers/ (Dukascopy, MetaTrader 5, OANDA - read-only - and Coinbase)
  indicators/          SMA/EMA/RMA, ATR, RSI, MACD, VWAP, opening range, prior-day levels, pivots, S/R
  patterns/            detectors, trade plans, outcome replay, pattern statistics
  backtest/            pattern backtests; no-code strategy rules + engine (IS/OOS, walk-forward, grid)
  montecarlo/          bootstrap / shuffle / parameter-uncertainty engine, edge test, Kelly, sizing table
  live/                background scanner, alerts, desktop notifications
  paper/               paper-trading engine, feeds (live quotes / replay), discipline checks
  journal/             journal analytics
  learning/            glossary, narratives, replay scoring, quizzes, concepts
  fx.py, risk.py, brokers.py, account.py   currency conversion, sizing and margin, broker profiles, balance
  ui/                  Streamlit pages and charts
tests/                 176 automated checks (run_tests.bat)
sample_data/           example CSV files
```

## Automated checks

176 tests cover:

- **Calculations:** every indicator (hand-computed), sessions and daylight saving, the CSV formats, the data checker, and GBP money maths (pip value, margin, costs, sizing).
- **No look-ahead:** for indicators, patterns and strategy rules.
- **Simulations:** pattern trade plans and outcomes, the Monte Carlo maths, and backtest accounting (costs, breakeven, no overlapping trades, walk-forward, grid).
- **Paper and live:** the paper-trading engine (bid/ask fills, gaps, margin, close-out), the alerts and scanner, and the free-data decoders against simulated servers.
- **Learning tools:** replay scoring, quizzes, concepts and journal analytics.
- **The app itself:** every page opens and works, including the regressions for the two bugs you reported.
