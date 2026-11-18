# Inputs EdgeLab still needs from you

While building, EdgeLab made the assumptions below so that everything works now. Change any of them in the app; you don't need to edit any code. The **Start here** page has a live checklist of the most important ones.

## 1. Your money and rules

| Assumption | Default used | Where to change it |
|---|---|---|
| Account balance | £10,000 until you enter or sync one | Settings → Account → *Enter balance* or *Sync from OANDA* |
| Which OANDA account to sync | The one with the largest balance | Settings → Account → Sync from OANDA → *Find my OANDA accounts* |
| Account currency | GBP | Settings → Account |
| Risk per trade | 0.65% of the current balance | Settings → Account |
| Position size | Fixed 1 mini lot (10,000 units); crypto (parked) risk-based | Settings → Account |
| Leverage and margin | UK retail: 30:1 majors, 20:1 other pairs, crypto 1:1, close-out at 50% | Settings → Broker. Only change these if you hold a professional account |

## 2. Costs: the most important item to fill in

| Assumption | Default used | Where to change it |
|---|---|---|
| OANDA spreads | **Placeholders**: EURUSD 1.0, GBPUSD 1.3, USDJPY 1.2, EURGBP 1.2, GBPJPY 2.5, others 1.5 pips | Data → OANDA → tick *measure the real spread* → *Use … in my broker profile*, or type them in Settings → Broker |
| Commission | None (assumes OANDA's spread-only pricing) | Settings → Broker (choose per lot, per trade or %) |
| Slippage | 0.2 pips per side | Settings → Broker |

Costs feed every backtest, alert, Monte Carlo run and paper fill. Wrong costs mean wrong conclusions.

## 3. Time and alerts

| Assumption | Default used | Where to change it |
|---|---|---|
| Your trading window | 08:00–17:00 UK time (London open through the London/New York overlap) | Settings → Sessions & time |
| Opening range | First 30 minutes of the window | Settings → Sessions & time |
| Watchlist | EURUSD, GBPUSD, USDJPY, EURGBP, GBPJPY, AUDUSD, USDCAD, EURJPY | Settings → Alerts |
| Alert timeframe and quality | 15m; "strict" (no warning flags); only inside your trading window | Settings → Alerts |
| Trade plan used for alerts and pattern stats | Stop entry; 2R take-profit; cancel after 3 bars | Settings → Alerts |
| Live source | OANDA practice token (MetaTrader 5 also supported) | Settings → Alerts |
| Notifications | Desktop pop-up and browser sound on | Settings → Alerts (use *Send a test desktop notification*) |

## 4. Paper trading and discipline

| Assumption | Default used | Where to change it |
|---|---|---|
| Paper account start | £10,000 (or your balance when you reset it) | Paper trading → ⚙️ Paper account |
| Over-trading warning | From the 6th trade of the day | Fixed for now (tell me if you want it as a setting) |
| Revenge-trading warning | A new trade within 15 minutes of a loss | Fixed for now |

## 5. Monte Carlo and backtests

| Assumption | Default used | Where to change it |
|---|---|---|
| "Ruin" / loss limit | Losing 20% of the starting balance at any point | Each Monte Carlo panel |
| In-sample share | First 70% of each pair's history; the last 30% is kept unseen | Edge builder slider |
| Reference exchange rates (used when your own data doesn't cover a date) | GBPUSD 1.27, EURUSD 1.085, USDJPY 150, EURGBP 0.855, USDCHF 0.88, USDCAD 1.36, AUDUSD 0.66, NZDUSD 0.60 | Settings → Exchange rates, or download GBPUSD/USDJPY data |

## 6. Not tested from the build environment: please confirm on your PC

The cloud environment that built EdgeLab cannot reach these services. Each was tested against simulated data only:

- [ ] **OANDA** live prices, candles and balance sync. You've confirmed the token works; please check the live scanner and paper feed run.
- [ ] **Dukascopy** free history download (Data → Free history).
- [ ] **MetaTrader 5** connection (only if you use MT5).
- [ ] **Windows desktop notifications** (Settings → Alerts → test button).

If anything fails, paste the error (with the file and line shown above it) and it can be fixed quickly.

## 7. Parked or not included (ideas for later)

- **Crypto** is hidden for now (Settings field `show_crypto`); the code is still there.
- **News calendar:** the pre-trade checklist asks you to check news yourself. An economic-calendar feed would need another data source.
- **Alerts for your own Edge-builder strategies:** today the scanner alerts on the 14 built-in patterns. Running saved strategies live is a natural next step.
- **Importing trades straight from OANDA** into the journal (read-only transaction history). You can import a CSV today.
