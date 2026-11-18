"""Check OHLCV data for problems and explain them in plain English.

Every check produces an Issue with: what was found, why it matters, and what to do.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
import pandas as pd

from edgelab.data.sessions import DEFAULT_SESSIONS, SessionConfig, market_open_mask
from edgelab.timeutil import bar_minutes, timeframe_label

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
STANDARD_MINUTES = {1, 2, 3, 5, 10, 15, 30, 60, 120, 240, 360, 480, 720, 1440, 10080}


@dataclass
class Issue:
    severity: str        # "error" (must fix), "warning" (should look), "info" (good to know)
    code: str
    title: str
    detail: str
    fix: str = ""
    count: int = 0
    examples: list[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    issues: list[Issue]
    n_rows: int
    bar_minutes: float | None
    start: pd.Timestamp | None
    end: pd.Timestamp | None

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def codes(self) -> set[str]:
        return {i.code for i in self.issues}

    def headline(self) -> str:
        tf = timeframe_label(self.bar_minutes)
        span = f"{self.start:%Y-%m-%d} to {self.end:%Y-%m-%d}" if self.start is not None else "no dates"
        e = len(self.errors)
        w = sum(i.severity == "warning" for i in self.issues)
        status = "No blocking problems." if e == 0 else f"{e} problem(s) must be fixed before saving."
        return f"{self.n_rows:,} bars of {tf} data, {span}. {status} {w} warning(s)."


def _fmt(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M UTC")


def _expected_grid(start, end, minutes: float, cfg: SessionConfig) -> pd.DatetimeIndex:
    grid = pd.date_range(start, end, freq=pd.Timedelta(minutes=minutes))
    return grid[market_open_mask(grid, cfg)]


def _gap_runs(missing: pd.DatetimeIndex, minutes: float) -> list[tuple[pd.Timestamp, pd.Timestamp, int]]:
    """Group consecutive missing timestamps into (first, last, n_bars) runs, largest first."""
    if len(missing) == 0:
        return []
    step = pd.Timedelta(minutes=minutes)
    s = pd.Series(missing)
    new_run = s.diff() != step
    run_id = new_run.cumsum()
    runs = s.groupby(run_id).agg(["first", "last", "count"])
    runs = runs.sort_values("count", ascending=False)
    return [(r["first"], r["last"], int(r["count"])) for _, r in runs.iterrows()]


@lru_cache(maxsize=32)
def _dst_dates(years: frozenset) -> set:
    """US and EU daylight-saving change dates (where most timezone mistakes show up)."""
    out = set()
    for y in years:
        for tz in ("America/New_York", "Europe/London"):
            rng = pd.date_range(f"{y}-01-01", f"{y}-12-31 23:00", freq="h", tz=tz)
            offs = pd.Series(rng.map(lambda t: t.utcoffset()), index=rng)
            changes = offs[offs != offs.shift()].index[1:]
            out.update(pd.Timestamp(c).tz_convert("UTC").normalize().tz_localize(None).date() for c in changes)
    return out


def _weekly_open(ts: pd.Timestamp) -> pd.Timestamp:
    """The forex weekly open (Sunday 17:00 New York) nearest to ts, in UTC."""
    ny = ts.tz_convert("America/New_York")
    sunday = (ny - pd.Timedelta(days=(ny.weekday() + 1) % 7)).normalize()
    cands = [(sunday + pd.Timedelta(days=d)).replace(hour=17, minute=0) for d in (-7, 0, 7)]
    cands = [c.tz_localize(None).tz_localize("America/New_York") for c in cands]
    return min((c.tz_convert("UTC") for c in cands), key=lambda c: abs(c - ts))


def detect_weekly_open_offset(idx: pd.DatetimeIndex, bar: pd.Timedelta | None = None) -> float | None:
    """For forex: hours between where the file's weeks start/end and Sunday 17:00 / Friday 17:00 New York.

    Uses every weekend gap, plus the first and last bar of the file when they sit near a weekend.
    Returns the median offset in hours, or None if it cannot be measured.
    """
    if len(idx) < 50:
        return None
    s = pd.Series(idx)
    bar = bar or s.diff().mode().iloc[0]
    offsets = []
    gaps = s.diff() > pd.Timedelta(hours=24)
    for o in s[gaps]:
        offsets.append((o - _weekly_open(o)) / pd.Timedelta(hours=1))
    for c in s.shift(1)[gaps]:
        close_ = c + bar
        offsets.append((close_ - _weekly_open(close_ + pd.Timedelta(days=2))
                        + pd.Timedelta(days=2)) / pd.Timedelta(hours=1))
    # A file may legitimately start after a weekly open or end before a weekly close (partial weeks),
    # but it can't start BEFORE the open or run PAST the close - only those directions prove a shift.
    first, last = s.iloc[0], s.iloc[-1] + bar
    off_first = (first - _weekly_open(first)) / pd.Timedelta(hours=1)
    if -8 <= off_first < 0:
        offsets.append(off_first)
    off_last = (last - _weekly_open(last + pd.Timedelta(days=2)) + pd.Timedelta(days=2)) / pd.Timedelta(hours=1)
    if 0 < off_last <= 8:
        offsets.append(off_last)
    return float(np.median(offsets)) if offsets else None


def validate_ohlcv(df: pd.DataFrame, asset_class: str = "forex", cfg: SessionConfig | None = None,
                   now: pd.Timestamp | None = None) -> ValidationReport:
    cfg = cfg or DEFAULT_SESSIONS.get(asset_class, DEFAULT_SESSIONS["crypto"])
    issues: list[Issue] = []
    n = len(df)
    if n == 0:
        issues.append(Issue("error", "empty", "No usable rows",
                            "After reading the file there were no rows with a valid timestamp and prices.",
                            "Check the column mapping and the date format."))
        return ValidationReport(issues, 0, None, None, None)

    idx = pd.DatetimeIndex(df.index)
    price = df[["open", "high", "low", "close"]]

    # --- 1. Missing values
    nan_rows = price.isna().any(axis=1)
    if nan_rows.any():
        issues.append(Issue("error", "nan_prices", "Missing prices",
                            f"{int(nan_rows.sum())} bars have a blank or non-numeric open/high/low/close.",
                            "These bars will be removed by 'Fix automatically'.", int(nan_rows.sum()),
                            [_fmt(t) for t in idx[nan_rows][:5]]))

    # --- 2. Non-positive prices
    nonpos = (price <= 0).any(axis=1)
    if nonpos.any():
        issues.append(Issue("error", "nonpositive", "Zero or negative prices",
                            f"{int(nonpos.sum())} bars contain a price of zero or below, which is impossible "
                            "for these markets and usually means a data feed glitch.",
                            "Remove these bars.", int(nonpos.sum()), [_fmt(t) for t in idx[nonpos][:5]]))

    # --- 3. OHLC consistency (high must be the highest price, low the lowest)
    bad_ohlc = (df["high"] < price[["open", "close", "low"]].max(axis=1) - 1e-12) | \
               (df["low"] > price[["open", "close", "high"]].min(axis=1) + 1e-12)
    bad_ohlc &= ~nan_rows
    if bad_ohlc.any():
        issues.append(Issue("error", "bad_ohlc", "High/low don't contain the open and close",
                            f"{int(bad_ohlc.sum())} bars have a high below the open/close or a low above it. "
                            "A candle's high must be its highest price and its low its lowest. This often means "
                            "columns were mapped in the wrong order (e.g. high and low swapped).",
                            "Double-check the column mapping; otherwise remove these bars.", int(bad_ohlc.sum()),
                            [_fmt(t) for t in idx[bad_ohlc][:5]]))

    # --- 4. Duplicates and ordering
    dup = idx.duplicated(keep="first")
    if dup.any():
        dup_ts = idx[dup][:50]
        dst_days = _dst_dates(frozenset(t.year for t in dup_ts))
        on_dst = sum(t.date() in dst_days for t in dup_ts)
        hint = (" Many fall on daylight-saving change dates, which suggests the file uses local time with "
                "DST: pick the exact timezone of the export.") if on_dst >= max(1, dup.sum() // 2) else ""
        issues.append(Issue("error", "duplicates", "Duplicate timestamps",
                            f"{int(dup.sum())} timestamps appear more than once.{hint}",
                            "Keep the first copy of each bar ('Fix automatically').", int(dup.sum()),
                            [_fmt(t) for t in idx[dup][:5]]))
    if not idx.is_monotonic_increasing:
        issues.append(Issue("warning", "unsorted", "Bars are out of time order",
                            "Some bars appear before earlier bars in the file. Analysis needs them sorted.",
                            "They will be sorted automatically."))

    clean_idx = idx[~dup].sort_values()
    minutes = bar_minutes(clean_idx)
    start, end = clean_idx[0], clean_idx[-1]

    # --- 5. Bar size
    if minutes is not None and round(minutes) not in STANDARD_MINUTES:
        issues.append(Issue("warning", "odd_timeframe", "Unusual bar size",
                            f"The most common gap between bars is {minutes:g} minutes, which isn't a standard "
                            "chart timeframe. The file may mix timeframes or have many missing bars.",
                            "Make sure you exported a single timeframe."))

    # --- 6. Future timestamps
    now = now or pd.Timestamp.now(tz="UTC")
    future = clean_idx > now + pd.Timedelta(hours=1)
    if future.any():
        issues.append(Issue("warning", "future", "Timestamps in the future",
                            f"{int(future.sum())} bars are dated after the current time. This almost always means "
                            "the timezone is wrong or day and month are swapped.",
                            "Check the timezone and date order settings.", int(future.sum()),
                            [_fmt(t) for t in clean_idx[future][:3]]))

    # --- 7. Missing bars (gaps), respecting the forex weekend
    if minutes is not None and minutes < 1440 and n > 10:
        grid = _expected_grid(start, end, minutes, cfg)
        present = pd.DatetimeIndex(clean_idx)
        missing = grid.difference(present)
        pct = len(missing) / max(1, len(grid)) * 100
        if len(missing):
            runs = _gap_runs(missing, minutes)
            ex = [f"{_fmt(a)} -> {_fmt(b)} ({c} bars)" for a, b, c in runs[:5]]
            sev = "warning" if pct > 2 else "info"
            issues.append(Issue(sev, "gaps", "Missing bars",
                                f"{len(missing):,} expected bars ({pct:.1f}%) are missing in {len(runs)} gap(s). "
                                "Small gaps are normal in quiet periods and holidays; big gaps distort indicators "
                                "like moving averages and VWAP, and can hide price moves from a backtest.",
                                "Gaps are kept as-is (we never invent prices). Consider re-downloading the "
                                "largest gaps.", len(missing), ex))
        offgrid = present[~market_open_mask(present, cfg)]
        if cfg.weekend_closed and len(offgrid) > 0:
            issues.append(Issue("info", "weekend_bars", "Bars during the forex weekend",
                                f"{len(offgrid)} bars fall between Friday 17:00 and Sunday 17:00 New York time, "
                                "when forex is normally closed. Either your broker quotes then, or the timestamps "
                                "are in a different timezone than selected.", "If a 'Timestamps look shifted' warning appears above, fix the timezone first.",
                                len(offgrid), [_fmt(t) for t in offgrid[:3]]))
        if cfg.weekend_closed:
            off = detect_weekly_open_offset(present)
            if off is not None and abs(off) >= 1.0:
                issues.append(Issue("warning", "tz_offset", "Timestamps look shifted",
                                    f"Forex reopens every Sunday at 17:00 New York time. In this file the weekly "
                                    f"open appears about {off:+.0f} hours from that, so the timestamps are probably "
                                    "not in the timezone you selected. MetaTrader brokers often use 'server time' "
                                    "GMT+2 / GMT+3 (choose Europe/Athens or EET).",
                                    "Re-import with the correct source timezone; session levels, VWAP and the "
                                    "opening range depend on it."))

    # --- 8. Price spikes / bad ticks
    valid = df.loc[~(nan_rows | nonpos)].sort_index()
    valid = valid[~valid.index.duplicated()]
    if len(valid) > 30:
        r = np.log(valid["close"]).diff()
        # Compare each move with the *local* typical move, so busy hours aren't flagged just for being busy
        scale = r.abs().rolling(100, min_periods=20).median().shift(1) * 1.4826
        z = r.abs() / scale.replace(0, np.nan)
        spikes = z[z > 25].index
        if len(spikes):
            nxt = r.shift(-1)
            reverts = ((np.sign(nxt) == -np.sign(r)) & (nxt.abs() > 0.7 * r.abs())).reindex(spikes).fillna(False)
            bad_ticks = spikes[reverts.to_numpy(dtype=bool)]
            after_bad = set(valid.index[valid.index.get_indexer(bad_ticks) + 1].tolist()) if len(bad_ticks) else set()
            big_moves = pd.DatetimeIndex([t for t in spikes[~reverts.to_numpy(dtype=bool)] if t not in after_bad])
            if len(bad_ticks):
                issues.append(Issue("warning", "spikes", "Likely bad ticks",
                                    f"{len(bad_ticks)} bars jumped more than 25x the typical recent move and then "
                                    "reversed straight away on the next bar. That is the signature of a bad tick (a "
                                    "wrong price print), which creates fake breakouts and fake signals.",
                                    "Inspect these bars on the chart and remove them if they are clearly errors.",
                                    len(bad_ticks), [_fmt(t) for t in bad_ticks[:5]]))
            if len(big_moves):
                issues.append(Issue("info", "big_moves", "Unusually large moves",
                                    f"{len(big_moves)} bars moved more than 25x the typical recent bar, without "
                                    "reversing. These are usually real (news releases, session opens).",
                                    "Nothing to fix; worth a look on the chart.", len(big_moves),
                                    [_fmt(t) for t in big_moves[:5]]))
        rng_ = (valid["high"] - valid["low"])
        body_hi = valid[["open", "close"]].max(axis=1)
        body_lo = valid[["open", "close"]].min(axis=1)
        typical = rng_.rolling(50, min_periods=10).median().shift(1)
        wick = np.maximum(valid["high"] - body_hi, body_lo - valid["low"])
        wild = (wick > 20 * typical) & (typical > 0)
        if wild.any():
            issues.append(Issue("warning", "wild_wicks", "Suspicious long wicks",
                                f"{int(wild.sum())} candles have a wick more than 20x the recent typical range. "
                                "These are often single bad prints rather than real trading.",
                                "Inspect them; consider removing if they are clearly errors.", int(wild.sum()),
                                [_fmt(t) for t in valid.index[wild][:5]]))
        # split-like jumps between consecutive bars
        split_like = pd.Series(False, index=valid.index)
        for ratio in (valid["open"] / valid["close"].shift(1), valid["close"] / valid["close"].shift(1)):
            for k in (2, 3, 4, 5, 10, 20, 100):
                split_like |= ((ratio - k).abs() / k < 0.03) | ((ratio - 1 / k).abs() * k < 0.03)
        if split_like.any():
            issues.append(Issue("warning", "split_like", "Price changed by a round multiple",
                                f"{int(split_like.sum())} bars open at about 2x, 3x, 10x (or 1/2, 1/3...) the "
                                "previous close. That pattern usually means a split, a token redenomination, a "
                                "contract roll or mixed data sources, not a real move.",
                                "Use adjusted data or split the file at that date.", int(split_like.sum()),
                                [_fmt(t) for t in valid.index[split_like][:5]]))
        flat = (valid["high"] == valid["low"]).mean()
        if flat > 0.2:
            issues.append(Issue("warning", "flat_bars", "Many bars with no movement",
                                f"{flat * 100:.0f}% of bars have high = low (no movement). This suggests a stale "
                                "feed, a very illiquid market, or a timeframe too small for this instrument.",
                                "Use a liquid instrument or a bigger timeframe."))

    # --- 9. Volume
    vol = df["volume"] if "volume" in df else pd.Series(0, index=df.index)
    if (vol.fillna(0) == 0).all():
        issues.append(Issue("info", "no_volume", "No volume data",
                            "Every bar has zero volume. Charts and price patterns still work, but VWAP falls back "
                            "to an equal-weighted average and volume signals are disabled.",
                            "Export with volume (MetaTrader: tick volume) if you can."))
    else:
        zero_share = (vol.fillna(0) == 0).mean()
        if zero_share > 0.1:
            issues.append(Issue("warning", "zero_volume", "Many zero-volume bars",
                                f"{zero_share * 100:.0f}% of bars have zero volume, which weakens volume-based "
                                "signals and VWAP.", ""))
        if (vol < 0).any():
            issues.append(Issue("error", "neg_volume", "Negative volume",
                                f"{int((vol < 0).sum())} bars have negative volume.", "Remove these bars."))
        if asset_class == "forex":
            issues.append(Issue("info", "tick_volume", "Forex volume is tick volume",
                                "Forex has no central exchange, so 'volume' from a broker counts price updates "
                                "(ticks), not traded amount. It tracks activity reasonably well but differs between "
                                "brokers. Treat volume signals on forex as rough."))

    # --- 10. Daily bars with shifting times (DST-shifted daily stamps)
    if minutes is not None and minutes >= 1440:
        hours = pd.Series(clean_idx.hour).value_counts()
        if len(hours) > 1:
            issues.append(Issue("info", "daily_times", "Daily bars start at different hours",
                                f"Daily bars are stamped at {', '.join(f'{h:02d}:00' for h in hours.index[:3])} UTC. "
                                "Usually harmless (daylight saving moves the broker's day boundary), but it means "
                                "the broker's 'day' may differ from the one this app uses.", ""))

    issues.sort(key=lambda i: SEVERITY_ORDER[i.severity])
    return ValidationReport(issues, n, minutes, start, end)


def clean_ohlcv(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Apply only safe, explainable fixes. Never invents or interpolates prices."""
    log = []
    out = df.copy()
    n0 = len(out)
    out = out[~out.index.isna()]
    if not out.index.is_monotonic_increasing:
        out = out.sort_index(kind="stable")
        log.append("Sorted bars into time order.")
    dup = out.index.duplicated(keep="first")
    if dup.any():
        out = out[~dup]
        log.append(f"Removed {int(dup.sum())} duplicate timestamps (kept the first copy).")
    price = out[["open", "high", "low", "close"]]
    bad = price.isna().any(axis=1) | (price <= 0).any(axis=1)
    hi_bad = out["high"] < price[["open", "close", "low"]].max(axis=1) - 1e-12
    lo_bad = out["low"] > price[["open", "close", "high"]].min(axis=1) + 1e-12
    bad |= hi_bad | lo_bad
    if "volume" in out:
        out["volume"] = out["volume"].fillna(0.0)
        bad |= out["volume"] < 0
    if bad.any():
        out = out[~bad]
        log.append(f"Removed {int(bad.sum())} bars with missing, impossible or inconsistent prices/volume.")
    if len(out) != n0 and not log:
        log.append(f"Removed {n0 - len(out)} unusable rows.")
    return out, log
