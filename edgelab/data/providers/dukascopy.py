"""Free historical forex candles from Dukascopy's public datafeed - no account, no API key.

Dukascopy (a Swiss bank) publishes years of 1-minute candles. Each trading day is one small LZMA-compressed
file:  https://datafeed.dukascopy.com/datafeed/{PAIR}/{YYYY}/{MM}/{DD}/BID_candles_min_1.bi5
* MM is ZERO-based (00 = January, 11 = December).
* Each record is 24 bytes, big-endian: seconds since 00:00 UTC (int32), four prices (int32), volume (float32).
  Prices are integers in points (divide by 10^5, or 10^3 for JPY pairs).
* The order of the four prices is not officially documented (open, close, low, high is the common reading),
  so this reader checks which order makes every candle's high the highest and low the lowest, and uses that.
* The file for a day appears after that day ends (UTC), so this is for HISTORY, not live scanning.
* Weekend days contain flat, zero-volume filler candles; these are removed.

Please be polite: this downloads one small file per day with a handful of parallel requests.
For personal, educational use.
"""
from __future__ import annotations

import lzma
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from itertools import permutations

import numpy as np
import pandas as pd

BASE_URL = "https://datafeed.dukascopy.com/datafeed"
RECORD = struct.Struct(">5if")
SUPPORTED = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD", "EURGBP", "EURJPY", "GBPJPY",
             "EURCHF", "GBPCHF", "AUDJPY", "EURAUD"]


class DukascopyError(RuntimeError):
    pass


def day_url(symbol: str, day: pd.Timestamp, side: str = "BID") -> str:
    return f"{BASE_URL}/{symbol.upper()}/{day.year:04d}/{day.month - 1:02d}/{day.day:02d}/{side}_candles_min_1.bi5"


def parse_day(raw: bytes, day: pd.Timestamp, point: float) -> pd.DataFrame:
    """Decode one day's file into 1-minute candles (UTC)."""
    if not raw:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    data = lzma.decompress(raw)
    n = len(data) // RECORD.size
    if n == 0:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    arr = np.array([RECORD.unpack_from(data, k * RECORD.size) for k in range(n)], dtype=float)
    secs, p, vol = arr[:, 0], arr[:, 1:5] / point, arr[:, 5]
    o, h, l, c = _resolve_order(p)
    day = pd.Timestamp(day).tz_localize("UTC") if pd.Timestamp(day).tzinfo is None else pd.Timestamp(day).tz_convert("UTC")
    idx = day.normalize() + pd.to_timedelta(secs.astype("int64"), unit="s")
    df = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": vol}, index=pd.DatetimeIndex(idx))
    df.index.name = "ts"
    return df


def _resolve_order(p: np.ndarray):
    """Work out which of the four price columns are open/high/low/close.

    Consistency (open and close inside [low, high]) picks out the high and low; continuity (each candle opens
    near the previous close) separates open from close. The common reading (open, close, low, high) wins ties."""
    preferred = (0, 3, 2, 1)   # file order open, close, low, high -> (open, high, low, close) column indices
    scored = []
    for perm in [preferred] + [q for q in permutations(range(4)) if q != preferred]:
        o, h, l, c = (p[:, k] for k in perm)
        ok = np.mean((h >= np.maximum(o, c) - 1e-12) & (l <= np.minimum(o, c) + 1e-12) & (h >= l))
        gap = float(np.mean(np.abs(o[1:] - c[:-1]))) if len(o) > 1 else 0.0
        scored.append((ok, -gap, perm == preferred, perm))
    best = max(scored, key=lambda t: (round(t[0], 9), round(t[1], 12), t[2]))[3]
    return tuple(p[:, k] for k in best)


def fetch(symbol: str, start: pd.Timestamp, end: pd.Timestamp, session=None, workers: int = 4, decimals: int = 5,
          progress=None, retries: int = 3) -> pd.DataFrame:
    """1-minute BID candles for [start, end) (UTC). Weekends and empty days are skipped."""
    symbol = symbol.upper()
    if symbol not in SUPPORTED:
        raise DukascopyError(f"{symbol} is not in the supported list: {', '.join(SUPPORTED)}")
    if session is None:
        import requests

        session = requests.Session()
        session.headers["User-Agent"] = "edgelab-educational-research"
    start = pd.Timestamp(start).tz_convert("UTC") if pd.Timestamp(start).tzinfo else pd.Timestamp(start, tz="UTC")
    end = pd.Timestamp(end).tz_convert("UTC") if pd.Timestamp(end).tzinfo else pd.Timestamp(end, tz="UTC")
    today = pd.Timestamp.now(tz="UTC").normalize()
    days = [d for d in pd.date_range(start.normalize(), min(end, today), freq="D", inclusive="left")
            if d.weekday() != 5]   # no Saturday files worth fetching
    point = 10 ** decimals

    def one(day):
        for attempt in range(retries):
            try:
                resp = session.get(day_url(symbol, day), timeout=30)
            except Exception as e:  # noqa: BLE001
                if attempt == retries - 1:
                    raise DukascopyError(f"Network error for {day:%Y-%m-%d}: {e}") from e
                time.sleep(1 + attempt)
                continue
            if resp.status_code == 404:
                return None
            if resp.status_code == 200:
                return parse_day(resp.content, day, point)
            if resp.status_code in (429, 500, 502, 503):
                time.sleep(2 * (attempt + 1))
                continue
            raise DukascopyError(f"Dukascopy returned HTTP {resp.status_code} for {day:%Y-%m-%d}")
        return None

    frames, done = [], 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for df in ex.map(one, days):
            done += 1
            if df is not None and not df.empty:
                frames.append(df)
            if progress and (done % 5 == 0 or done == len(days)):
                progress(done / max(1, len(days)), f"{symbol}: {done}/{len(days)} days")
    if not frames:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    out = pd.concat(frames).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out = out[(out.index >= start) & (out.index < end)]
    flat_filler = (out["volume"] <= 0) & (out["high"] == out["low"])
    return out[~flat_filler]


def fetch_bars(symbol: str, timeframe_min: int, start, end, cfg, decimals: int = 5, **kw) -> pd.DataFrame:
    """Download 1-minute candles and build the requested timeframe (e.g. 5m)."""
    from edgelab.data.resample import resample_ohlcv
    from edgelab.data.sessions import market_open_mask

    m1 = fetch(symbol, start, end, decimals=decimals, **kw)
    if m1.empty:
        return m1
    m1 = m1[market_open_mask(m1.index, cfg)]
    return m1 if timeframe_min == 1 else resample_ohlcv(m1, timeframe_min, cfg)
