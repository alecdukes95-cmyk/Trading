"""Instrument metadata: asset class, pip size, price precision, currency conversion."""
from __future__ import annotations

from dataclasses import dataclass

FX_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "SEK", "NOK", "SGD", "HKD", "MXN", "ZAR", "TRY", "XAU", "XAG"}
CRYPTO_QUOTES = ("USDT", "USDC", "USD", "EUR", "BTC")


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

    def quote_to_account(self, amount_quote: float, price: float, account_ccy: str = "USD") -> float | None:
        """Convert a P&L amount in quote currency to the account currency.

        Returns None when a conversion rate is not available (e.g. EURGBP with a USD account).
        """
        if self.quote == account_ccy or (self.quote in ("USDT", "USDC") and account_ccy == "USD"):
            return amount_quote
        if self.base == account_ccy and price:
            return amount_quote / price
        return None


INSTRUMENTS: dict[str, Instrument] = {
    "EURUSD": Instrument("EURUSD", "forex", "EUR", "USD", 0.0001, 5, 1.0850, 0.0040, 320),
    "GBPUSD": Instrument("GBPUSD", "forex", "GBP", "USD", 0.0001, 5, 1.2700, 0.0048, 300),
    "USDJPY": Instrument("USDJPY", "forex", "USD", "JPY", 0.01, 3, 150.00, 0.0052, 330),
    "BTCUSD": Instrument("BTCUSD", "crypto", "BTC", "USD", 1.0, 2, 65000.0, 0.024, 12.0),
    "ETHUSD": Instrument("ETHUSD", "crypto", "ETH", "USD", 0.1, 2, 3200.0, 0.032, 150.0),
}

SAMPLE_SYMBOLS = list(INSTRUMENTS)


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
