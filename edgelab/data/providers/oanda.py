"""OANDA v20 REST API - READ-ONLY client for prices and account balance.

Safety: this client can only send HTTP GET requests. It has no code path that could create, change or
cancel orders or trades - that is deliberate. (An OANDA API token can trade, so keep it in .env and
never share it.)

Hosts:   practice (demo): https://api-fxpractice.oanda.com    live: https://api-fxtrade.oanda.com
Get a token: log in to OANDA -> My Account / Manage API Access -> generate a personal access token.
Put it in the .env file as OANDA_API_KEY=... and OANDA_ENVIRONMENT=practice (or live).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

HOSTS = {"practice": "https://api-fxpractice.oanda.com", "live": "https://api-fxtrade.oanda.com"}
GRANULARITY = {"1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30", "1h": "H1", "4h": "H4", "1d": "D"}
STEP = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D": 86400}
MAX_COUNT = 5000
INSTRUMENTS = {"EURUSD": "EUR_USD", "GBPUSD": "GBP_USD", "USDJPY": "USD_JPY", "EURGBP": "EUR_GBP",
               "GBPJPY": "GBP_JPY", "AUDUSD": "AUD_USD", "USDCAD": "USD_CAD", "USDCHF": "USD_CHF",
               "EURJPY": "EUR_JPY", "GBPCHF": "GBP_CHF"}


class OandaError(RuntimeError):
    pass


def to_oanda(symbol: str) -> str:
    s = symbol.upper().replace("/", "").replace("_", "")
    return INSTRUMENTS.get(s, f"{s[:3]}_{s[3:]}")


@dataclass
class AccountSummary:
    account_id: str
    currency: str
    balance: float
    nav: float
    unrealized_pl: float
    margin_used: float
    margin_available: float
    open_trades: int
    margin_closeout_pct: float | None
    fetched_at: pd.Timestamp


class OandaClient:
    def __init__(self, token: str, environment: str = "practice", session=None, pause: float = 0.1):
        if not token:
            raise OandaError("No OANDA API token. Add OANDA_API_KEY=... to the .env file in the EdgeLab folder.")
        if environment not in HOSTS:
            raise OandaError("OANDA_ENVIRONMENT must be 'practice' or 'live'.")
        self.base = HOSTS[environment]
        self.environment = environment
        self._token = token
        self._session = session
        self.pause = pause

    # The ONLY network method. GET requests only - by design.
    def _get(self, path: str, params: dict | None = None) -> dict:
        if self._session is None:
            import requests

            self._session = requests.Session()
        headers = {"Authorization": f"Bearer {self._token}", "Accept-Datetime-Format": "RFC3339",
                   "User-Agent": "edgelab-readonly"}
        for attempt in range(4):
            resp = self._session.get(self.base + path, params=params or {}, headers=headers, timeout=30)
            if resp.status_code == 429:
                time.sleep(1.0 * (attempt + 1))
                continue
            break
        if resp.status_code == 401:
            raise OandaError("OANDA rejected the token (401). Check OANDA_API_KEY, and that OANDA_ENVIRONMENT "
                             "matches the account type (practice token -> practice, live token -> live).")
        if resp.status_code != 200:
            try:
                msg = resp.json().get("errorMessage", resp.text[:200])
            except ValueError:
                msg = resp.text[:200]
            raise OandaError(f"OANDA returned HTTP {resp.status_code}: {msg}")
        return resp.json()

    # ------------------------------------------------------------------ account (read-only)
    def accounts(self) -> list[str]:
        return [a["id"] for a in self._get("/v3/accounts").get("accounts", [])]

    def account_summary(self, account_id: str | None = None) -> AccountSummary:
        if not account_id:
            ids = self.accounts()
            if not ids:
                raise OandaError("No accounts found for this token.")
            account_id = ids[0]
        a = self._get(f"/v3/accounts/{account_id}/summary")["account"]
        f = lambda k, d=0.0: float(a.get(k, d) or d)  # noqa: E731
        mco = a.get("marginCloseoutPercent")
        return AccountSummary(account_id, a.get("currency", "?"), f("balance"), f("NAV"), f("unrealizedPL"),
                              f("marginUsed"), f("marginAvailable"), int(a.get("openTradeCount", 0) or 0),
                              float(mco) * 100 if mco not in (None, "") else None, pd.Timestamp.now(tz="UTC"))

    # ------------------------------------------------------------------ prices
    def candles(self, symbol: str, timeframe: str, start: pd.Timestamp, end: pd.Timestamp, price: str = "MBA",
                progress=None) -> pd.DataFrame:
        """Completed candles between start and end. Returns mid OHLC + volume, plus bid/ask closes
        (bid_c, ask_c) when price includes B and A - used to measure the real spread."""
        if timeframe not in GRANULARITY:
            raise OandaError(f"Timeframe must be one of {', '.join(GRANULARITY)}")
        gran = GRANULARITY[timeframe]
        step = pd.Timedelta(seconds=STEP[gran])
        inst = to_oanda(symbol)
        start, end = _utc(start), _utc(end)
        rows, cursor, n_req = [], start, 0
        total = max(1.0, (end - start) / (step * MAX_COUNT))
        while cursor < end:
            data = self._get(f"/v3/instruments/{inst}/candles",
                             {"granularity": gran, "price": price, "from": cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
                              "count": MAX_COUNT, "dailyAlignment": 17, "alignmentTimezone": "America/New_York"})
            batch = data.get("candles", [])
            n_req += 1
            if not batch:
                break
            for c in batch:
                if not c.get("complete", True):
                    continue
                mid = c.get("mid") or _mid_from(c)
                row = {"time": c["time"], "open": float(mid["o"]), "high": float(mid["h"]), "low": float(mid["l"]),
                       "close": float(mid["c"]), "volume": float(c.get("volume", 0))}
                if "bid" in c and "ask" in c:
                    row["bid_c"], row["ask_c"] = float(c["bid"]["c"]), float(c["ask"]["c"])
                rows.append(row)
            last = pd.Timestamp(batch[-1]["time"])
            last = last.tz_localize("UTC") if last.tzinfo is None else last.tz_convert("UTC")
            new_cursor = last + step
            if new_cursor <= cursor:
                break
            cursor = new_cursor
            if progress:
                progress(min(1.0, n_req / total), f"Downloaded {len(rows):,} candles...")
            if len(batch) < MAX_COUNT:
                break
            if self.pause:
                time.sleep(self.pause)
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(rows)
        df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("time"), utc=True))
        df.index.name = "ts"
        df = df[~df.index.duplicated(keep="last")].sort_index()
        return df[(df.index >= start) & (df.index < end)]


def _mid_from(c: dict) -> dict:
    b, a = c["bid"], c["ask"]
    return {k: (float(b[k]) + float(a[k])) / 2 for k in "ohlc"}


def _utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def spread_stats(df: pd.DataFrame, pip: float, window_mask=None) -> dict | None:
    """Typical spread in pips, measured from bid/ask candle closes (overall and inside your trading window)."""
    if "bid_c" not in df or "ask_c" not in df or df.empty:
        return None
    sp = (df["ask_c"] - df["bid_c"]) / pip
    valid = sp >= 0
    if not valid.any():
        return None
    out = {"median_all": float(sp[valid].median()), "p90_all": float(sp[valid].quantile(0.9))}
    if window_mask is not None:
        m = pd.Series(np.asarray(window_mask, dtype=bool), index=df.index) & valid
        if m.any():
            out["median_window"] = float(sp[m].median())
            out["p90_window"] = float(sp[m].quantile(0.9))
    return out
