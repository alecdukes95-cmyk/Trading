"""Historical performance of each pattern on the loaded data - descriptive, IN-SAMPLE only.

This is not a backtest of a strategy (that is Stage 4, with out-of-sample testing). It answers:
"when this pattern appeared before on this data, and the standard plan was followed, what happened?"
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from edgelab.patterns.base import PATTERNS


@dataclass
class PatternStats:
    pattern: str
    direction: str
    n_signals: int
    n_filled: int
    wins: int
    losses: int
    timeouts: int
    win_rate: float | None
    avg_r: float | None
    median_r: float | None
    ci_low: float | None
    ci_high: float | None
    profit_factor: float | None
    median_mfe: float | None
    median_mae: float | None
    avg_fwd_atr: float | None
    sample_note: str
    verdict: str

    @property
    def name(self) -> str:
        return PATTERNS[self.pattern].name


def sample_note(n: int) -> str:
    if n < 20:
        return f"Only {n} filled trades - far too few to say anything. Treat as anecdotes."
    if n < 50:
        return f"{n} filled trades - a small sample; luck dominates at this size."
    if n < 200:
        return f"{n} filled trades - a moderate sample. Still in-sample only."
    return f"{n} filled trades - a decent sample, but in-sample only (no out-of-sample test yet)."


def summarise(pattern: str, direction: str, outcomes: list) -> PatternStats:
    filled = [o for o in outcomes if o.filled and o.result in ("win", "loss", "timeout")]
    n = len(filled)
    r = np.array([o.r for o in filled], dtype=float)
    fwd = np.array([o.fwd_atr for o in outcomes], dtype=float)
    fwd = fwd[np.isfinite(fwd)]
    wins = sum(o.result == "win" for o in filled)
    losses = sum(o.result == "loss" for o in filled)
    tos = sum(o.result == "timeout" for o in filled)
    if n:
        avg = float(r.mean())
        sd = float(r.std(ddof=1)) if n > 1 else float("nan")
        half = 1.96 * sd / np.sqrt(n) if n > 1 else float("nan")
        lo, hi = avg - half, avg + half
        gp, gl = r[r > 0].sum(), -r[r < 0].sum()
        pf = float(gp / gl) if gl > 0 else None
    else:
        avg = lo = hi = pf = None
    if n < 20:
        verdict = "Not enough data to judge."
    elif lo is not None and lo > 0:
        verdict = ("Average result after costs is above zero with 95% confidence ON THIS DATA (in-sample). "
                   "That is not yet evidence of a real edge - it needs an out-of-sample test (Stage 4).")
    elif hi is not None and hi < 0:
        verdict = "Average result after costs is below zero with 95% confidence: on this data the plan lost money."
    else:
        verdict = "Average result after costs is NOT distinguishable from zero - no evidence of an edge here."
    return PatternStats(pattern, direction, len(outcomes), n, wins, losses, tos, wins / n if n else None, avg,
                        float(np.median(r)) if n else None, lo, hi, pf,
                        float(np.median([o.mfe_r for o in filled])) if n else None,
                        float(np.median([o.mae_r for o in filled])) if n else None,
                        float(fwd.mean()) if len(fwd) else None, sample_note(n), verdict)


def stats_table(signals, outcomes) -> dict[tuple[str, str], PatternStats]:
    groups: dict[tuple[str, str], list] = {}
    for s, o in zip(signals, outcomes):
        if o is None:
            continue
        groups.setdefault((s.pattern, s.direction), []).append(o)
    return {k: summarise(k[0], k[1], v) for k, v in groups.items()}


def stats_frame(table: dict) -> pd.DataFrame:
    rows = []
    for (pat, d), st in sorted(table.items(), key=lambda kv: -kv[1].n_filled):
        rows.append({"Pattern": st.name, "Direction": d, "Signals": st.n_signals, "Filled": st.n_filled,
                     "Win rate": None if st.win_rate is None else round(st.win_rate * 100, 1),
                     "Avg R (after costs)": None if st.avg_r is None else round(st.avg_r, 3),
                     "95% range of avg R": "-" if st.ci_low is None or not np.isfinite(st.ci_low) else
                     f"{st.ci_low:+.2f} to {st.ci_high:+.2f}",
                     "Profit factor": None if st.profit_factor is None else round(st.profit_factor, 2),
                     "Sample": st.sample_note.split(" - ")[0] if st.n_filled >= 20 else "too few"})
    return pd.DataFrame(rows)
