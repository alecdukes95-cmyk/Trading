"""Shared UI state: storage, settings, cached computations."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from edgelab.analysis import IndicatorParams, add_indicators
from edgelab.config import Settings, db_path, load_settings, save_settings
from edgelab.data.resample import resample_ohlcv
from edgelab.data.sample import ensure_sample_data
from edgelab.data.sessions import SessionConfig
from edgelab.data.store import Store
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
