"""Download crypto candles from Coinbase's public market-data endpoint (no account or key needed).

Endpoint: GET https://api.exchange.coinbase.com/products/{product}/candles
Each response holds at most 300 candles as [time, low, high, open, close, volume], newest first.
"""
from __future__ import annotations

import time

import pandas as pd

BASE_URL = "https://api.exchange.coinbase.com"
GRANULARITY = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400}
MAX_CANDLES = 300
PRODUCTS = {"BTCGBP": "BTC-GBP", "ETHGBP": "ETH-GBP", "BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD", "SOLUSD": "SOL-USD"}


class ProviderError(RuntimeError):
    pass


def _session():
    import requests

    s = requests.Session()
    s.headers["User-Agent"] = "edgelab-educational-chart-tool"
    return s


def fetch_candles(product: str, timeframe: str, start: pd.Timestamp, end: pd.Timestamp, session=None,
                  pause: float = 0.35, progress=None, max_retries: int = 4) -> pd.DataFrame:
    if timeframe not in GRANULARITY:
        raise ProviderError(f"Coinbase supports {', '.join(GRANULARITY)}; got {timeframe}")
    gran = GRANULARITY[timeframe]
    session = session or _session()
    start = pd.Timestamp(start).tz_convert("UTC") if pd.Timestamp(start).tzinfo else pd.Timestamp(start, tz="UTC")
    end = pd.Timestamp(end).tz_convert("UTC") if pd.Timestamp(end).tzinfo else pd.Timestamp(end, tz="UTC")
    chunk = pd.Timedelta(seconds=gran * MAX_CANDLES)
    rows: list = []
    cursor = start
    total = max(1.0, (end - start) / chunk)
    i = 0
    while cursor < end:
        stop = min(cursor + chunk, end)
        params = {"granularity": gran, "start": cursor.isoformat(), "end": stop.isoformat()}
        for attempt in range(max_retries):
            resp = session.get(f"{BASE_URL}/products/{product}/candles", params=params, timeout=20)
            if resp.status_code == 429:
                time.sleep(pause * (2 ** attempt) * 3)
                continue
            break
        if resp.status_code != 200:
            raise ProviderError(f"Coinbase returned HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if isinstance(data, dict):
            raise ProviderError(f"Coinbase error: {data.get('message', data)}")
        rows.extend(data)
        cursor = stop
        i += 1
        if progress:
            progress(min(1.0, i / total), f"Downloaded {len(rows):,} candles...")
        if pause:
            time.sleep(pause)
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=["time", "low", "high", "open", "close", "volume"])
    df.index = pd.to_datetime(df.pop("time").astype("int64"), unit="s", utc=True)
    df.index.name = "ts"
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df[(df.index >= start) & (df.index < end)]
