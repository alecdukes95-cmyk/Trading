"""Robust timestamp parsing for CSV exports from many platforms.

Handles: ISO strings, strings with UTC offsets, day-first vs month-first dates, MetaTrader
"2024.01.02 13:05" style, separate date and time columns, and unix epochs (s / ms / us / ns).
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class ParseResult:
    ts: pd.DatetimeIndex                 # tz-aware UTC (NaT where unparseable)
    notes: list[str] = field(default_factory=list)
    n_failed: int = 0
    n_dst_problems: int = 0
    had_offset: bool = False


def _epoch_unit(values: np.ndarray) -> str:
    m = float(np.nanmedian(np.abs(values)))
    if m > 1e17:
        return "ns"
    if m > 1e14:
        return "us"
    if m > 1e11:
        return "ms"
    return "s"


def _regularity(ts: pd.DatetimeIndex) -> float:
    """Share of consecutive differences equal to the most common positive difference."""
    s = pd.Series(ts.dropna())
    if len(s) < 3:
        return 0.0
    d = s.diff().dropna()
    pos = d[d > pd.Timedelta(0)]
    if pos.empty:
        return 0.0
    return float((d == pos.mode().iloc[0]).mean())


def _parse_strings(s: pd.Series, dayfirst: bool) -> pd.Series:
    s = s.astype(str).str.strip()
    # MetaTrader uses dots in dates: 2024.01.02
    s = s.str.replace(r"^(\d{4})\.(\d{2})\.(\d{2})", r"\1-\2-\3", regex=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # pandas nags when dayfirst doesn't apply to ISO dates
        out = pd.to_datetime(s, errors="coerce", dayfirst=dayfirst, utc=False)
        if out.isna().mean() > 0.5:
            out = pd.to_datetime(s, errors="coerce", dayfirst=dayfirst, format="mixed")
    return out


def parse_timestamps(values: pd.Series, source_tz: str = "UTC", dayfirst: bool | None = None,
                     time_values: pd.Series | None = None) -> ParseResult:
    """Parse a column of timestamps into UTC.

    source_tz: timezone the file's timestamps are written in (ignored if they carry an offset).
    dayfirst: None = auto-detect, True = 31/01/2024, False = 01/31/2024.
    time_values: optional separate time-of-day column (MetaTrader exports).
    """
    notes: list[str] = []
    s = pd.Series(values).reset_index(drop=True)
    if time_values is not None:
        s = s.astype(str).str.strip() + " " + pd.Series(time_values).reset_index(drop=True).astype(str).str.strip()

    numeric = pd.to_numeric(s, errors="coerce")
    if time_values is None and numeric.notna().mean() > 0.95 and numeric.abs().median() > 1e8:
        unit = _epoch_unit(numeric.to_numpy(dtype=float))
        ts = pd.to_datetime(numeric, unit=unit, utc=True, errors="coerce")
        notes.append(f"Timestamps look like unix epoch numbers (unit: {unit}); epochs are always UTC.")
        idx = pd.DatetimeIndex(ts)
        return ParseResult(idx, notes, int(idx.isna().sum()), 0, True)

    if dayfirst is None and s.astype(str).str.strip().str.match(r"^\d{4}[-./]\d{1,2}[-./]\d{1,2}").mean() > 0.9:
        dayfirst = False  # year-first dates (2026-01-05, 2026.01.05) are always year-month-day
    if dayfirst is None:
        a = _parse_strings(s, dayfirst=False)
        b = _parse_strings(s, dayfirst=True)
        fa, fb = a.isna().sum(), b.isna().sum()
        if fa != fb:
            parsed = a if fa < fb else b
            chosen = fa > fb
        elif (a.dropna() == b.dropna()).all():
            parsed, chosen = a, False
        else:
            ra, rb = _regularity(pd.DatetimeIndex(a)), _regularity(pd.DatetimeIndex(b))
            chosen = rb > ra
            parsed = b if chosen else a
            notes.append("Dates were ambiguous (e.g. 03/04 could be 3 April or 4 March). "
                         f"Chose {'day-first (DD/MM)' if chosen else 'month-first (MM/DD)'} because it gives evenly "
                         "spaced bars. Override the date order if that is wrong.")
        if chosen:
            notes.append("Interpreted dates as day-first (DD/MM/YYYY).")
    else:
        parsed = _parse_strings(s, dayfirst=dayfirst)

    n_failed = int(parsed.isna().sum())
    had_offset = isinstance(parsed.dtype, pd.DatetimeTZDtype)
    n_dst = 0
    if had_offset:
        idx = pd.DatetimeIndex(parsed).tz_convert("UTC")
        notes.append("Timestamps include a UTC offset, so the timezone setting was not needed.")
    else:
        naive = pd.DatetimeIndex(parsed)
        if source_tz in ("UTC", "Etc/UTC", "GMT"):
            idx = naive.tz_localize("UTC")
        else:
            local = naive.tz_localize(source_tz, ambiguous="NaT", nonexistent="NaT")
            n_dst = int(local.isna().sum()) - n_failed
            idx = local.tz_convert("UTC")
            if n_dst > 0:
                notes.append(f"{n_dst} timestamps fall in a daylight-saving clock change in {source_tz} "
                             "(a time that happens twice in autumn, or never happens in spring). They were "
                             "marked invalid. This usually means the export uses local time with DST.")
    if n_failed:
        notes.append(f"{n_failed} timestamps could not be read at all.")
    return ParseResult(idx, notes, n_failed, n_dst, had_offset)
