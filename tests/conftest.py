import numpy as np
import pandas as pd
import pytest


def make_bars(closes, start="2026-03-02 08:00", freq="5min", tz="UTC", spread=0.0002, volume=100.0):
    """Simple OHLCV frame from a list of closes (open = previous close)."""
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz=tz)
    opens = np.r_[closes[0], closes[:-1]]
    highs = np.maximum(opens, closes) + spread
    lows = np.minimum(opens, closes) - spread
    vol = np.full(len(closes), volume) if np.isscalar(volume) else np.asarray(volume, float)
    df = pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": vol}, index=idx)
    df.index.name = "ts"
    return df


@pytest.fixture
def bars():
    return make_bars


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGELAB_HOME", str(tmp_path))
    return tmp_path
