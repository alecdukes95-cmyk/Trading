"""Read OHLCV CSV files from common platforms and map their columns to a standard format."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from edgelab.data.timeparse import parse_timestamps

OHLCV_FIELDS = ["timestamp", "date", "time", "open", "high", "low", "close", "volume"]
REQUIRED_PRICE = ["open", "high", "low", "close"]

ALIASES = {
    "timestamp": ["timestamp", "datetime", "date time", "date/time", "time (utc)", "gmt time", "local time",
                  "open time", "opentime", "time_open", "start", "ts", "unix", "time"],
    "date": ["date", "day", "<date>", "trade date"],
    "time": ["time", "<time>", "hour", "time of day"],
    "open": ["open", "o", "<open>", "open price", "opening price", "first"],
    "high": ["high", "h", "<high>", "high price", "max"],
    "low": ["low", "l", "<low>", "low price", "min"],
    "close": ["close", "c", "<close>", "close price", "closing price", "last", "price", "adj close"],
    "volume": ["volume", "vol", "v", "<vol>", "<tickvol>", "tickvol", "tick volume", "tick_volume",
               "volume from", "volume (btc)", "base volume", "real_volume", "<real_volume>"],
}


def _norm(c: str) -> str:
    return re.sub(r"\s+", " ", str(c).strip().lower())


def _looks_like_header(cols) -> bool:
    """False when the 'header' row is actually data (headerless MetaTrader export)."""
    numeric_like = sum(bool(re.fullmatch(r"[-+.\d:/ ]+", str(c).strip())) for c in cols)
    return numeric_like < max(2, len(cols) // 2)


def read_table(source, filename: str | None = None) -> pd.DataFrame:
    """Read a CSV/TXT export (path, bytes or file-like) and sniff the delimiter."""
    if isinstance(source, (str, Path)):
        raw = Path(source).read_bytes()
        filename = filename or Path(source).name
    elif isinstance(source, bytes):
        raw = source
    else:
        raw = source.read()
    text = raw.decode("utf-8-sig", errors="replace")
    df = pd.read_csv(io.StringIO(text), sep=None, engine="python")
    if not _looks_like_header(df.columns):
        df = pd.read_csv(io.StringIO(text), sep=None, engine="python", header=None)
        n = df.shape[1]
        if n >= 7:
            names = ["date", "time", "open", "high", "low", "close", "volume"] + [f"extra{i}" for i in range(n - 7)]
        elif n == 6:
            names = ["timestamp", "open", "high", "low", "close", "volume"]
        else:
            names = ["timestamp", "open", "high", "low", "close"][:n] + [f"extra{i}" for i in range(max(0, n - 5))]
        df.columns = names
    df.columns = [str(c).strip() for c in df.columns]
    return df


def guess_mapping(columns, aliases: dict[str, list[str]] = ALIASES) -> dict[str, str | None]:
    """Guess which source column holds each standard field. Returns field -> column (or None)."""
    norm = {_norm(c): c for c in columns}
    mapping: dict[str, str | None] = {}
    used: set[str] = set()
    order = ["open", "high", "low", "close", "volume", "date", "time", "timestamp"]
    for fld in order:
        mapping[fld] = None
        for alias in aliases[fld]:
            col = norm.get(alias)
            if col is not None and col not in used:
                mapping[fld] = col
                used.add(col)
                break
    # A lone "date" or lone "time" column is really the timestamp
    if mapping["timestamp"] is None:
        if mapping["date"] and not mapping["time"]:
            mapping["timestamp"], mapping["date"] = mapping["date"], None
        elif mapping["time"] and not mapping["date"]:
            mapping["timestamp"], mapping["time"] = mapping["time"], None
    elif mapping["date"] and not mapping["time"]:
        mapping["date"] = None
    if mapping["timestamp"] and mapping["date"] and mapping["time"]:
        mapping["timestamp"] = None  # prefer explicit date + time pair
    return mapping


def guess_symbol(filename: str | None) -> str:
    if not filename:
        return ""
    stem = Path(filename).stem.upper()
    m = re.search(r"(BTC|ETH|XBT|SOL|[A-Z]{3})[-_/]?(USDT|USDC|USD|EUR|GBP|JPY|CHF|AUD|NZD|CAD)", stem)
    return (m.group(1) + m.group(2)) if m else re.split(r"[^A-Z0-9]", stem)[0]


@dataclass
class BuildResult:
    df: pd.DataFrame                 # index: UTC DatetimeIndex 'ts'; columns open/high/low/close/volume
    notes: list[str] = field(default_factory=list)
    dropped_unparseable: int = 0


def build_ohlcv(raw: pd.DataFrame, mapping: dict[str, str | None], source_tz: str = "UTC",
                dayfirst: bool | None = None) -> BuildResult:
    """Turn a raw table + column mapping into a standard OHLCV frame (not yet cleaned)."""
    missing = [f for f in REQUIRED_PRICE if not mapping.get(f)]
    if missing:
        raise ValueError(f"Please choose which column holds: {', '.join(missing)}")
    if mapping.get("date") and mapping.get("time"):
        pr = parse_timestamps(raw[mapping["date"]], source_tz, dayfirst, time_values=raw[mapping["time"]])
    elif mapping.get("timestamp"):
        pr = parse_timestamps(raw[mapping["timestamp"]], source_tz, dayfirst)
    else:
        raise ValueError("Please choose the timestamp column (or a date column plus a time column).")

    out = pd.DataFrame(index=pr.ts)
    for f in REQUIRED_PRICE:
        col = raw[mapping[f]]
        if col.dtype == object or str(col.dtype).startswith("str"):
            col = col.astype(str).str.replace(",", "", regex=False)
        out[f] = pd.to_numeric(col, errors="coerce").to_numpy()
    if mapping.get("volume"):
        out["volume"] = pd.to_numeric(raw[mapping["volume"]], errors="coerce").to_numpy()
    else:
        out["volume"] = 0.0
    out.index.name = "ts"
    bad = out.index.isna()
    notes = list(pr.notes)
    if bad.any():
        notes.append(f"Removed {int(bad.sum())} rows whose timestamp could not be used.")
    out = out[~bad]
    if not mapping.get("volume"):
        notes.append("No volume column was mapped, so volume-based features will be unavailable.")
    return BuildResult(out, notes, int(bad.sum()))
