"""Trading-day and session-window logic for 24-hour markets.

Forex and crypto trade around the clock, so "the day" has to be defined explicitly:

* The **trading day** decides where daily candles, VWAP resets and prior-day high/low come from.
  Forex convention: the day rolls at 17:00 New York time (so Sunday 17:00 NY starts Monday's
  session). Crypto convention: the day rolls at 00:00 UTC (how exchanges build daily candles).
* The **session window** is the part of the day you actually trade (default London open through
  the London/New York overlap for forex). The opening range and time-of-day filters use it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from edgelab.timeutil import ensure_utc_index


def _minutes(hhmm: str) -> int:
    h, m = hhmm.strip().split(":")
    return int(h) * 60 + int(m)


@dataclass(frozen=True)
class SessionConfig:
    day_tz: str = "UTC"
    day_start: str = "00:00"
    window_tz: str = "UTC"
    window_start: str = "00:00"
    window_end: str = "24:00"
    weekend_closed: bool = False  # forex: closed Fri 17:00 New York -> Sun 17:00 New York

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SessionConfig":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})

    def describe(self) -> str:
        return (f"Trading day starts {self.day_start} {self.day_tz}; "
                f"active window {self.window_start}-{self.window_end} {self.window_tz}")


FOREX_SESSION = SessionConfig("America/New_York", "17:00", "Europe/London", "08:00", "17:00", True)
CRYPTO_SESSION = SessionConfig("UTC", "00:00", "UTC", "00:00", "24:00", False)
DEFAULT_SESSIONS = {"forex": FOREX_SESSION, "crypto": CRYPTO_SESSION}


def trading_day(ts, cfg: SessionConfig) -> pd.DatetimeIndex:
    """Label each timestamp with its trading day (a naive midnight date)."""
    idx = ensure_utc_index(ts)
    local = idx.tz_convert(cfg.day_tz).tz_localize(None)
    start = pd.Timedelta(minutes=_minutes(cfg.day_start))
    shift = pd.Timedelta(days=1) - start if start > pd.Timedelta(0) else pd.Timedelta(0)
    return (local + shift).normalize()


def _window_bounds(cfg: SessionConfig) -> tuple[int, int]:
    return _minutes(cfg.window_start), _minutes(cfg.window_end)


def minutes_of_day(ts, tz: str) -> np.ndarray:
    local = ensure_utc_index(ts).tz_convert(tz)
    return np.asarray(local.hour * 60 + local.minute)


def in_window(ts, cfg: SessionConfig) -> np.ndarray:
    """True for bars whose open time is inside the active session window."""
    s, e = _window_bounds(cfg)
    if s == e or (s == 0 and e >= 1440):
        return np.ones(len(ts), dtype=bool)
    mins = minutes_of_day(ts, cfg.window_tz)
    if s < e:
        return (mins >= s) & (mins < e)
    return (mins >= s) | (mins < e)  # window crosses midnight


def minutes_into_window(ts, cfg: SessionConfig) -> np.ndarray:
    s, _ = _window_bounds(cfg)
    return (minutes_of_day(ts, cfg.window_tz) - s) % 1440


def window_date(ts, cfg: SessionConfig) -> pd.DatetimeIndex:
    """Calendar date of the session window a bar belongs to (handles windows crossing midnight)."""
    s, _ = _window_bounds(cfg)
    local = ensure_utc_index(ts).tz_convert(cfg.window_tz).tz_localize(None)
    return (local - pd.Timedelta(minutes=s)).normalize()


def market_open_mask(ts, cfg: SessionConfig) -> np.ndarray:
    """False for timestamps inside the forex weekend closure (Fri 17:00 -> Sun 17:00 New York)."""
    idx = ensure_utc_index(ts)
    if not cfg.weekend_closed:
        return np.ones(len(idx), dtype=bool)
    ny = idx.tz_convert("America/New_York")
    wd = np.asarray(ny.weekday)
    mins = np.asarray(ny.hour * 60 + ny.minute)
    close_min = 17 * 60
    closed = (wd == 5) | ((wd == 4) & (mins >= close_min)) | ((wd == 6) & (mins < close_min))
    return ~closed
