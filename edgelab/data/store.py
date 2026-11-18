"""Local SQLite storage for price datasets and trades. Nothing leaves your computer."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from edgelab.timeutil import from_epoch_seconds, to_epoch_seconds

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    source TEXT,
    is_sample INTEGER DEFAULT 0,
    n_bars INTEGER,
    start_ts INTEGER,
    end_ts INTEGER,
    created_at TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS bars (
    dataset_id INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (dataset_id, ts)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,
    symbol TEXT,
    side TEXT,
    entry_ts INTEGER,
    exit_ts INTEGER,
    entry_price REAL,
    exit_price REAL,
    size REAL,
    fees REAL,
    stop_price REAL,
    setup TEXT,
    notes TEXT,
    pnl REAL,
    r_multiple REAL,
    imported_at TEXT
);
"""

TRADE_COLUMNS = ["source", "symbol", "side", "entry_ts", "exit_ts", "entry_price", "exit_price", "size", "fees",
                 "stop_price", "setup", "notes", "pnl", "r_multiple"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------------------------------------------------------------- datasets
    def save_dataset(self, df: pd.DataFrame, *, name: str, symbol: str, asset_class: str, timeframe: str,
                     source: str = "csv", is_sample: bool = False, notes: str = "") -> int:
        if df.empty:
            raise ValueError("Cannot save an empty dataset")
        ts = to_epoch_seconds(df.index)
        rows = np.column_stack([ts, df[["open", "high", "low", "close"]].to_numpy(float),
                                df["volume"].fillna(0).to_numpy(float) if "volume" in df else np.zeros(len(df))])
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO datasets (name, symbol, asset_class, timeframe, source, is_sample, n_bars, start_ts, "
                "end_ts, created_at, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (name, symbol, asset_class, timeframe, source, int(is_sample), len(df), int(ts.min()),
                 int(ts.max()), _now(), notes))
            ds_id = cur.lastrowid
            c.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)",
                          ((ds_id, int(r[0]), r[1], r[2], r[3], r[4], r[5]) for r in rows))
        return int(ds_id)

    def list_datasets(self) -> pd.DataFrame:
        with self._conn() as c:
            df = pd.read_sql_query("SELECT * FROM datasets ORDER BY is_sample, asset_class DESC, symbol, timeframe, id", c)
        if not df.empty:
            df["start"] = from_epoch_seconds(df["start_ts"])
            df["end"] = from_epoch_seconds(df["end_ts"])
        return df

    def get_dataset(self, ds_id: int) -> dict | None:
        df = self.list_datasets()
        row = df[df["id"] == ds_id]
        return None if row.empty else row.iloc[0].to_dict()

    def load_bars(self, ds_id: int, start: pd.Timestamp | None = None, end: pd.Timestamp | None = None) -> pd.DataFrame:
        q = "SELECT ts, open, high, low, close, volume FROM bars WHERE dataset_id = ?"
        params: list = [ds_id]
        if start is not None:
            q += " AND ts >= ?"
            params.append(int(to_epoch_seconds([start])[0]))
        if end is not None:
            q += " AND ts <= ?"
            params.append(int(to_epoch_seconds([end])[0]))
        q += " ORDER BY ts"
        with self._conn() as c:
            df = pd.read_sql_query(q, c, params=params)
        df.index = from_epoch_seconds(df.pop("ts"))
        df.index.name = "ts"
        return df

    def delete_dataset(self, ds_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM bars WHERE dataset_id = ?", (ds_id,))
            c.execute("DELETE FROM datasets WHERE id = ?", (ds_id,))

    def has_samples(self) -> bool:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM datasets WHERE is_sample = 1").fetchone()[0] > 0

    # ------------------------------------------------------------------ trades
    def save_trades(self, trades: pd.DataFrame, source: str) -> int:
        """trades: columns as produced by tradelog.build_trades (entry_time/exit_time as UTC timestamps)."""
        if trades.empty:
            return 0
        t = trades.copy()
        t["source"] = source
        t["entry_ts"] = to_epoch_seconds(t["entry_time"])
        t["exit_ts"] = to_epoch_seconds(t["exit_time"])
        for col in TRADE_COLUMNS:
            if col not in t:
                t[col] = None
        rows = t[TRADE_COLUMNS].astype(object).where(t[TRADE_COLUMNS].notna(), None).itertuples(index=False)
        with self._conn() as c:
            c.executemany(
                f"INSERT INTO trades ({', '.join(TRADE_COLUMNS)}, imported_at) VALUES ({', '.join('?' * len(TRADE_COLUMNS))}, ?)",
                (tuple(r) + (_now(),) for r in rows))
        return len(t)

    def load_trades(self, symbol: str | None = None) -> pd.DataFrame:
        q = "SELECT * FROM trades"
        params: list = []
        if symbol:
            q += " WHERE symbol = ?"
            params.append(symbol)
        q += " ORDER BY entry_ts"
        with self._conn() as c:
            df = pd.read_sql_query(q, c, params=params)
        if not df.empty:
            df["entry_time"] = from_epoch_seconds(df["entry_ts"])
            df["exit_time"] = from_epoch_seconds(df["exit_ts"])
        return df

    def delete_trades(self, source: str | None = None) -> None:
        with self._conn() as c:
            if source is None:
                c.execute("DELETE FROM trades")
            else:
                c.execute("DELETE FROM trades WHERE source = ?", (source,))
