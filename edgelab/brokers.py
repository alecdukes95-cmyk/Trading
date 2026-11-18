"""Broker profiles: costs, leverage and margin rules. Switch the active broker in Settings.

UK retail rules (FCA) built into every profile by default:
* Maximum leverage 30:1 on major currency pairs (pairs made only of USD, EUR, GBP, JPY, CHF, CAD),
  20:1 on other currency pairs.
* Margin close-out: the broker must start closing positions when equity falls to 50% of the margin
  required to hold them.
* Crypto derivatives (CFDs, spread bets, futures, options) may not be sold to UK retail clients, so
  crypto is treated as unleveraged spot (1:1) unless you change it.

The cost numbers in the built-in profiles are PLACEHOLDERS, not quotes from those brokers. Replace
them with the spreads and commissions your broker actually shows you.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

COMMISSION_MODELS = {
    "none": "No commission (costs are in the spread)",
    "per_lot_round_turn": "Per standard lot (100,000 units), round turn",
    "per_lot_per_side": "Per standard lot (100,000 units), each side",
    "per_trade": "Flat amount per trade, round turn",
    "percent_per_side": "% of trade value, each side",
}

DEFAULT_SPREADS = {"EURUSD": 1.0, "GBPUSD": 1.3, "USDJPY": 1.2, "EURGBP": 1.2, "GBPJPY": 2.5}
STANDARD_LOT = 100_000


@dataclass
class BrokerProfile:
    name: str
    platform: str = "MT4 / MT5"
    csv_timezone: str = "Europe/Athens"
    commission_model: str = "none"
    commission_value: float = 0.0             # in account currency (or % for percent_per_side)
    spreads_pips: dict = field(default_factory=lambda: dict(DEFAULT_SPREADS))
    default_spread_pips: float = 1.5           # for pairs not listed
    slippage_pips: float = 0.2                 # per side, forex
    crypto_spread_pct: float = 0.10            # full spread as % of price
    crypto_fee_pct: float = 0.25               # per side
    crypto_slippage_pct: float = 0.02          # per side
    leverage_fx_major: float = 30.0
    leverage_fx_minor: float = 20.0
    leverage_crypto: float = 1.0
    margin_closeout_pct: float = 50.0
    min_units: int = 1_000                     # 0.01 lot
    data_access: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BrokerProfile":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})

    def spread_for(self, symbol: str) -> float:
        return float(self.spreads_pips.get(symbol, self.default_spread_pips))


PLACEHOLDER_NOTE = ("Costs below are placeholders, not this broker's real prices. Copy the spreads and "
                    "commission from your broker's website or trading platform.")

BUILTIN_BROKERS: dict[str, BrokerProfile] = {p.name: p for p in [
    BrokerProfile("Generic UK broker (spread only)", "MT4 / MT5", "Europe/Athens", notes=PLACEHOLDER_NOTE,
                  data_access="Export bars from MetaTrader, or use the MetaTrader 5 Python link (Windows)."),
    BrokerProfile("Generic UK broker (raw spread + commission)", "MT4 / MT5 / cTrader", "Europe/Athens",
                  commission_model="per_lot_round_turn", commission_value=5.0,
                  spreads_pips={"EURUSD": 0.2, "GBPUSD": 0.4, "USDJPY": 0.3, "EURGBP": 0.4, "GBPJPY": 0.9},
                  default_spread_pips=0.6, notes=PLACEHOLDER_NOTE,
                  data_access="Export bars from MetaTrader/cTrader, or the MetaTrader 5 Python link (Windows)."),
    BrokerProfile("OANDA", "OANDA web/app, MT4, TradingView", "UTC", notes=PLACEHOLDER_NOTE,
                  data_access="v20 REST API with a free practice (demo) account key - planned downloader."),
    BrokerProfile("IG", "IG web platform, MT4, ProRealTime, TradingView", "Europe/London", notes=PLACEHOLDER_NOTE,
                  data_access="IG REST API with a demo account (weekly historical-data allowance), or MT4 export."),
    BrokerProfile("Pepperstone", "MT4, MT5, cTrader, TradingView", "Europe/Athens", notes=PLACEHOLDER_NOTE,
                  data_access="MetaTrader 5 Python link (Windows) or platform CSV export."),
    BrokerProfile("CMC Markets", "CMC web platform, MT4", "Europe/London", notes=PLACEHOLDER_NOTE,
                  data_access="MT4 history export."),
    BrokerProfile("FXCM", "Trading Station, MT4, TradingView", "UTC", notes=PLACEHOLDER_NOTE,
                  data_access="Platform CSV export or MT4 export."),
    BrokerProfile("Interactive Brokers", "Trader Workstation (TWS)", "Europe/London",
                  commission_model="percent_per_side", commission_value=0.002, default_spread_pips=0.3,
                  spreads_pips={"EURUSD": 0.2, "GBPUSD": 0.3, "USDJPY": 0.3, "EURGBP": 0.3, "GBPJPY": 0.6},
                  notes=PLACEHOLDER_NOTE, data_access="TWS API historical data (requires TWS running)."),
    BrokerProfile("Custom", "Other", "UTC", notes="Fill in your own broker's details."),
]}


def all_profiles(user_profiles: dict) -> dict[str, BrokerProfile]:
    out = dict(BUILTIN_BROKERS)
    for name, d in (user_profiles or {}).items():
        out[name] = BrokerProfile.from_dict({**d, "name": name})
    return out
