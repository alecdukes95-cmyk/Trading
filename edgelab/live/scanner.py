"""Background scanner: watches your forex pairs and raises an alert when a pattern completes.

Two modes:
* live        - every few seconds, checks whether a new candle has closed for each pair/timeframe; if so it
                downloads the latest completed candles from OANDA (GET only), runs the detectors and alerts
                on patterns completed by that newest candle.
* simulation  - replays stored data (your OANDA downloads, or the synthetic samples) through the same
                pipeline at high speed, so you can see how alerts behave without a live connection.
                The detectors only ever see bars up to the simulated "now".

The scanner never places orders.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

import pandas as pd

from edgelab.config import api_key, db_path, load_settings
from edgelab.data.resample import resample_ohlcv
from edgelab.data.sessions import in_window, market_open_mask
from edgelab.data.store import Store
from edgelab.fx import FxConverter
from edgelab.instruments import get_instrument
from edgelab.live.alerts import build_payload, desktop_text, headline, history_before, passes_filters
from edgelab.live.notify import desktop_notify
from edgelab.patterns import analyse
from edgelab.timeutil import timeframe_minutes

log = logging.getLogger(__name__)


@dataclass
class ScannerStatus:
    running: bool = False
    mode: str = ""
    started_at: pd.Timestamp | None = None
    last_check: pd.Timestamp | None = None
    sim_clock: pd.Timestamp | None = None
    sim_end: pd.Timestamp | None = None
    cycles: int = 0
    alerts: int = 0
    errors: list = field(default_factory=list)
    sources: dict = field(default_factory=dict)   # (symbol, tf) -> description of the data used


def pick_dataset(store: Store, symbol: str) -> dict | None:
    """Prefer your own data (e.g. OANDA downloads, smallest timeframe), else the synthetic sample."""
    ds = store.list_datasets()
    if ds.empty:
        return None
    mine = ds[(ds["symbol"] == symbol) & (ds["is_sample"] == 0)]
    pool = mine if not mine.empty else ds[ds["symbol"] == symbol]
    if pool.empty:
        return None
    pool = pool.assign(m=pool["timeframe"].map(timeframe_minutes)).sort_values(["m", "n_bars"], ascending=[True, False])
    return pool.iloc[0].to_dict()


class ScannerService:
    def __init__(self, path=None):
        self.store = Store(path or db_path())
        self.status = ScannerStatus()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_bar: dict = {}          # (symbol, tf) -> last processed bar open time
        self._stats_cache: dict = {}       # (symbol, tf, day) -> (analysis, source)
        self.sim_seconds = 2.0
        self.sim_step_minutes = 15

    # ------------------------------------------------------------------ control
    def start(self, mode: str, sim_days: int = 15, sim_seconds: float = 2.0) -> None:
        self.stop()
        self._stop.clear()
        self.status = ScannerStatus(running=True, mode=mode, started_at=pd.Timestamp.now(tz="UTC"))
        self.sim_seconds = sim_seconds
        target = self._run_live if mode == "live" else self._run_sim
        self._thread = threading.Thread(target=target, args=() if mode == "live" else (sim_days,), daemon=True,
                                        name=f"edgelab-scanner-{mode}")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self.status.running = False

    def _error(self, msg: str) -> None:
        with self._lock:
            self.status.errors = (self.status.errors + [f"{pd.Timestamp.now(tz='Europe/London'):%H:%M:%S} {msg}"])[-20:]
        log.warning(msg)

    # ------------------------------------------------------------------ shared
    def _emit(self, analysis, sig, inst, tf: str, settings, mode: str, source: str, balance: float) -> bool:
        if not passes_filters(sig, analysis.ctx, settings):
            return False
        conv = FxConverter(reference=dict(settings.reference_rates))
        stats = history_before(analysis, sig.pattern, sig.direction, sig.i)
        pl = build_payload(sig, analysis.ctx, inst, tf, settings, balance, conv, stats, source, mode)
        aid = self.store.add_alert(mode, inst.symbol, tf, sig.pattern, sig.direction, sig.ts, pl)
        if aid is None:
            return False
        with self._lock:
            self.status.alerts += 1
        if settings.notify_desktop:
            desktop_notify(("[SIM] " if mode == "simulation" else "") + headline(pl)[:60], desktop_text(pl))
        return True

    def _balance(self, settings) -> float:
        from edgelab.account import summarise

        return summarise(self.store.load_balance_events(), self.store.load_trades(), settings.account_currency,
                         settings.account_balance).balance

    # ------------------------------------------------------------------ live (OANDA)
    def _history(self, symbol: str, tf: str, settings):
        """Analysis of your stored data for this pair/timeframe, used for 'how did this pattern do before'."""
        key = (symbol, tf, pd.Timestamp.now(tz="UTC").date())
        if key in self._stats_cache:
            return self._stats_cache[key]
        ds = pick_dataset(self.store, symbol)
        if ds is None:
            self._stats_cache[key] = (None, "no stored data")
            return self._stats_cache[key]
        inst = get_instrument(symbol)
        cfg = settings.session_for("forex")
        df = self.store.load_bars(int(ds["id"]))
        m = timeframe_minutes(tf)
        if timeframe_minutes(ds["timeframe"]) < m:
            df = resample_ohlcv(df, m, cfg)
        a = analyse(df, cfg, inst, m, settings.broker(), settings.target_r, settings.order_expiry_bars)
        src = f"{ds['name']}" + (" - SYNTHETIC, not real market data" if ds["is_sample"] else "")
        self._stats_cache[key] = (a, src)
        return self._stats_cache[key]

    def _run_live(self) -> None:
        from edgelab.data.providers.oanda import OandaClient

        while not self._stop.is_set():
            settings = load_settings()
            try:
                client = OandaClient(api_key("OANDA_API_KEY") or "", settings.oanda_environment)
            except Exception as e:  # noqa: BLE001
                self._error(str(e))
                self.status.running = False
                return
            now = pd.Timestamp.now(tz="UTC")
            balance = self._balance(settings)
            for sym in settings.watchlist:
                for tf in settings.scan_timeframes:
                    if self._stop.is_set():
                        return
                    m = timeframe_minutes(tf)
                    last = self._last_bar.get((sym, tf))
                    if last is not None and now < last + pd.Timedelta(minutes=2 * m) + pd.Timedelta(seconds=5):
                        continue   # the next candle hasn't closed yet
                    try:
                        self._scan_live_one(client, sym, tf, settings, balance)
                    except Exception as e:  # noqa: BLE001
                        self._error(f"{sym} {tf}: {e}")
            with self._lock:
                self.status.last_check = pd.Timestamp.now(tz="UTC")
                self.status.cycles += 1
            self._stop.wait(10)

    def _scan_live_one(self, client, sym: str, tf: str, settings, balance: float) -> None:
        inst = get_instrument(sym)
        cfg = settings.session_for("forex")
        m = timeframe_minutes(tf)
        end = pd.Timestamp.now(tz="UTC")
        df = client.candles(sym, tf, end - pd.Timedelta(days=6 if m <= 15 else 20), end, price="M")
        if len(df) < 60:
            return
        newest = df.index[-1]
        if self._last_bar.get((sym, tf)) == newest:
            return
        first_time = (sym, tf) not in self._last_bar
        self._last_bar[(sym, tf)] = newest
        if first_time:
            return  # don't alert on a candle that closed before the scanner started
        a = analyse(df, cfg, inst, m, settings.broker(), settings.target_r, settings.order_expiry_bars,
                    settings.enabled_patterns or None)
        hist, src = self._history(sym, tf, settings)
        for sig in [s for s in a.signals if s.i == len(df) - 1]:
            if not passes_filters(sig, a.ctx, settings):
                continue
            stats = history_before(hist, sig.pattern, sig.direction) if hist is not None else None
            conv = FxConverter(reference=dict(settings.reference_rates))
            pl = build_payload(sig, a.ctx, inst, tf, settings, balance, conv, stats, src, "live")
            if self.store.add_alert("live", sym, tf, sig.pattern, sig.direction, sig.ts, pl) is not None:
                with self._lock:
                    self.status.alerts += 1
                if settings.notify_desktop:
                    desktop_notify(headline(pl)[:60], desktop_text(pl))
        with self._lock:
            self.status.sources[f"{sym} {tf}"] = f"OANDA {settings.oanda_environment}, history: {src}"

    # ------------------------------------------------------------------ simulation (replay)
    def _run_sim(self, sim_days: int) -> None:
        settings = load_settings()
        cfg = settings.session_for("forex")
        prepared = []
        for sym in settings.watchlist:
            ds = pick_dataset(self.store, sym)
            if ds is None:
                self._error(f"{sym}: no stored data to replay")
                continue
            base = self.store.load_bars(int(ds["id"]))
            inst = get_instrument(sym)
            for tf in settings.scan_timeframes:
                m = timeframe_minutes(tf)
                if m < timeframe_minutes(ds["timeframe"]):
                    continue
                df = base if m == timeframe_minutes(ds["timeframe"]) else resample_ohlcv(base, m, cfg)
                a = analyse(df, cfg, inst, m, settings.broker(), settings.target_r, settings.order_expiry_bars)
                close_t = df.index + pd.Timedelta(minutes=m)
                src = ds["name"] + (" - SYNTHETIC, not real market data" if ds["is_sample"] else "")
                by_i: dict = {}
                for sig in a.signals:
                    by_i.setdefault(sig.i, []).append(sig)
                prepared.append((sym, tf, inst, a, close_t, src, by_i))
                with self._lock:
                    self.status.sources[f"{sym} {tf}"] = f"replaying {src}"
            if self._stop.is_set():
                return
        if not prepared:
            self._error("Nothing to replay - load some data first.")
            self.status.running = False
            return
        end = min(p[4][-1] for p in prepared)
        clock = end - pd.Timedelta(days=sim_days)
        step = pd.Timedelta(minutes=min(timeframe_minutes(tf) for tf in settings.scan_timeframes))
        balance = self._balance(settings)
        with self._lock:
            self.status.sim_end = end
        while not self._stop.is_set() and clock < end:
            nxt = clock + step
            for sym, tf, inst, a, close_t, src, by_i in prepared:
                lo, hi = close_t.searchsorted(clock, side="right"), close_t.searchsorted(nxt, side="right")
                for bar in range(lo, hi):   # bars that closed during this simulated step
                    for sig in by_i.get(bar, []):
                        self._emit(a, sig, inst, tf, settings, "simulation", src, balance)
            clock = nxt
            with self._lock:
                self.status.sim_clock = clock
                self.status.last_check = pd.Timestamp.now(tz="UTC")
                self.status.cycles += 1
            quiet = (settings.notify_only_in_window and not in_window(pd.DatetimeIndex([clock]), cfg)[0]) or \
                not market_open_mask(pd.DatetimeIndex([clock]), cfg)[0]
            if not quiet:   # fast-forward through nights and weekends, when no alerts are allowed
                self._stop.wait(self.sim_seconds)
        self.status.running = False
