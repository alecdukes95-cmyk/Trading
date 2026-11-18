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
CRYPTO_SESSION = SessionConfig("UTC", "00:00", "Europe/London", "08:00", "17:00", False)
FULL_DAY_CRYPTO = SessionConfig("UTC", "00:00", "UTC", "00:00", "24:00", False)
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


# --------------------------------------------------------------------------- world market sessions
# Local business hours of the three big FX centres. Defined in each city's own timezone, so daylight
# saving is handled automatically (UK and US change clocks on different dates, which shifts the
# London/New York overlap for a few weeks each spring and autumn). Tokyo has no daylight saving.
MARKET_SESSIONS = {
    "Asia (Tokyo)": ("Asia/Tokyo", "09:00", "18:00"),
    "London": ("Europe/London", "08:00", "17:00"),
    "New York": ("America/New_York", "08:00", "17:00"),
}


def session_membership(ts) -> pd.DataFrame:
    """One boolean column per market session: is this bar inside that city's business hours (weekdays)?"""
    idx = ensure_utc_index(ts)
    out = {}
    for name, (tz, start, end) in MARKET_SESSIONS.items():
        local = idx.tz_convert(tz)
        mins = np.asarray(local.hour * 60 + local.minute)
        wd = np.asarray(local.weekday)
        out[name] = (mins >= _minutes(start)) & (mins < _minutes(end)) & (wd < 5)
    return pd.DataFrame(out, index=idx)


def session_label(ts) -> list[str]:
    """Human label for each bar: e.g. 'London/New York overlap', 'Asia/London overlap', 'Asia only'."""
    m = session_membership(ts)
    short = {"Asia (Tokyo)": "Asia", "London": "London", "New York": "New York"}
    labels = []
    for row in m.itertuples(index=False):
        active = [short[n] for n, on in zip(m.columns, row) if on]
        if not active:
            labels.append("outside the main sessions")
        elif len(active) == 1:
            labels.append(f"{active[0]} session")
        else:
            labels.append("/".join(active) + " overlap")
    return labels


def session_times_today(tz: str = "Europe/London", day: pd.Timestamp | None = None) -> dict[str, tuple[str, str]]:
    """Each market session's hours converted into `tz` for a given day (default today)."""
    day = pd.Timestamp(day or pd.Timestamp.now(tz="UTC")).tz_localize(None).normalize()
    out = {}
    for name, (stz, start, end) in MARKET_SESSIONS.items():
        s = pd.Timestamp(f"{day:%Y-%m-%d} {start}").tz_localize(stz).tz_convert(tz)
        e = pd.Timestamp(f"{day:%Y-%m-%d} {end}").tz_localize(stz).tz_convert(tz)
        out[name] = (s.strftime("%H:%M"), e.strftime("%H:%M"))
    return out
