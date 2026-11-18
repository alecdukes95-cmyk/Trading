"""Paper trading engine: fake trades filled against REAL bid/ask prices, entirely inside EdgeLab.

Nothing here talks to a broker's order system - not even a practice account. Prices come in (from OANDA /
MetaTrader 5 quotes, or a replay of stored history) and this engine decides what would have happened:

* Market orders fill at the current ASK (buy) or BID (sell), so you pay the real spread.
* Limit / stop orders fill when the quote touches the price (buy uses ask, sell uses bid). A stop that is
  jumped over fills at the worse current price, as in real markets.
* Stop-loss / take-profit are checked on every quote (long: bid; short: ask).
* Margin: positions need notional / leverage; orders that would take free margin below zero are rejected,
  and if equity falls to the broker's close-out level (UK retail: 50%) everything is closed.
* Commission from your broker profile is charged when a position closes.
* Every closed paper trade is written to your trade log (source 'paper') - never to your real balance.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field

import pandas as pd

from edgelab.brokers import STANDARD_LOT, BrokerProfile
from edgelab.fx import FxConverter
from edgelab.instruments import get_instrument
from edgelab.risk import margin_required

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS paper_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_ts TEXT, symbol TEXT, side TEXT, order_type TEXT, units REAL,
    price REAL, sl REAL, tp REAL, expiry_ts TEXT, status TEXT, status_ts TEXT, reason TEXT, position_id INTEGER,
    plan TEXT
);
CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, symbol TEXT, side TEXT, units REAL, open_ts TEXT,
    open_price REAL, sl REAL, tp REAL, initial_sl REAL, status TEXT, close_ts TEXT, close_price REAL,
    close_reason TEXT, pnl REAL, r REAL, fees REAL, best REAL, worst REAL, plan TEXT, review TEXT, events TEXT,
    trade_id INTEGER
);
"""


@dataclass
class Env:
    """What the engine needs to value positions: costs/leverage, currency conversion and the latest mid prices."""
    broker: BrokerProfile
    ccy: str = "GBP"
    reference_rates: dict = field(default_factory=dict)
    mids: dict = field(default_factory=dict)      # symbol -> latest mid price (used for conversions)

    def converter(self) -> FxConverter:
        conv = FxConverter(reference=dict(self.reference_rates)) if self.reference_rates else FxConverter()
        return conv


