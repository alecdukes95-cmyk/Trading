"""Price feeds for paper trading: live (OANDA / MetaTrader 5 quotes) or a replay of stored history.

Both give the same two things: current bid/ask quotes, and a chart including the still-forming candle.
"""
from __future__ import annotations

import threading
import time

import numpy as np
import pandas as pd

from edgelab.timeutil import timeframe_minutes


class LiveFeed:
    is_live = True

    def __init__(self, client, name: str):
        self.client, self.name = client, name
        self._chart_cache: dict = {}
        self._lock = threading.Lock()

    def now(self) -> pd.Timestamp:
        return pd.Timestamp.now(tz="UTC")

    def quotes(self, symbols) -> dict:
        return self.client.prices(sorted(set(symbols))) if symbols else {}

    def chart(self, symbol: str, tf: str, bars: int = 200) -> pd.DataFrame:
        key = (symbol, tf)
        with self._lock:
            hit = self._chart_cache.get(key)
        if hit and time.time() - hit[0] < 10:
            return hit[1]
        m = timeframe_minutes(tf)
        end = self.now()
        start = end - pd.Timedelta(minutes=m * bars * 2) - pd.Timedelta(days=3)
        df = self.client.candles(symbol, tf, start, end, price="M", include_incomplete=True).iloc[-bars:]
        with self._lock:
            self._chart_cache[key] = (time.time(), df)
        return df


class ReplayFeed:
    """Replays stored candles as if live, so you can practise any time (e.g. at the weekend).

    Each stored bar is played as 4 quotes (open -> first extreme -> second extreme -> close). Bid/ask are set a
    spread apart around that price. The future is never shown on the chart.
    """
    is_live = False

    def __init__(self, data: dict, spreads_price: dict, start: pd.Timestamp, step_seconds: float = 1.0):
        self.data = {s: df for s, df in data.items() if not df.empty}     # symbol -> base OHLCV (UTC index)
        self.spreads = spreads_price                                      # symbol -> spread in price units
        self.clock = pd.Timestamp(start)
        self.step_seconds = step_seconds
        self.name = "Replay of stored history"
        any_df = next(iter(self.data.values()))
        self.bar = pd.Timedelta(minutes=float(pd.Series(any_df.index).diff().median() / pd.Timedelta(minutes=1)))
        self._lock = threading.Lock()

    def now(self) -> pd.Timestamp:
        return self.clock

    def advance(self) -> None:
        """Move the replay clock forward one quarter-bar (skipping closed-market gaps)."""
        with self._lock:
            nxt = self.clock + self.bar / 4
            ends = []
            for df in self.data.values():
                k = df.index.searchsorted(nxt, side="right") - 1
                if k >= 0 and df.index[k] + self.bar > nxt:
                    break
                j = df.index.searchsorted(nxt)
                if j < len(df):
                    ends.append(df.index[j])
            else:
                if ends:
                    nxt = min(ends)
            self.clock = nxt

    def _path(self, row) -> list[float]:
        o, h, l, c = row["open"], row["high"], row["low"], row["close"]
        return [o, l, h, c] if c >= o else [o, h, l, c]

    def _state(self, symbol: str):
        df = self.data.get(symbol)
        if df is None:
            return None
        k = df.index.searchsorted(self.clock, side="right") - 1
        if k < 0 or df.index[k] + self.bar <= self.clock:
            return None
        sub = int(np.clip((self.clock - df.index[k]) / (self.bar / 4), 0, 3))
        return df, k, sub

    def quotes(self, symbols) -> dict:
        out = {}
        for s in symbols:
            st = self._state(s)
            if st is None:
                continue
            df, k, sub = st
            px = self._path(df.iloc[k])[sub]
            half = self.spreads.get(s, 0.0) / 2
            out[s] = {"bid": px - half, "ask": px + half, "time": self.clock, "tradeable": True}
        return out

    def chart(self, symbol: str, tf: str, bars: int = 200) -> pd.DataFrame:
        from edgelab.data.resample import AGG

        st = self._state(symbol)
        df = self.data.get(symbol)
        if df is None:
            return pd.DataFrame()
        if st is None:
            done = df[df.index + self.bar <= self.clock]
            partial = None
        else:
            _, k, sub = st
            done = df.iloc[:k]
            path = self._path(df.iloc[k])[:sub + 1]
            partial = pd.DataFrame({"open": [path[0]], "high": [max(path)], "low": [min(path)], "close": [path[-1]],
                                    "volume": [df["volume"].iloc[k] * (sub + 1) / 4]}, index=[df.index[k]])
        out = pd.concat([done.iloc[-bars * 4:], partial]) if partial is not None else done.iloc[-bars * 4:]
        m = timeframe_minutes(tf)
        base_m = self.bar / pd.Timedelta(minutes=1)
        if m > base_m:
            out = out.resample(pd.Timedelta(minutes=m), label="left", closed="left", origin="epoch").agg(AGG)
            out = out.dropna(subset=["open"])
        out.index.name = "ts"
        return out.iloc[-bars:]
