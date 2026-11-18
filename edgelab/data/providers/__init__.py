"""Market-data downloaders. Read-only: none of these can place orders.

Free options:
  dukascopy - years of 1-minute forex history, no account or key (history only, not live).
  mt5       - MetaTrader 5 terminal on Windows with any broker account (a free demo works): history + live.
  oanda     - OANDA v20 API with a free practice (demo) account token: history + live + balance sync.
  coinbase  - public crypto candles (crypto is parked for now).
Manual: CSV exports from any platform, including the free HistData.com files.
"""
from __future__ import annotations


def make_live_client(settings):
    """The candle source used by the live scanner, chosen in Settings -> Alerts."""
    if settings.live_source == "mt5":
        from edgelab.data.providers.mt5 import MT5Client

        return MT5Client(settings.mt5_server_tz, settings.mt5_symbol_suffix)
    from edgelab.config import api_key
    from edgelab.data.providers.oanda import OandaClient

    return OandaClient(api_key("OANDA_API_KEY") or "", settings.oanda_environment)


def live_source_ready(settings) -> tuple[bool, str]:
    if settings.live_source == "mt5":
        try:
            import MetaTrader5  # noqa: F401
        except ImportError:
            return False, "MetaTrader 5 package not installed (Windows only) - see Data -> MetaTrader 5."
        return True, "MetaTrader 5 terminal"
    from edgelab.config import api_key

    if not api_key("OANDA_API_KEY"):
        return False, "No OANDA token in .env (free with a practice account) - or switch the live source to MT5."
    return True, f"OANDA {settings.oanda_environment}"
