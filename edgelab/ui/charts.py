"""Plotly chart builder: candlesticks + overlays + indicator panels."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from edgelab.data.sessions import session_membership
from edgelab.indicators.levels import Level

UP = "#26a69a"
DOWN = "#ef5350"
SESSION_COLORS = {"Asia (Tokyo)": "#ef6c00", "London": "#1565c0", "New York": "#2e7d32"}
SESSION_SHORT = {"Asia (Tokyo)": "Asia", "London": "London", "New York": "New York"}
COLORS = {
    "vwap": "#f5a623", "ema_fast": "#42a5f5", "ema_slow": "#ab47bc", "sma_mid": "#8d6e63", "sma_long": "#546e7a",
    "or": "#7e57c2", "pdh": "#2e7d32", "pdl": "#c62828", "pdc": "#757575", "support": "#2e7d32",
    "resistance": "#c62828", "rsi": "#5c6bc0", "macd": "#1e88e5", "signal": "#fb8c00",
}


@dataclass
class ChartOptions:
    overlays: set = field(default_factory=lambda: {"vwap", "ema_fast", "ema_slow"})
    panels: list = field(default_factory=lambda: ["volume", "rsi"])
    levels: list[Level] = field(default_factory=list)
    show_day_separators: bool = True
    ema_fast: int = 9
    ema_slow: int = 20
    sma_mid: int = 50
    sma_long: int = 200
    height: int = 780
    decimals: int = 5


def _labels(idx: pd.DatetimeIndex, tz: str, daily: bool) -> list[str]:
    local = idx.tz_convert(tz)
    return list(local.strftime("%Y-%m-%d" if daily else "%a %d %b %H:%M"))


def build_chart(df: pd.DataFrame, opts: ChartOptions, tz: str = "UTC", title: str = "", daily: bool = False,
                trades: pd.DataFrame | None = None, markers: pd.DataFrame | None = None) -> go.Figure:
    """df: indicator frame (see analysis.add_indicators). Uses a category x-axis so gaps (weekends) vanish."""
    panels = [p for p in opts.panels if p in ("volume", "rsi", "macd", "atr")]
    if "volume" in panels and df["volume"].fillna(0).sum() == 0:
        panels.remove("volume")
    n_rows = 1 + len(panels)
    heights = [0.58] + [0.42 / max(1, len(panels))] * len(panels) if panels else [1.0]
    fig = make_subplots(rows=n_rows, cols=1, shared_xaxes=True, vertical_spacing=0.025, row_heights=heights)
    x = _labels(df.index, tz, daily)
    fmt = f".{opts.decimals}f"

    fig.add_trace(go.Candlestick(x=x, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
                                 name="Price", increasing_line_color=UP, decreasing_line_color=DOWN,
                                 increasing_fillcolor=UP, decreasing_fillcolor=DOWN, showlegend=False), row=1, col=1)

    boundaries = set()
    if "day" in df and not daily:
        boundaries = set(np.flatnonzero(df["day"].ne(df["day"].shift()).to_numpy())[1:].tolist())

    def line(col, name, color, dash=None, width=1.4, shape="linear", per_day=False):
        if col in df and df[col].notna().any():
            xs, ys = x, df[col].to_numpy()
            if per_day and boundaries:  # break session lines (VWAP, prior-day levels) at each new day
                xs, ys = [], []
                for i, (xi, yi) in enumerate(zip(x, df[col].to_numpy())):
                    if i in boundaries:
                        xs.append(xi)
                        ys.append(None)
                    xs.append(xi)
                    ys.append(None if pd.isna(yi) else float(yi))
            fig.add_trace(go.Scatter(x=xs, y=ys, name=name, mode="lines", connectgaps=False,
                                     line=dict(color=color, width=width, dash=dash, shape=shape),
                                     hovertemplate=f"{name}: %{{y:{fmt}}}<extra></extra>"), row=1, col=1)

    ov = opts.overlays
    if "vwap" in ov and not daily:
        line("vwap", "VWAP", COLORS["vwap"], width=2, per_day=True)
    if "ema_fast" in ov:
        line(f"ema{opts.ema_fast}", f"EMA {opts.ema_fast}", COLORS["ema_fast"])
    if "ema_slow" in ov:
        line(f"ema{opts.ema_slow}", f"EMA {opts.ema_slow}", COLORS["ema_slow"])
    if "sma_mid" in ov:
        line(f"sma{opts.sma_mid}", f"SMA {opts.sma_mid}", COLORS["sma_mid"])
    if "sma_long" in ov:
        line(f"sma{opts.sma_long}", f"SMA {opts.sma_long}", COLORS["sma_long"], width=2)
    if "or" in ov and not daily:
        line("or_high", "Opening range high", COLORS["or"], dash="dot", shape="hv", per_day=True)
        line("or_low", "Opening range low", COLORS["or"], dash="dot", shape="hv", per_day=True)
    if "pdhl" in ov and not daily:
        line("pdh", "Prior-day high", COLORS["pdh"], dash="dash", shape="hv", per_day=True)
        line("pdl", "Prior-day low", COLORS["pdl"], dash="dash", shape="hv", per_day=True)
        line("pdc", "Prior-day close", COLORS["pdc"], dash="dot", width=1, shape="hv", per_day=True)

    for lv in opts.levels:
        c = COLORS[lv.kind]
        fig.add_hrect(y0=lv.low, y1=lv.high if lv.high > lv.low else lv.low * (1 + 1e-9), fillcolor=c, opacity=0.12,
                      line_width=0, row=1, col=1)
        fig.add_hline(y=lv.price, line=dict(color=c, width=1, dash="longdash"), opacity=0.7, row=1, col=1,
                      annotation_text=f"{lv.kind} ({lv.touches} touches)", annotation_position="top left",
                      annotation_font_size=10, annotation_font_color=c)

    if "sessions" in ov and not daily:
        mem = session_membership(df.index)
        for name, color in SESSION_COLORS.items():
            on = mem[name].to_numpy()
            if not on.any():
                continue
            edges = np.flatnonzero(np.diff(np.r_[0, on.astype(int), 0]))
            for a, b in zip(edges[::2], edges[1::2] - 1):
                fig.add_vrect(x0=x[a], x1=x[b], fillcolor=color, opacity=0.10, line_width=0, layer="below",
                              row="all", col=1)
                fig.add_annotation(x=x[a], y=1.0, yref="y domain", text=SESSION_SHORT[name], showarrow=False,
                                   xanchor="left", yanchor="top", font=dict(size=9, color=color), row=1, col=1)

    if opts.show_day_separators and boundaries:
        for i in sorted(boundaries):
            fig.add_vline(x=x[i], line=dict(color="rgba(128,128,128,0.35)", width=1, dash="dot"))

    if trades is not None and not trades.empty:
        _add_trades(fig, df, x, trades)

    if markers is not None and not markers.empty:
        pos = df.index.get_indexer(markers.index)
        ok = pos >= 0
        fig.add_trace(go.Scatter(x=[x[i] for i in pos[ok]], y=markers.loc[ok, "y"], mode="markers+text",
                                 text=markers.loc[ok, "text"], textposition="top center",
                                 marker=dict(symbol=markers.loc[ok, "symbol"] if "symbol" in markers else "circle",
                                             size=11, color=markers.loc[ok, "color"] if "color" in markers else "#000"),
                                 name="Markers", hoverinfo="text"), row=1, col=1)

    r = 2
    for p in panels:
        if p == "volume":
            colors = np.where(df["close"] >= df["open"], UP, DOWN)
            fig.add_trace(go.Bar(x=x, y=df["volume"], marker_color=colors, name="Volume", opacity=0.7,
                                 showlegend=False), row=r, col=1)
            fig.update_yaxes(title_text="Volume", row=r, col=1)
        elif p == "rsi":
            fig.add_trace(go.Scatter(x=x, y=df["rsi"], name="RSI", line=dict(color=COLORS["rsi"], width=1.3),
                                     showlegend=False), row=r, col=1)
            for lvl, dash in ((70, "dash"), (50, "dot"), (30, "dash")):
                fig.add_hline(y=lvl, line=dict(color="gray", width=0.8, dash=dash), row=r, col=1)
            fig.update_yaxes(title_text="RSI", range=[0, 100], row=r, col=1)
        elif p == "macd":
            hc = np.where(df["macd_hist"] >= 0, UP, DOWN)
            fig.add_trace(go.Bar(x=x, y=df["macd_hist"], marker_color=hc, name="MACD hist", showlegend=False,
                                 opacity=0.6), row=r, col=1)
            fig.add_trace(go.Scatter(x=x, y=df["macd"], name="MACD", line=dict(color=COLORS["macd"], width=1.2),
                                     showlegend=False), row=r, col=1)
            fig.add_trace(go.Scatter(x=x, y=df["macd_signal"], name="Signal",
                                     line=dict(color=COLORS["signal"], width=1.2), showlegend=False), row=r, col=1)
            fig.update_yaxes(title_text="MACD", row=r, col=1)
        elif p == "atr":
            fig.add_trace(go.Scatter(x=x, y=df["atr"], name="ATR", line=dict(color="#6d4c41", width=1.3),
                                     showlegend=False), row=r, col=1)
            fig.update_yaxes(title_text="ATR", row=r, col=1)
        r += 1

    fig.update_xaxes(type="category", nticks=12, showgrid=False, rangeslider_visible=False)
    fig.update_yaxes(tickformat=fmt, row=1, col=1, side="right")
    fig.update_layout(height=opts.height, margin=dict(l=10, r=10, t=40 if title else 10, b=10), title=title,
                      hovermode="x unified", dragmode="pan",
                      legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0, font_size=11))
    return fig


def _add_trades(fig, df, x, trades):
    idx = df.index
    for kind, tcol, pcol in (("entry", "entry_time", "entry_price"), ("exit", "exit_time", "exit_price")):
        t = trades[(trades[tcol] >= idx[0]) & (trades[tcol] <= idx[-1])]
        if t.empty:
            continue
        pos = idx.searchsorted(t[tcol], side="right") - 1
        long_ = (t["side"] == "long").to_numpy()
        if kind == "entry":
            sym = np.where(long_, "triangle-up", "triangle-down")
            col = np.where(long_, "#1b5e20", "#b71c1c")
        else:
            sym = np.full(len(t), "x")
            col = np.where(t["pnl"].fillna(0) >= 0, "#1b5e20", "#b71c1c")
        hover = [f"{kind.title()} {s} @ {p}" + (f"<br>P&L {pnl:,.2f}" if kind == "exit" and pd.notna(pnl) else "")
                 for s, p, pnl in zip(t["side"], t[pcol], t["pnl"])]
        fig.add_trace(go.Scatter(x=[x[i] for i in pos], y=t[pcol], mode="markers", name=f"Trade {kind}s",
                                 marker=dict(symbol=sym, size=12, color=col, line=dict(width=1, color="white")),
                                 hovertext=hover, hoverinfo="text"), row=1, col=1)
