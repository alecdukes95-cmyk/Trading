"""Shared UI state: storage, settings, cached computations."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from edgelab.analysis import IndicatorParams, add_indicators
from edgelab.account import BalanceSummary, summarise
from edgelab.config import Settings, api_key, db_path, load_settings, save_settings
from edgelab.data.providers.oanda import AccountSummary, OandaClient
from edgelab.data.resample import resample_ohlcv
from edgelab.data.sample import ensure_sample_data
from edgelab.data.sessions import SessionConfig
from edgelab.data.store import Store
from edgelab.fx import FxConverter, converter_from_store
from edgelab.timeutil import timeframe_minutes


@st.cache_resource
def get_store() -> Store:
    return Store(db_path())


def get_settings() -> Settings:
    if "_edgelab_settings" not in st.session_state:
        st.session_state["_edgelab_settings"] = load_settings()
    return st.session_state["_edgelab_settings"]


def update_settings(s: Settings) -> None:
    save_settings(s)
    st.session_state["_edgelab_settings"] = s
    st.cache_data.clear()


def ensure_samples() -> None:
    store = get_store()
    if store.has_samples():
        return
    bar = st.progress(0.0, text="First launch: creating sample data (about 10-20 seconds, only once)...")
    ensure_sample_data(store, progress=lambda f, msg: bar.progress(min(1.0, f), text=msg))
    bar.empty()


@st.cache_data(show_spinner=False, max_entries=4)
def _own_series(ids_key: tuple) -> dict:
    return converter_from_store(get_store()).series


def get_converter() -> FxConverter:
    """Currency converter: your own price data first, then the reference rates in Settings."""
    ds = datasets()
    key = tuple(ds.loc[ds["is_sample"] == 0, "id"].tolist()) if not ds.empty else ()
    return FxConverter(reference=dict(get_settings().reference_rates), series=_own_series(key))


def datasets() -> pd.DataFrame:
    return get_store().list_datasets()


@st.cache_data(show_spinner=False, max_entries=6)
def load_bars(ds_id: int) -> pd.DataFrame:
    return get_store().load_bars(ds_id)


@st.cache_data(show_spinner="Calculating indicators...", max_entries=8)
def indicator_frame(ds_id: int, base_tf: str, tf: str, cfg_dict: dict, params: IndicatorParams) -> pd.DataFrame:
    cfg = SessionConfig.from_dict(cfg_dict)
    df = load_bars(ds_id)
    m = timeframe_minutes(tf)
    if tf != base_tf:
        df = resample_ohlcv(df, m, cfg)
    return add_indicators(df, cfg, params, intraday=m < 1440)


def dataset_label(row) -> str:
    return f"{row['name']}  [{row['start']:%Y-%m-%d} to {row['end']:%Y-%m-%d}]"


def pick_dataset(key: str = "dataset", container=st) -> dict | None:
    ds = datasets()
    if ds.empty:
        container.warning("No datasets yet. Go to **Data** to import one.")
        return None
    labels = {int(r["id"]): dataset_label(r) for _, r in ds.iterrows()}
    ids = list(labels)
    default = st.session_state.get(f"{key}_id", ids[0])
    if default not in ids:
        default = ids[0]
    chosen = container.selectbox("Dataset", ids, index=ids.index(default), format_func=labels.get, key=f"{key}_sel")
    st.session_state[f"{key}_id"] = chosen
    return ds[ds["id"] == chosen].iloc[0].to_dict()


def severity_box(issue) -> None:
    body = f"**{issue.title}** - {issue.detail}"
    if issue.fix:
        body += f"\n\n*What to do:* {issue.fix}"
    if issue.examples:
        body += "\n\n*Examples:* " + "; ".join(issue.examples)
    {"error": st.error, "warning": st.warning, "info": st.info}[issue.severity](body)


# --------------------------------------------------------------------------- balance and OANDA
def balance_summary() -> BalanceSummary:
    s = get_settings()
    st_ = get_store()
    _maybe_auto_sync()
    events = st_.load_balance_events()
    if events.empty:  # first use: anchor the starting balance now, so older imported trades aren't double-counted
        st_.add_balance_event(pd.Timestamp.now(tz="UTC"), "set", s.account_balance, s.account_currency,
                              "Starting balance (default from settings - edit it under Settings -> Account)")
        events = st_.load_balance_events()
    return summarise(events, st_.load_trades(), s.account_currency, s.account_balance)


def risk_amount() -> float:
    return balance_summary().balance * get_settings().risk_pct / 100


def oanda_client() -> OandaClient:
    return OandaClient(api_key("OANDA_API_KEY") or "", get_settings().oanda_environment)


def sync_oanda_balance() -> AccountSummary:
    """Read balance/margin from OANDA (GET only) and record it as the new balance figure."""
    s = get_settings()
    summ = oanda_client().account_summary(s.oanda_account_id or None)
    get_store().add_balance_event(summ.fetched_at, "sync", summ.balance, summ.currency,
                                  f"OANDA {s.oanda_environment} account {summ.account_id}")
    s.oanda_last_summary = {"account_id": summ.account_id, "currency": summ.currency, "balance": summ.balance,
                            "nav": summ.nav, "unrealized_pl": summ.unrealized_pl, "margin_used": summ.margin_used,
                            "margin_available": summ.margin_available, "open_trades": summ.open_trades,
                            "fetched_at": summ.fetched_at.isoformat()}
    if not s.oanda_account_id:
        s.oanda_account_id = summ.account_id
    save_settings(s)
    return summ


def _maybe_auto_sync() -> None:
    s = get_settings()
    if not s.oanda_auto_sync or st.session_state.get("_oanda_synced") or not api_key("OANDA_API_KEY"):
        return
    st.session_state["_oanda_synced"] = True
    try:
        sync_oanda_balance()
    except Exception as e:  # noqa: BLE001
        st.session_state["_oanda_sync_error"] = str(e)
