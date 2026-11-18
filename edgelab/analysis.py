"""One place that adds all chart indicators to a price frame."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from edgelab.data.sessions import SessionConfig, trading_day
from edgelab.indicators import atr, ema, macd, opening_range, prior_day_levels, rsi, sma, vwap


@dataclass(frozen=True)
class IndicatorParams:
    ema_fast: int = 9
    ema_slow: int = 20
    sma_mid: int = 50
    sma_long: int = 200
    atr_n: int = 14
    rsi_n: int = 14
    or_minutes: int = 30


def add_indicators(df: pd.DataFrame, cfg: SessionConfig, p: IndicatorParams = IndicatorParams(),
                   intraday: bool = True) -> pd.DataFrame:
    out = df.copy()
    c = out["close"]
    out[f"ema{p.ema_fast}"] = ema(c, p.ema_fast)
    out[f"ema{p.ema_slow}"] = ema(c, p.ema_slow)
    out[f"sma{p.sma_mid}"] = sma(c, p.sma_mid)
    out[f"sma{p.sma_long}"] = sma(c, p.sma_long)
    out["atr"] = atr(out, p.atr_n)
    out["rsi"] = rsi(c, p.rsi_n)
    m = macd(c)
    out["macd"], out["macd_signal"], out["macd_hist"] = m["macd"], m["signal"], m["hist"]
    out["day"] = trading_day(out.index, cfg)
    if intraday:
        out["vwap"] = vwap(out, cfg)
        orr = opening_range(out, cfg, p.or_minutes)
        out["or_high"], out["or_low"] = orr["or_high"], orr["or_low"]
        pdl = prior_day_levels(out, cfg)
        out["pdh"], out["pdl"], out["pdc"] = pdl["pdh"], pdl["pdl"], pdl["pdc"]
    return out
