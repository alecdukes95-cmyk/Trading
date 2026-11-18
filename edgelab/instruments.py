"""Instrument metadata: asset class, pip size, price precision, currency conversion."""
from __future__ import annotations

from dataclasses import dataclass

FX_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "SEK", "NOK", "SGD", "HKD", "MXN", "ZAR", "TRY", "XAU", "XAG"}
CRYPTO_QUOTES = ("USDT", "USDC", "USD", "GBP", "EUR", "BTC")
MAJOR_CCYS = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD"}


@dataclass(frozen=True)
class Instrument:
    symbol: str
    asset_class: str          # "forex" or "crypto"
    base: str
    quote: str
    pip: float                # forex: one pip; crypto: one tick used for display
    decimals: int             # price decimals to display/round
    typical_price: float = 1.0
    daily_vol: float = 0.006  # typical daily move as a fraction (used by the sample generator)
    base_volume: float = 300  # typical 5-minute volume (sample generator)

    @property
    def is_forex(self) -> bool:
        return self.asset_class == "forex"

    def price_to_pips(self, delta: float) -> float:
        return delta / self.pip

    def fmt_price(self, p: float) -> str:
        return f"{p:,.{self.decimals}f}"

    def fmt_move(self, delta: float, ref_price: float | None = None) -> str:
        """Human description of a price move: pips for forex, $ and % for crypto."""
        if self.is_forex:
            return f"{delta / self.pip:+.1f} pips"
        pct = f" ({delta / ref_price * 100:+.2f}%)" if ref_price else ""
        return f"{delta:+,.{self.decimals}f}{pct}"

    @property
    def leverage_class(self) -> str:
        """FCA/ESMA categories: 'fx_major' (only USD, EUR, GBP, JPY, CHF, CAD), 'fx_minor', or 'crypto'."""
        if not self.is_forex:
            return "crypto"
        return "fx_major" if {self.base, self.quote} <= MAJOR_CCYS else "fx_minor"


INSTRUMENTS: dict[str, Instrument] = {
    # majors
    "EURUSD": Instrument("EURUSD", "forex", "EUR", "USD", 0.0001, 5, 1.0850, 0.0040, 320),
    "GBPUSD": Instrument("GBPUSD", "forex", "GBP", "USD", 0.0001, 5, 1.2700, 0.0048, 300),
    "USDJPY": Instrument("USDJPY", "forex", "USD", "JPY", 0.01, 3, 150.00, 0.0052, 330),
    "USDCHF": Instrument("USDCHF", "forex", "USD", "CHF", 0.0001, 5, 0.8800, 0.0045, 220),
    "USDCAD": Instrument("USDCAD", "forex", "USD", "CAD", 0.0001, 5, 1.3600, 0.0040, 240),
    "AUDUSD": Instrument("AUDUSD", "forex", "AUD", "USD", 0.0001, 5, 0.6600, 0.0058, 260),
    "NZDUSD": Instrument("NZDUSD", "forex", "NZD", "USD", 0.0001, 5, 0.6000, 0.0060, 200),
    # crosses
    "EURGBP": Instrument("EURGBP", "forex", "EUR", "GBP", 0.0001, 5, 0.8550, 0.0035, 250),
    "EURJPY": Instrument("EURJPY", "forex", "EUR", "JPY", 0.01, 3, 162.00, 0.0058, 300),
    "GBPJPY": Instrument("GBPJPY", "forex", "GBP", "JPY", 0.01, 3, 190.00, 0.0065, 350),
    "EURCHF": Instrument("EURCHF", "forex", "EUR", "CHF", 0.0001, 5, 0.9500, 0.0035, 180),
    "GBPCHF": Instrument("GBPCHF", "forex", "GBP", "CHF", 0.0001, 5, 1.1200, 0.0050, 200),
    "AUDJPY": Instrument("AUDJPY", "forex", "AUD", "JPY", 0.01, 3, 99.00, 0.0072, 260),
    "EURAUD": Instrument("EURAUD", "forex", "EUR", "AUD", 0.0001, 5, 1.6400, 0.0055, 220),
    # crypto (parked for now - hidden in the app unless enabled in Settings)
    "BTCUSD": Instrument("BTCUSD", "crypto", "BTC", "USD", 1.0, 2, 65000.0, 0.024, 12.0),
    "ETHUSD": Instrument("ETHUSD", "crypto", "ETH", "USD", 0.1, 2, 3200.0, 0.032, 150.0),
}

FOREX_SYMBOLS = [k for k, v in INSTRUMENTS.items() if v.asset_class == "forex"]
SAMPLE_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD", "EURGBP", "EURJPY",
                  "GBPJPY"]
DEFAULT_WATCHLIST = ["EURUSD", "GBPUSD", "USDJPY", "EURGBP", "GBPJPY", "AUDUSD", "USDCAD", "EURJPY"]


def normalize_symbol(symbol: str) -> str:
    return "".join(ch for ch in symbol.upper() if ch.isalnum())


def get_instrument(symbol: str, asset_class: str | None = None) -> Instrument:
    """Look up a known instrument or infer sensible metadata for an unknown one."""
    sym = normalize_symbol(symbol)
    if sym in INSTRUMENTS and (asset_class is None or INSTRUMENTS[sym].asset_class == asset_class):
        return INSTRUMENTS[sym]
    # Common aliases: BTCUSDT -> BTCUSD metadata, XBT -> BTC
    alias = sym.replace("XBT", "BTC")
    for q in ("USDT", "USDC"):
        if alias.endswith(q) and alias[: -len(q)] + "USD" in INSTRUMENTS:
            base = INSTRUMENTS[alias[: -len(q)] + "USD"]
            return Instrument(sym, "crypto", base.base, q, base.pip, base.decimals, base.typical_price, base.daily_vol, base.base_volume)
    if alias in INSTRUMENTS:
        i = INSTRUMENTS[alias]
        return Instrument(sym, i.asset_class, i.base, i.quote, i.pip, i.decimals, i.typical_price, i.daily_vol, i.base_volume)

    looks_fx = len(sym) == 6 and sym[:3] in FX_CURRENCIES and sym[3:] in FX_CURRENCIES
    if asset_class == "forex" or (asset_class is None and looks_fx):
        base, quote = (sym[:3], sym[3:]) if len(sym) == 6 else (sym, "USD")
        jpy = quote == "JPY"
        return Instrument(sym, "forex", base, quote, 0.01 if jpy else 0.0001, 3 if jpy else 5)
    for q in CRYPTO_QUOTES:
        if sym.endswith(q) and len(sym) > len(q):
            return Instrument(sym, "crypto", sym[: -len(q)], q, 0.01, 4)
    return Instrument(sym, asset_class or "crypto", sym, "USD", 0.01, 4)
