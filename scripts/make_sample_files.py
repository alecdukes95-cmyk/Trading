"""Regenerate the example files in sample_data/ (run: python scripts/make_sample_files.py)."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from edgelab.data.sample import generate_bars  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "sample_data"
OUT.mkdir(exist_ok=True)

# 1) A clean, simple CSV: one week of EURUSD 5m in UTC
df = generate_bars("EURUSD", start="2026-01-04", end="2026-01-10", seed=7)  # Sunday open to Friday close
clean = df.reset_index()
clean["ts"] = clean["ts"].dt.strftime("%Y-%m-%d %H:%M:%S")
clean.columns = ["Date", "Open", "High", "Low", "Close", "Volume"]
clean.to_csv(OUT / "EURUSD_5m_example_UTC.csv", index=False)

# 2) A deliberately messy MetaTrader-style export (no header, broker time GMT+2, problems injected)
m = df.copy()
m.index = m.index.tz_convert("Europe/Athens").tz_localize(None)  # broker "server time"
m = m.reset_index()
m.insert(0, "date", m["ts"].dt.strftime("%Y.%m.%d"))
m.insert(1, "time", m["ts"].dt.strftime("%H:%M"))
m = m.drop(columns="ts")
m.loc[500, "close"] = round(m.loc[500, "close"] * 1.02, 5)          # bad tick
m.loc[500, "high"] = max(m.loc[500, "high"], m.loc[500, "close"])
m = pd.concat([m, m.iloc[[800, 801]]])                             # duplicated rows
m = m.drop(index=range(1000, 1060))                                # a five-hour gap
m.loc[1200, ["high", "low"]] = m.loc[1200, ["low", "high"]].to_numpy()  # swapped high/low
m.to_csv(OUT / "EURUSD_M5_messy_broker_time.csv", index=False, header=False)

# 3) An example trade log (illustrative trades, not real performance)
rng = np.random.default_rng(3)
rows = []
for i in range(12):
    t0 = pd.Timestamp("2026-01-05 08:30") + pd.Timedelta(days=int(i // 3), hours=int(2 * (i % 3)))
    side = "Buy" if rng.random() < 0.5 else "Sell"
    entry = float(df["close"].asof(t0.tz_localize("UTC")))
    stop = entry - 0.0010 if side == "Buy" else entry + 0.0010
    exit_t = t0 + pd.Timedelta(minutes=int(rng.integers(15, 120)))
    exit_p = float(df["close"].asof(exit_t.tz_localize("UTC")))
    rows.append([t0.strftime("%Y-%m-%d %H:%M"), exit_t.strftime("%Y-%m-%d %H:%M"), "EURUSD", side, 10000,
                 round(entry, 5), round(exit_p, 5), round(stop, 5), 0.0, "example", ""])
pd.DataFrame(rows, columns=["Open Time", "Close Time", "Symbol", "Type", "Units", "Open Price", "Close Price",
                            "S/L", "Commission", "Setup", "Notes"]).to_csv(OUT / "sample_trade_log.csv", index=False)
print("Wrote", *sorted(p.name for p in OUT.iterdir()))
