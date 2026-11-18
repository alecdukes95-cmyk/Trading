"""Background service that keeps paper trades up to date: polls quotes and lets the engine react to them."""
from __future__ import annotations

import logging
import threading

import pandas as pd

from edgelab.config import db_path, load_settings
from edgelab.data.store import Store
from edgelab.live.notify import desktop_notify
from edgelab.paper.engine import Env, PaperBook

log = logging.getLogger(__name__)
CONVERSION_PAIRS = ["GBPUSD", "USDJPY", "EURUSD", "USDCHF", "USDCAD", "AUDUSD"]


class PaperService:
    def __init__(self, path=None):
        self.store = Store(path or db_path())
        self.book = PaperBook(self.store.path, trade_store=self.store)
        self.feed = None
        self.watch: set = set()
        self.quotes: dict = {}
        self.events: list = []        # recent engine events, newest last (for the UI)
        self.errors: list = []
        self.last_update: pd.Timestamp | None = None
        self.interval = 2.0
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def env(self, settings=None) -> Env:
        s = settings or load_settings()
        mids = {k: (q["bid"] + q["ask"]) / 2 for k, q in self.quotes.items()}
        return Env(s.broker(), s.account_currency, dict(s.reference_rates), mids)

    def start(self, feed, interval: float = 2.0) -> None:
        self.stop()
        self.feed, self.interval = feed, interval
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="edgelab-paper")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None

    def tick(self) -> list:
        """One update: get quotes, let the engine process them. (Called by the thread; callable from tests.)"""
        s = load_settings()
        if self.feed is None:
            return []
        if not self.feed.is_live:
            self.feed.advance()
        need = set(self.watch) | self.book.symbols_in_use() | set(CONVERSION_PAIRS)
        q = self.feed.quotes(sorted(need))
        with self._lock:
            self.quotes.update(q)
            self.last_update = self.feed.now()
        env = self.env(s)
        new = []
        for sym in self.book.symbols_in_use():
            if sym in q:
                new += self.book.on_price(sym, q[sym]["bid"], q[sym]["ask"], self.feed.now(), env, self.quotes)
        if new:
            with self._lock:
                self.events = (self.events + [{**e, "at": self.feed.now()} for e in new])[-50:]
            if s.notify_desktop:
                for e in new:
                    desktop_notify(f"Paper trade: {e['type']}", describe(e, env.ccy))
        return new

    def _run(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001
                with self._lock:
                    self.errors = (self.errors + [f"{pd.Timestamp.now(tz='Europe/London'):%H:%M:%S} {e}"])[-20:]
                log.warning("paper feed error: %s", e)
            self._stop.wait(self.interval)


def describe(e: dict, ccy: str = "GBP") -> str:
    sym = e.get("symbol", "")
    cur = "£" if ccy == "GBP" else ccy + " "
    if e["type"] == "filled":
        return f"{sym}: your {e['side']} order filled at {e['price']}."
    if e["type"] == "expired":
        return f"{sym}: your pending order expired unfilled."
    pnl, pips = e.get("pnl"), e.get("pips")
    return (f"{sym}: {e['type']} - closed at {e.get('price')}"
            + (f", {pips:+.1f} pips" if pips is not None else "")
            + (f", {cur}{pnl:+,.2f}" if pnl is not None else "")
            + (f" ({e['r']:+.2f} R)" if e.get("r") is not None else "") + ".")
