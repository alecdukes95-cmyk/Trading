"""Currency conversion for a GBP (or any) account.

A rate for pair "EURUSD" means: 1 EUR = rate USD (price quoted as QUOTE per 1 BASE).

Where rates come from, in priority order:
1. `hints` passed by the caller - e.g. the trade's own exit price when converting a GBPUSD trade's USD
   profit into GBP. This is the most accurate source.
2. Historical price series from YOUR loaded data (never from synthetic sample data), looked up "as of"
   the time of the conversion.
3. Reference rates from Settings (editable). Used when no real data covers the date; results are flagged.

Missing direct pairs are built by triangulating through USD (e.g. JPY->GBP = JPY->USD->GBP).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

USD_LIKE = {"USDT": "USD", "USDC": "USD"}

DEFAULT_REFERENCE_RATES = {
    "GBPUSD": 1.27,
    "EURUSD": 1.085,
    "USDJPY": 150.0,
    "EURGBP": 0.855,
    "USDCHF": 0.88,
    "USDCAD": 1.36,
    "AUDUSD": 0.66,
    "NZDUSD": 0.60,
    "BTCUSD": 65000.0,
    "ETHUSD": 3200.0,
}
MAX_STALENESS = pd.Timedelta(days=4)  # a series value older than this is not trusted for a date


def _ccy(c: str) -> str:
    c = c.upper()
    return USD_LIKE.get(c, c)


@dataclass
class RateLookup:
    rate: float | None
    source: str  # "identity", "hint", "data", "reference", "missing"


@dataclass
class FxConverter:
    reference: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_REFERENCE_RATES))
    series: dict[str, pd.Series] = field(default_factory=dict)  # pair -> close prices (UTC index, sorted)
    used_sources: set = field(default_factory=set)

    # ------------------------------------------------------------------ direct pair lookup
    def _pair(self, pair: str, at, hints: dict | None) -> RateLookup:
        if hints and pair in hints and hints[pair]:
            return RateLookup(float(hints[pair]), "hint")
        s = self.series.get(pair)
        if s is not None and len(s) and at is not None:
            at = pd.Timestamp(at)
            at = at.tz_localize("UTC") if at.tzinfo is None else at.tz_convert("UTC")
            pos = s.index.searchsorted(at, side="right") - 1
            if pos >= 0 and at - s.index[pos] <= MAX_STALENESS:
                return RateLookup(float(s.iloc[pos]), "data")
        if s is not None and len(s) and at is None:
            return RateLookup(float(s.iloc[-1]), "data")
        if pair in self.reference and self.reference[pair]:
            return RateLookup(float(self.reference[pair]), "reference")
        return RateLookup(None, "missing")

    def _direct(self, base: str, quote: str, at, hints) -> RateLookup:
        r = self._pair(base + quote, at, hints)
        if r.rate:
            return r
        inv = self._pair(quote + base, at, hints)
        if inv.rate:
            return RateLookup(1.0 / inv.rate, inv.source)
        return RateLookup(None, "missing")

    # ------------------------------------------------------------------ public API
    def lookup(self, from_ccy: str, to_ccy: str, at=None, hints: dict | None = None) -> RateLookup:
        """How many `to_ccy` one unit of `from_ccy` is worth."""
        a, b = _ccy(from_ccy), _ccy(to_ccy)
        if a == b:
            return RateLookup(1.0, "identity")
        d = self._direct(a, b, at, hints)
        if d.rate:
            self.used_sources.add(d.source)
            return d
        if "USD" not in (a, b):
            x, y = self._direct(a, "USD", at, hints), self._direct("USD", b, at, hints)
            if x.rate and y.rate:
                src = "reference" if "reference" in (x.source, y.source) else ("data" if "data" in (x.source, y.source) else "hint")
                self.used_sources.add(src)
                return RateLookup(x.rate * y.rate, src)
        self.used_sources.add("missing")
        return RateLookup(None, "missing")

    def rate(self, from_ccy: str, to_ccy: str, at=None, hints: dict | None = None) -> float | None:
        return self.lookup(from_ccy, to_ccy, at, hints).rate

    def convert(self, amount: float, from_ccy: str, to_ccy: str, at=None, hints: dict | None = None) -> float | None:
        r = self.rate(from_ccy, to_ccy, at, hints)
        return None if r is None or amount is None or (isinstance(amount, float) and np.isnan(amount)) else amount * r

    def describe_reference(self) -> str:
        return ", ".join(f"{k} {v:g}" for k, v in self.reference.items())


def converter_from_store(store, reference: dict[str, float] | None = None) -> FxConverter:
    """Build a converter from the user's OWN datasets (synthetic samples are deliberately excluded)."""
    conv = FxConverter(reference=dict(reference or DEFAULT_REFERENCE_RATES))
    ds = store.list_datasets()
    if ds.empty:
        return conv
    mine = ds[ds["is_sample"] == 0]
    for sym, grp in mine.groupby("symbol"):
        frames = [store.load_bars(int(i))["close"] for i in grp["id"]]
        s = pd.concat(frames).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        key = sym.replace("USDT", "USD").replace("USDC", "USD")
        conv.series[key] = s
    return conv