def _ts(t) -> str:
    t = pd.Timestamp(t)
    return (t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")).isoformat()


def _pt(s):
    return None if s in (None, "") else pd.Timestamp(s)


class PaperBook:
    def __init__(self, path, trade_store=None):
        self.path = path
        self.trade_store = trade_store        # edgelab Store, to write closed trades into the trade log
        with self._conn() as c:
            c.executescript(SCHEMA)
            cols = {r[1] for r in c.execute("PRAGMA table_info(paper_positions)")}
            if "trade_id" not in cols:
                c.execute("ALTER TABLE paper_positions ADD COLUMN trade_id INTEGER")
        if self.meta("start_balance") is None:
            self.reset(10_000.0, "GBP")

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ account
    def meta(self, key):
        with self._conn() as c:
            r = c.execute("SELECT value FROM paper_meta WHERE key = ?", (key,)).fetchone()
        return None if r is None else r["value"]

    def set_meta(self, key, value):
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO paper_meta VALUES (?, ?)", (key, str(value)))

    def reset(self, start_balance: float, currency: str = "GBP") -> None:
        with self._conn() as c:
            c.execute("DELETE FROM paper_orders")
            c.execute("DELETE FROM paper_positions")
        self.set_meta("start_balance", float(start_balance))
        self.set_meta("currency", currency)
        self.set_meta("created", _ts(pd.Timestamp.now(tz="UTC")))
        if self.trade_store is not None:
            self.trade_store.delete_trades("paper")

    @property
    def start_balance(self) -> float:
        return float(self.meta("start_balance") or 0)

    def balance(self) -> float:
        with self._conn() as c:
            r = c.execute("SELECT COALESCE(SUM(pnl), 0) AS p FROM paper_positions WHERE status = 'closed'").fetchone()
        return self.start_balance + float(r["p"])

    # ------------------------------------------------------------------ queries
    def positions(self, status: str | None = "open") -> list[dict]:
        q = "SELECT * FROM paper_positions" + (" WHERE status = ?" if status else "") + " ORDER BY id"
        with self._conn() as c:
            rows = [dict(r) for r in c.execute(q, (status,) if status else ())]
        for r in rows:
            r["plan"] = json.loads(r["plan"] or "{}")
            r["review"] = json.loads(r["review"] or "{}")
            r["events"] = json.loads(r["events"] or "[]")
        return rows

    def orders(self, status: str | None = "pending") -> list[dict]:
        q = "SELECT * FROM paper_orders" + (" WHERE status = ?" if status else "") + " ORDER BY id"
        with self._conn() as c:
            rows = [dict(r) for r in c.execute(q, (status,) if status else ())]
        for r in rows:
            r["plan"] = json.loads(r["plan"] or "{}")
        return rows

    def position(self, pid: int) -> dict | None:
        rows = [p for p in self.positions(None) if p["id"] == pid]
        return rows[0] if rows else None

    def symbols_in_use(self) -> set:
        return {p["symbol"] for p in self.positions()} | {o["symbol"] for o in self.orders()}

    # ------------------------------------------------------------------ valuation
    def _conv_hints(self, env: Env, extra: dict | None = None) -> dict:
        h = dict(env.mids)
        h.update(extra or {})
        return h

    def quote_to_ccy(self, amount: float, symbol: str, price: float, env: Env) -> float | None:
        inst = get_instrument(symbol)
        conv = env.converter()
        return conv.convert(amount, inst.quote, env.ccy, hints=self._conv_hints(env, {inst.symbol: price}))

    def unrealised(self, pos: dict, bid: float, ask: float, env: Env) -> tuple[float | None, float]:
        """(P&L in account currency, P&L in pips) marked at the price you could close at."""
        inst = get_instrument(pos["symbol"])
        d = 1 if pos["side"] == "long" else -1
        mark = bid if d > 0 else ask
        q = (mark - pos["open_price"]) * pos["units"] * d
        return self.quote_to_ccy(q, pos["symbol"], mark, env), (mark - pos["open_price"]) * d / inst.pip

    def risk_ccy(self, pos: dict, env: Env) -> float | None:
        if pos.get("initial_sl") is None:
            return None
        q = abs(pos["open_price"] - pos["initial_sl"]) * pos["units"]
        return self.quote_to_ccy(q, pos["symbol"], pos["open_price"], env)

    def commission(self, symbol: str, units: float, price: float, env: Env) -> float:
        b = env.broker
        lots = units / STANDARD_LOT
        m, v = b.commission_model, b.commission_value
        if m == "per_lot_round_turn":
            return v * lots
        if m == "per_lot_per_side":
            return 2 * v * lots
        if m == "per_trade":
            return v
        if m == "percent_per_side":
            n = self.quote_to_ccy(units * price, symbol, price, env) or 0.0
            return 2 * v / 100 * n
        return 0.0

    def snapshot(self, quotes: dict, env: Env) -> dict:
        """Balance, equity, margin and live P&L of every open position."""
        bal = self.balance()
        unreal, used, rows = 0.0, 0.0, []
        for p in self.positions():
            q = quotes.get(p["symbol"])
            if q is None:
                rows.append({**p, "pnl_live": None, "pips_live": None, "r_live": None})
                continue
            pnl, pips = self.unrealised(p, q["bid"], q["ask"], env)
            mid = (q["bid"] + q["ask"]) / 2
            m = margin_required(get_instrument(p["symbol"]), p["units"], mid, env.converter(), env.ccy, env.broker)
            used += m or 0.0
            unreal += pnl or 0.0
            risk = self.risk_ccy(p, env)
            rows.append({**p, "pnl_live": pnl, "pips_live": pips, "r_live": (pnl / risk) if pnl is not None and risk else None,
                         "margin": m, "mark": q["bid"] if p["side"] == "long" else q["ask"]})
        equity = bal + unreal
        return {"balance": bal, "unrealised": unreal, "equity": equity, "used_margin": used,
                "free_margin": equity - used, "margin_level": (equity / used * 100) if used > 0 else None,
                "positions": rows, "currency": env.ccy}

    # ------------------------------------------------------------------ orders
    def place_order(self, symbol: str, side: str, order_type: str, units: float, *, quote: dict | None, env: Env,
                    now, price: float | None = None, sl: float | None = None, tp: float | None = None,
                    expiry_minutes: int | None = None, plan: dict | None = None,
                    all_quotes: dict | None = None) -> dict:
        """Returns {'ok', 'message', 'order_id', 'position_id'}."""
        symbol = symbol.upper()
        inst = get_instrument(symbol)
        side, order_type = side.lower(), order_type.lower()
        d = 1 if side == "long" else -1
        if units <= 0:
            return {"ok": False, "message": "Size must be above zero."}
        if order_type == "market":
            if not quote or not quote.get("tradeable", True):
                return {"ok": False, "message": "No live price right now (market closed or feed not running)."}
            ref = quote["ask"] if d > 0 else quote["bid"]
        else:
            if price is None or price <= 0:
                return {"ok": False, "message": "Limit and stop orders need a price."}
            ref = price
            if quote:
                cur = quote["ask"] if d > 0 else quote["bid"]
                wrong = (order_type == "limit" and ((d > 0 and price >= cur) or (d < 0 and price <= cur))) or \
                        (order_type == "stop" and ((d > 0 and price <= cur) or (d < 0 and price >= cur)))
                if wrong:
                    kind = {"limit": "below" if d > 0 else "above", "stop": "above" if d > 0 else "below"}[order_type]
                    return {"ok": False, "message": f"A {side} {order_type} order must be {kind} the current price "
                                                    f"({inst.fmt_price(cur)}). Use a market order to trade now."}
        if sl is not None and ((d > 0 and sl >= ref) or (d < 0 and sl <= ref)):
            return {"ok": False, "message": "The stop-loss is on the wrong side of the entry price."}
        if tp is not None and ((d > 0 and tp <= ref) or (d < 0 and tp >= ref)):
            return {"ok": False, "message": "The take-profit is on the wrong side of the entry price."}
        # margin check at the entry price
        snap = self.snapshot(all_quotes or ({symbol: quote} if quote else {}), env)
        need = margin_required(inst, units, ref, env.converter(), env.ccy, env.broker)
        if need is None:
            return {"ok": False, "message": f"No exchange rate to value {inst.quote} in {env.ccy}."}
        if snap["free_margin"] - need < 0:
            return {"ok": False, "message": f"Rejected: needs {need:,.2f} margin but only "
                                            f"{snap['free_margin']:,.2f} is free."}
        expiry = _ts(pd.Timestamp(now) + pd.Timedelta(minutes=expiry_minutes)) if expiry_minutes else None
        with self._conn() as c:
            cur = c.execute("INSERT INTO paper_orders (created_ts, symbol, side, order_type, units, price, sl, tp, "
                            "expiry_ts, status, status_ts, plan) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (_ts(now), symbol, side, order_type, float(units), price, sl, tp, expiry, "pending",
                             _ts(now), json.dumps(plan or {})))
            oid = int(cur.lastrowid)
        if order_type == "market":
            pid = self._fill(oid, ref, now)
            return {"ok": True, "message": f"Filled {side} {units:,.0f} {symbol} at {inst.fmt_price(ref)}.",
                    "order_id": oid, "position_id": pid}
        return {"ok": True, "message": f"{order_type.title()} order placed: {side} {units:,.0f} {symbol} at "
                                       f"{inst.fmt_price(price)}.", "order_id": oid, "position_id": None}

    def _fill(self, oid: int, fill_price: float, now) -> int:
        o = [x for x in self.orders(None) if x["id"] == oid][0]
        ev = [{"t": _ts(now), "what": "opened", "price": fill_price}]
        with self._conn() as c:
            cur = c.execute("INSERT INTO paper_positions (order_id, symbol, side, units, open_ts, open_price, sl, tp, "
                            "initial_sl, status, best, worst, plan, review, events) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (oid, o["symbol"], o["side"], o["units"], _ts(now), float(fill_price), o["sl"], o["tp"],
                             o["sl"], "open", 0.0, 0.0, json.dumps(o["plan"]), "{}", json.dumps(ev)))
            pid = int(cur.lastrowid)
            c.execute("UPDATE paper_orders SET status = 'filled', status_ts = ?, position_id = ? WHERE id = ?",
                      (_ts(now), pid, oid))
        return pid

    def cancel_order(self, oid: int, now, reason: str = "cancelled by you") -> None:
        with self._conn() as c:
            c.execute("UPDATE paper_orders SET status = 'cancelled', status_ts = ?, reason = ? WHERE id = ? AND "
                      "status = 'pending'", (_ts(now), reason, oid))

    def modify_position(self, pid: int, now, sl: float | None = "keep", tp: float | None = "keep") -> str:
        p = self.position(pid)
        if p is None or p["status"] != "open":
            return "Position not open."
        d = 1 if p["side"] == "long" else -1
        events, notes = p["events"], []
        new_sl = p["sl"] if sl == "keep" else sl
        new_tp = p["tp"] if tp == "keep" else tp
        if sl != "keep":
            old = p["sl"]
            if old is not None and new_sl is not None and (new_sl - old) * d < 0:
                notes.append("stop widened")   # moved further from the entry: classic loss-aversion habit
            if new_sl is None and old is not None:
                notes.append("stop removed")
            events.append({"t": _ts(now), "what": "stop moved", "from": old, "to": new_sl, "flags": notes})
        if tp != "keep":
            events.append({"t": _ts(now), "what": "target moved", "from": p["tp"], "to": new_tp})
        with self._conn() as c:
            c.execute("UPDATE paper_positions SET sl = ?, tp = ?, events = ? WHERE id = ?",
                      (new_sl, new_tp, json.dumps(events), pid))
        return "Updated" + (f" - note: {', '.join(notes)}" if notes else ".")

    def close_position(self, pid: int, price: float, now, reason: str, env: Env) -> dict | None:
        p = self.position(pid)
        if p is None or p["status"] != "open":
            return None
        inst = get_instrument(p["symbol"])
        d = 1 if p["side"] == "long" else -1
        q = (price - p["open_price"]) * p["units"] * d
        gross = self.quote_to_ccy(q, p["symbol"], price, env)
        fees = self.commission(p["symbol"], p["units"], price, env)
        pnl = (gross if gross is not None else 0.0) - fees
        risk = self.risk_ccy(p, env)
        r = pnl / risk if risk else None
        events = p["events"] + [{"t": _ts(now), "what": "closed", "price": price, "reason": reason}]
        with self._conn() as c:
            c.execute("UPDATE paper_positions SET status = 'closed', close_ts = ?, close_price = ?, close_reason = ?, "
                      "pnl = ?, r = ?, fees = ?, events = ? WHERE id = ?",
                      (_ts(now), float(price), reason, pnl, r, fees, json.dumps(events), pid))
        if self.trade_store is not None:
            tid = self.trade_store.add_trade({
                "symbol": p["symbol"], "side": p["side"], "entry_time": pd.Timestamp(p["open_ts"]),
                "exit_time": pd.Timestamp(_ts(now)), "entry_price": p["open_price"], "exit_price": float(price),
                "size": p["units"], "fees": fees, "stop_price": p["initial_sl"], "setup": p["plan"].get("setup", ""),
                "notes": p["plan"].get("why", ""), "pnl": pnl, "r_multiple": r, "pnl_currency": env.ccy}, "paper")
            plan = p["plan"] or {}
            self.trade_store.journal_upsert(tid, tags=[plan["setup"]] if plan.get("setup") else [],
                                            emotion=plan.get("emotion"), notes=plan.get("why"), planned_target=p["tp"])
            with self._conn() as c:
                c.execute("UPDATE paper_positions SET trade_id = ? WHERE id = ?", (tid, pid))
        return {"id": pid, "symbol": p["symbol"], "side": p["side"], "pnl": pnl, "r": r, "reason": reason,
                "price": price, "pips": (price - p["open_price"]) * d / inst.pip}

    def close_all(self, quotes: dict, now, reason: str, env: Env) -> list[dict]:
        out = []
        for p in self.positions():
            q = quotes.get(p["symbol"])
            if q:
                out.append(self.close_position(p["id"], q["bid"] if p["side"] == "long" else q["ask"], now, reason, env))
        return [x for x in out if x]

    def save_review(self, pid: int, review: dict) -> None:
        with self._conn() as c:
            c.execute("UPDATE paper_positions SET review = ? WHERE id = ?", (json.dumps(review), pid))
        p = self.position(pid)
        if self.trade_store is not None and p and p.get("trade_id"):
            self.trade_store.journal_upsert(int(p["trade_id"]), followed=review.get("followed"),
                                            grade=review.get("grade"), mistakes=review.get("mistakes", []),
                                            lesson=review.get("lesson"))

    # ------------------------------------------------------------------ the heart: process a new quote
    def on_price(self, symbol: str, bid: float, ask: float, now, env: Env, all_quotes: dict | None = None) -> list[dict]:
        """Apply one bid/ask update: trigger orders, expire them, hit stops/targets, check margin."""
        symbol = symbol.upper()
        events = []
        now_ts = pd.Timestamp(now)
        for o in self.orders():
            if o["symbol"] != symbol:
                continue
            if o["expiry_ts"] and now_ts >= pd.Timestamp(o["expiry_ts"]):
                self.cancel_order(o["id"], now, "expired")
                events.append({"type": "expired", "symbol": symbol, "order_id": o["id"]})
                continue
            d = 1 if o["side"] == "long" else -1
            px = ask if d > 0 else bid
            hit = (o["order_type"] == "limit" and ((d > 0 and px <= o["price"]) or (d < 0 and px >= o["price"]))) or \
                  (o["order_type"] == "stop" and ((d > 0 and px >= o["price"]) or (d < 0 and px <= o["price"])))
            if hit:
                if o["order_type"] == "limit":   # limits fill at your price or better
                    fill = min(px, o["price"]) if d > 0 else max(px, o["price"])
                else:                            # stops become market orders: fill at the current quote (slippage)
                    fill = px
                pid = self._fill(o["id"], fill, now)
                events.append({"type": "filled", "symbol": symbol, "order_id": o["id"], "position_id": pid,
                               "price": fill, "side": o["side"]})
        for p in self.positions():
            if p["symbol"] != symbol:
                continue
            d = 1 if p["side"] == "long" else -1
            mark = bid if d > 0 else ask
            inst = get_instrument(symbol)
            move = (mark - p["open_price"]) * d / inst.pip
            if move > (p["best"] or 0) or move < (p["worst"] or 0):
                with self._conn() as c:
                    c.execute("UPDATE paper_positions SET best = ?, worst = ? WHERE id = ?",
                              (max(p["best"] or 0, move), min(p["worst"] or 0, move), p["id"]))
            if p["sl"] is not None and ((d > 0 and mark <= p["sl"]) or (d < 0 and mark >= p["sl"])):
                res = self.close_position(p["id"], mark, now, "stop-loss", env)   # gap beyond the stop = worse fill
                events.append({"type": "stop-loss", **res})
            elif p["tp"] is not None and ((d > 0 and mark >= p["tp"]) or (d < 0 and mark <= p["tp"])):
                res = self.close_position(p["id"], p["tp"], now, "take-profit", env)
                events.append({"type": "take-profit", **res})
        # margin close-out
        quotes = dict(all_quotes or {})
        quotes[symbol] = {"bid": bid, "ask": ask}
        snap = self.snapshot(quotes, env)
        lvl = snap["margin_level"]
        if lvl is not None and lvl <= env.broker.margin_closeout_pct and len(quotes) >= len(self.symbols_in_use()):
            for res in self.close_all(quotes, now, "margin close-out", env):
                events.append({"type": "margin close-out", **res})
        return events
