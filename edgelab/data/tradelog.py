"""Import a trade log (any broker/platform) via a column-mapping step, and compute P&L and R."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from edgelab.data.loader import guess_mapping
from edgelab.data.timeparse import parse_timestamps
from edgelab.data.validate import Issue
from edgelab.fx import FxConverter
from edgelab.instruments import get_instrument, normalize_symbol

REQUIRED = ["entry_time", "exit_time", "entry_price", "exit_price", "size", "side"]
OPTIONAL = ["symbol", "fees", "stop_price", "setup", "notes"]
ALL_FIELDS = REQUIRED + OPTIONAL

TRADE_ALIASES = {
    "entry_time": ["entry_time", "entry time", "open time", "opened", "open date", "entry date", "time in",
                   "date in", "entry", "open_time", "opentime"],
    "exit_time": ["exit_time", "exit time", "close time", "closed", "close date", "exit date", "time out",
                  "date out", "exit", "close_time", "closetime"],
    "entry_price": ["entry_price", "entry price", "open price", "price in", "avg entry", "entry px", "open_price",
                    "openprice"],
    "exit_price": ["exit_price", "exit price", "close price", "price out", "avg exit", "exit px", "close_price",
                   "closeprice"],
    "size": ["size", "quantity", "qty", "units", "amount", "volume", "lots", "position size", "contracts"],
    "side": ["side", "direction", "type", "action", "buy/sell", "long/short", "position"],
    "symbol": ["symbol", "instrument", "pair", "ticker", "market", "asset", "item"],
    "fees": ["fees", "fee", "commission", "commissions", "costs", "cost", "swap", "fees & commissions"],
    "stop_price": ["stop_price", "stop", "stop loss", "s/l", "sl", "initial stop"],
    "setup": ["setup", "tag", "strategy", "playbook", "pattern"],
    "notes": ["notes", "note", "comment", "comments", "remarks"],
}

LONG_WORDS = {"long", "buy", "b", "bot", "bought", "l", "1", "+1", "buy to open", "bto"}
SHORT_WORDS = {"short", "sell", "s", "sld", "sold", "-1", "sell short", "sell to open", "sto", "ss"}


def guess_trade_mapping(columns) -> dict[str, str | None]:
    return guess_mapping_generic(columns, TRADE_ALIASES)


def guess_mapping_generic(columns, aliases) -> dict[str, str | None]:
    norm = {str(c).strip().lower(): c for c in columns}
    out, used = {}, set()
    for fld, names in aliases.items():
        out[fld] = None
        for a in names:
            c = norm.get(a)
            if c is not None and c not in used:
                out[fld] = c
                used.add(c)
                break
    return out


def normalize_side(v) -> str | None:
    s = str(v).strip().lower()
    if s in LONG_WORDS:
        return "long"
    if s in SHORT_WORDS:
        return "short"
    return None


@dataclass
class TradeBuildResult:
    trades: pd.DataFrame
    issues: list[Issue] = field(default_factory=list)


def compute_pnl(trades: pd.DataFrame, account_ccy: str = "GBP", conv: FxConverter | None = None) -> pd.DataFrame:
    """Add pnl (account currency, after fees), r_multiple (if a stop is given) and rate_source.

    The quote-currency profit is converted at the exchange rate at the EXIT time. The trade's own exit
    price is used where it is the conversion rate (e.g. USD profit on GBPUSD for a GBP account).
    R is computed with the same rate, so it reflects the price move and fees only.
    """
    conv = conv or FxConverter()
    t = trades.copy()
    direction = np.where(t["side"] == "long", 1.0, -1.0)
    gross_quote = (t["exit_price"] - t["entry_price"]) * t["size"] * direction
    fees = t["fees"].fillna(0.0).to_numpy(float) if "fees" in t else np.zeros(len(t))
    stops = t["stop_price"] if "stop_price" in t else pd.Series(np.nan, index=t.index)
    pnl, risk, src = [], [], []
    for sym, g, px, entry, stop, size, when in zip(t["symbol"], gross_quote, t["exit_price"], t["entry_price"],
                                                    stops, t["size"], t["exit_time"]):
        inst = get_instrument(sym)
        hints = {inst.symbol.replace("USDT", "USD").replace("USDC", "USD"): px}
        look = conv.lookup(inst.quote, account_ccy, when, hints)
        src.append(look.source)
        if look.rate is None:
            pnl.append(np.nan)
            risk.append(np.nan)
            continue
        pnl.append(g * look.rate)
        risk.append(abs(entry - stop) * size * look.rate if pd.notna(stop) and stop != entry else np.nan)
    t["pnl"] = np.asarray(pnl, dtype=float) - fees
    t["r_multiple"] = t["pnl"] / np.asarray(risk, dtype=float)
    t["rate_source"] = src
    t["pnl_currency"] = account_ccy
    return t


def build_trades(raw: pd.DataFrame, mapping: dict[str, str | None], source_tz: str = "UTC",
                 default_symbol: str = "", dayfirst: bool | None = None, account_ccy: str = "GBP",
                 conv: FxConverter | None = None) -> TradeBuildResult:
    missing = [f for f in REQUIRED if not mapping.get(f)]
    if missing:
        raise ValueError(f"Please choose which column holds: {', '.join(missing)}")
    if not mapping.get("symbol") and not default_symbol:
        raise ValueError("Your file has no symbol column - please type the symbol these trades are for.")

    issues: list[Issue] = []
    t = pd.DataFrame(index=raw.index)
    et = parse_timestamps(raw[mapping["entry_time"]], source_tz, dayfirst)
    xt = parse_timestamps(raw[mapping["exit_time"]], source_tz, dayfirst)
    t["entry_time"] = et.ts
    t["exit_time"] = xt.ts
    for f in ("entry_price", "exit_price", "size", "fees", "stop_price"):
        if mapping.get(f):
            col = raw[mapping[f]].astype(str).str.replace(r"[,$£€\s]", "", regex=True)
            t[f] = pd.to_numeric(col, errors="coerce")
        else:
            t[f] = np.nan if f == "stop_price" else (0.0 if f == "fees" else np.nan)
    t["fees"] = t["fees"].abs().fillna(0.0)
    t["side"] = raw[mapping["side"]].map(normalize_side)
    # Some platforms encode side as the sign of the quantity
    neg = t["size"] < 0
    if neg.any():
        t.loc[neg & t["side"].isna(), "side"] = "short"
        t["size"] = t["size"].abs()
    t["symbol"] = raw[mapping["symbol"]].astype(str).map(normalize_symbol) if mapping.get("symbol") else normalize_symbol(default_symbol)
    for f in ("setup", "notes"):
        t[f] = raw[mapping[f]].astype(str) if mapping.get(f) else ""

    problems = {
        "bad_time": t["entry_time"].isna() | t["exit_time"].isna(),
        "bad_price": t["entry_price"].isna() | t["exit_price"].isna() | (t["entry_price"] <= 0) | (t["exit_price"] <= 0),
        "bad_size": t["size"].isna() | (t["size"] <= 0),
        "bad_side": t["side"].isna(),
    }
    order = (t["exit_time"] < t["entry_time"]) & ~problems["bad_time"]
    text = {
        "bad_time": ("Unreadable entry/exit times", "Check the date format and the timestamp columns."),
        "bad_price": ("Missing or invalid prices", "Entry and exit prices must be positive numbers."),
        "bad_size": ("Missing or invalid size", "Position size must be a positive number."),
        "bad_side": ("Unknown trade direction", "Side should be words like Buy/Sell or Long/Short."),
    }
    bad_any = pd.Series(False, index=t.index)
    for code, mask in problems.items():
        if mask.any():
            title, fix = text[code]
            issues.append(Issue("error", code, title, f"{int(mask.sum())} trades affected; they will be skipped.",
                                fix, int(mask.sum()), [f"row {i + 2}" for i in t.index[mask][:5]]))
            bad_any |= mask
    if order.any():
        issues.append(Issue("error", "exit_before_entry", "Exit before entry",
                            f"{int(order.sum())} trades exit before they enter. Entry and exit columns may be "
                            "swapped. They will be skipped.", "", int(order.sum())))
        bad_any |= order
    if t["stop_price"].isna().all():
        issues.append(Issue("info", "no_stops", "No stop prices",
                            "Without the initial stop, results can't be measured in R (multiples of the amount "
                            "risked). Dollar P&L still works. Add a stop column to unlock R-based stats."))
    good = t[~bad_any].copy()
    conv = conv or FxConverter()
    good = compute_pnl(good, account_ccy, conv)
    unconverted = good["pnl"].isna()
    if unconverted.any():
        issues.append(Issue("warning", "no_conversion", "P&L currency conversion not possible",
                            f"{int(unconverted.sum())} trades have profit in a currency with no exchange rate to "
                            f"{account_ccy}. Add a reference rate in Settings. Their P&L is left blank.", ""))
    n_ref = int((good["rate_source"] == "reference").sum())
    if n_ref:
        issues.append(Issue("info", "reference_rates", f"Converted to {account_ccy} with reference rates",
                            f"{n_ref} trades were converted using the fixed reference rates in Settings "
                            f"({conv.describe_reference()}), because none of your own price data covers their "
                            "exit dates. Import GBPUSD (and USDJPY) data for those dates for exact conversions.",
                            "Optional: import matching price data, then re-import the log."))
    if len(good) < 30:
        issues.append(Issue("info", "small_sample", "Small sample",
                            f"Only {len(good)} valid trades. Statistics from fewer than ~30 trades (and ideally "
                            "100+) are dominated by luck.", ""))
    return TradeBuildResult(good.reset_index(drop=True), issues)
