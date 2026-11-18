"""Position size, pip value, leverage/margin and trading-cost maths - all in the ACCOUNT currency.

Worked example (GBP account, GBPUSD = 1.25, 1 mini lot = 10,000 units of EURUSD at 1.1000, 30:1):
* Pip value: 0.0001 x 10,000 = $1.00 per pip  ->  $1.00 / 1.25 = GBP 0.80 per pip
* Notional:  10,000 EUR x 1.1000 = $11,000    ->  GBP 8,800
* Required margin at 30:1: GBP 8,800 / 30 = GBP 293.33 (3.33% of the position value)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from edgelab.brokers import STANDARD_LOT, BrokerProfile
from edgelab.fx import FxConverter
from edgelab.instruments import Instrument

MINI_LOT = 10_000


def _hint(inst: Instrument, price: float) -> dict:
    return {inst.symbol.replace("USDT", "USD").replace("USDC", "USD"): price}


def value_per_price_unit(inst: Instrument, units: float, price: float, conv: FxConverter, ccy: str, at=None) -> float | None:
    """Account-currency P&L for a 1.0 move in price (e.g. 1.1000 -> 1.2000 on EURUSD) on `units`."""
    return conv.convert(units, inst.quote, ccy, at, hints=_hint(inst, price))


def pip_value(inst: Instrument, units: float, price: float, conv: FxConverter, ccy: str, at=None) -> float | None:
    v = value_per_price_unit(inst, units, price, conv, ccy, at)
    return None if v is None else v * inst.pip


def notional(inst: Instrument, units: float, price: float, conv: FxConverter, ccy: str, at=None) -> float | None:
    """Value of the position in the account currency."""
    return conv.convert(units * price, inst.quote, ccy, at, hints=_hint(inst, price))


def leverage_for(inst: Instrument, broker: BrokerProfile) -> float:
    return {"fx_major": broker.leverage_fx_major, "fx_minor": broker.leverage_fx_minor,
            "crypto": broker.leverage_crypto}[inst.leverage_class]


def margin_required(inst, units, price, conv, ccy, broker, at=None) -> float | None:
    n = notional(inst, units, price, conv, ccy, at)
    return None if n is None else n / leverage_for(inst, broker)


@dataclass
class TradeCosts:
    spread: float
    slippage: float
    commission: float
    fees: float

    @property
    def total(self) -> float:
        return self.spread + self.slippage + self.commission + self.fees


def trade_costs(inst: Instrument, units: float, price: float, broker: BrokerProfile, conv: FxConverter, ccy: str,
                at=None) -> TradeCosts | None:
    """Round-trip cost of opening and closing a position, in the account currency."""
    n = notional(inst, units, price, conv, ccy, at)
    if n is None:
        return None
    if inst.is_forex:
        pv = pip_value(inst, units, price, conv, ccy, at)
        spread = broker.spread_for(inst.symbol) * pv
        slip = 2 * broker.slippage_pips * pv
        lots = units / STANDARD_LOT
        m, v = broker.commission_model, broker.commission_value
        comm = {"none": 0.0, "per_lot_round_turn": v * lots, "per_lot_per_side": 2 * v * lots,
                "per_trade": v, "percent_per_side": 2 * v / 100 * n}.get(m, 0.0)
        return TradeCosts(spread, slip, comm, 0.0)
    spread = broker.crypto_spread_pct / 100 * n
    slip = 2 * broker.crypto_slippage_pct / 100 * n
    fees = 2 * broker.crypto_fee_pct / 100 * n
    return TradeCosts(spread, slip, 0.0, fees)


def units_for_risk(risk_amount: float, stop_distance: float, value_per_unit_move: float, step: int = 1_000) -> int:
    """Largest position (rounded DOWN to `step` units) whose stop-loss loses at most risk_amount.

    value_per_unit_move: account-currency value of a 1.0 price move per 1 unit.
    """
    if stop_distance <= 0 or value_per_unit_move <= 0:
        return 0
    raw = risk_amount / (stop_distance * value_per_unit_move)
    return int(math.floor(raw / step) * step) if step >= 1 else raw


@dataclass
class PositionReport:
    units: float
    lots: float
    pip_value: float | None
    notional: float
    leverage: float
    margin_required: float
    margin_pct: float
    equity: float
    used_margin: float
    free_margin: float
    margin_level_pct: float | None
    closeout_loss: float            # loss that would trigger the broker's margin close-out
    closeout_distance: float | None  # adverse price move (price units) that would trigger it
    stop_distance: float | None
    price_risk: float | None         # loss at the stop, before costs
    costs: TradeCosts
    total_risk: float | None         # loss at the stop, including round-trip costs
    risk_pct: float | None
    risk_limit: float
    max_stop_for_limit: float | None  # widest stop (price units) keeping total risk within the limit
    units_for_limit: int | None       # size that risks exactly the limit with this stop
    reward: float | None
    rr: float | None
    cost_in_r: float | None
    warnings: list[str] = field(default_factory=list)


def analyse_position(inst: Instrument, units: float, price: float, *, balance: float, risk_pct: float,
                     broker: BrokerProfile, conv: FxConverter, ccy: str, stop_distance: float | None = None,
                     target_distance: float | None = None, other_margin_used: float = 0.0,
                     unrealised_pnl: float = 0.0, at=None) -> PositionReport:
    vpu = value_per_price_unit(inst, 1.0, price, conv, ccy, at)  # per unit, per 1.0 price move
    if vpu is None:
        raise ValueError(f"No exchange rate available to convert {inst.quote} into {ccy}. Add one in Settings.")
    move_value = vpu * units
    n = notional(inst, units, price, conv, ccy, at)
    lev = leverage_for(inst, broker)
    margin = n / lev
    equity = balance + unrealised_pnl
    used = other_margin_used + margin
    free = equity - used
    level = equity / used * 100 if used > 0 else None
    closeout_loss = equity - broker.margin_closeout_pct / 100 * used
    closeout_distance = closeout_loss / move_value if move_value > 0 else None
    costs = trade_costs(inst, units, price, broker, conv, ccy, at)
    risk_limit = balance * risk_pct / 100

    price_risk = total_risk = rpct = max_stop = u_limit = reward = rr = cost_r = None
    if stop_distance and stop_distance > 0:
        price_risk = stop_distance * move_value
        total_risk = price_risk + costs.total
        rpct = total_risk / balance * 100 if balance > 0 else None
        cost_per_unit = costs.total / units if units else 0.0
        max_stop = (risk_limit - costs.total) / move_value if move_value > 0 else None
        # units such that units * (stop * vpu + cost_per_unit) <= limit
        denom = stop_distance * vpu + cost_per_unit
        if denom > 0:
            raw_units = risk_limit / denom
            u_limit = (int(math.floor(raw_units / broker.min_units) * broker.min_units) if inst.is_forex
                       else math.floor(raw_units * 10_000) / 10_000)
        cost_r = costs.total / price_risk if price_risk else None
    if target_distance and target_distance > 0:
        reward = target_distance * move_value - costs.total
        if price_risk:
            rr = reward / total_risk

    w = []
    if free < 0:
        w.append(f"Not enough free margin: this position needs {margin:,.2f} but only {equity - other_margin_used:,.2f} "
                 "is free. The broker would reject the order.")
    elif level is not None and level < 200:
        w.append(f"Margin level would be only {level:.0f}%. A small move against you could trigger a margin call "
                 f"(close-out starts at {broker.margin_closeout_pct:.0f}%).")
    if rpct is not None and total_risk > risk_limit * 1.0001:
        w.append(f"This trade risks {rpct:.2f}% of your balance, above your {risk_pct:.2f}% rule. Use "
                 f"{u_limit:,} {'units' if inst.is_forex else 'coins'} or a stop of at most {max(max_stop or 0, 0) / inst.pip if inst.is_forex else max(max_stop or 0, 0):,.1f} "
                 f"{'pips' if inst.is_forex else 'points'}.")
    if max_stop is not None and max_stop <= 0:
        w.append("Costs alone exceed your risk limit at this size - the trade can't fit your rule.")
    if reward is not None and target_distance and costs.total > 0.1 * target_distance * move_value:
        w.append(f"Costs take {costs.total / (target_distance * move_value) * 100:.0f}% of the target profit. "
                 "Small targets are hard to profit from after costs.")
    if not inst.is_forex and lev > 1:
        w.append("UK retail clients cannot be sold crypto derivatives (CFDs, spread bets, futures, options) under FCA "
                 "rules, so leveraged crypto is normally not available to you.")
    return PositionReport(units, units / STANDARD_LOT, move_value * inst.pip if inst.is_forex else None, n, lev, margin,
                          100 / lev, equity, used, free, level, closeout_loss, closeout_distance, stop_distance,
                          price_risk, costs, total_risk, rpct, risk_limit, max_stop, u_limit, reward, rr, cost_r, w)
