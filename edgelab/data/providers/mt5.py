"""MetaTrader 5 on Windows: free history AND live candles from any MT5 broker account (a free demo works).

Requirements (Windows only):
1. Install the MetaTrader 5 terminal from your broker (or metaquotes.net) and log in to a demo account.
2. `pip install MetaTrader5` (run.bat installs it automatically on Windows).
3. Keep the terminal running while EdgeLab downloads or scans.

READ-ONLY: this module only calls initialize / symbol lookup / copy_rates_range. It contains no order calls.

Timestamps: MT5 returns bar times in the BROKER'S SERVER TIME (often GMT+2/+3). Set the server timezone in
Settings (default Europe/Athens); EdgeLab converts to UTC. Demo accounts may give limited 1-minute history
(sometimes ~3 months); 5m/15m/1h usually go back further.
"""
from __future__ import annotations

import pandas as pd

TIMEFRAMES = {"1m": "TIMEFRAME_M1", "5m": "TIMEFRAME_M5", "15m": "TIMEFRAME_M15", "30m": "TIMEFRAME_M30",
              "1h": "TIMEFRAME_H1", "4h": "TIMEFRAME_H4", "1d": "TIMEFRAME_D1"}
MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}


class MT5Error(RuntimeError):
    pass


def _load():
    try:
        import MetaTrader5 as mt5  # noqa: N813
    except ImportError as e:
        raise MT5Error("The MetaTrader5 Python package isn't installed. It only works on Windows: run "
                       "`.venv\\Scripts\\python -m pip install MetaTrader5`, and install + log in to the MT5 "
                       "terminal.") from e
    return mt5


class MT5Client:
    def __init__(self, server_tz: str = "Europe/Athens", symbol_suffix: str = "", mt5_module=None):
        self.mt5 = mt5_module or _load()
        self.server_tz = server_tz
        self.suffix = symbol_suffix
        if not self.mt5.initialize():
            raise MT5Error(f"Couldn't connect to the MetaTrader 5 terminal ({self.mt5.last_error()}). Is it open and "
                           "logged in?")
        self._resolved: dict = {}

    def resolve(self, symbol: str) -> str:
        """Find the broker's name for a pair (brokers add suffixes like EURUSD.a or EURUSDm)."""
        if symbol in self._resolved:
            return self._resolved[symbol]
        for cand in [symbol + self.suffix, symbol]:
            if self.mt5.symbol_info(cand) is not None:
                self.mt5.symbol_select(cand, True)
                self._resolved[symbol] = cand
                return cand
        found = self.mt5.symbols_get(f"*{symbol}*") or []
        if not found:
            raise MT5Error(f"Your broker has no symbol like {symbol}.")
        name = sorted((f.name for f in found), key=len)[0]
        self.mt5.symbol_select(name, True)
        self._resolved[symbol] = name
        return name

    def candles(self, symbol: str, timeframe: str, start, end, price: str = "M") -> pd.DataFrame:
        """Completed candles between start and end (UTC). Same interface as the OANDA client."""
        if timeframe not in TIMEFRAMES:
            raise MT5Error(f"Timeframe must be one of {', '.join(TIMEFRAMES)}")
        tf = getattr(self.mt5, TIMEFRAMES[timeframe])
        start = pd.Timestamp(start).tz_convert("UTC") if pd.Timestamp(start).tzinfo else pd.Timestamp(start, tz="UTC")
        end = pd.Timestamp(end).tz_convert("UTC") if pd.Timestamp(end).tzinfo else pd.Timestamp(end, tz="UTC")
        # ask for a margin either side because the terminal works in server time; trim after converting
        rates = self.mt5.copy_rates_range(self.resolve(symbol), tf, (start - pd.Timedelta(days=1)).to_pydatetime(),
                                          (end + pd.Timedelta(days=1)).to_pydatetime())
        if rates is None:
            raise MT5Error(f"No data from MetaTrader 5 ({self.mt5.last_error()}).")
        df = pd.DataFrame(rates)
        if df.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        local = pd.to_datetime(df["time"].astype("int64"), unit="s")   # server wall-clock time
        idx = pd.DatetimeIndex(local).tz_localize(self.server_tz, ambiguous="NaT", nonexistent="NaT").tz_convert("UTC")
        vol = df["tick_volume"] if "tick_volume" in df else df.get("real_volume", 0)
        out = pd.DataFrame({"open": df["open"].to_numpy(float), "high": df["high"].to_numpy(float),
                            "low": df["low"].to_numpy(float), "close": df["close"].to_numpy(float),
                            "volume": pd.Series(vol).to_numpy(float)}, index=idx)
        out = out[~out.index.isna()]
        out.index.name = "ts"
        now = pd.Timestamp.now(tz="UTC")
        complete = out.index + pd.Timedelta(minutes=MINUTES[timeframe]) <= now
        out = out[complete & (out.index >= start) & (out.index < end)]
        return out[~out.index.duplicated(keep="last")].sort_index()
