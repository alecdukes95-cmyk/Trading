"""Quizzes generated from real historical charts (and the glossary).

Question types
--------------
setup     "Which setup just completed on this chart?"         (chart up to the signal bar, no markers)
next      "What happened over the next N bars?"               (up 1.5 ATR first / down first / nothing decisive)
stop      "Where is the most sensible stop-loss?"             (the pattern's invalidation vs too tight vs too wide)
concept   "What does this term mean?"                         (glossary definitions, no chart)

Every question carries its explanation, shown after you answer. Answers are recorded so the app can track your
weakest topics.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from edgelab.learning.glossary import GLOSSARY
from edgelab.learning.replay import MOVE_ATR, hindsight_label
from edgelab.patterns.base import PATTERNS, TRADEABLE

QTYPES = {"setup": "Spot the setup", "next": "What happened next?", "stop": "Place the stop", "concept": "Concepts"}


@dataclass
class Question:
    qtype: str
    prompt: str
    options: list
    answer: int                      # index into options
    explanation: str
    topic: str                       # pattern key, glossary term, or 'stops' - used for weak-topic tracking
    signal_i: int | None = None      # chart bar the question is about (chart questions)
    extra: dict = field(default_factory=dict)


def _shuffle(rng, correct, wrong: list) -> tuple[list, int]:
    opts = [correct] + list(wrong)
    order = rng.permutation(len(opts))
    opts = [opts[k] for k in order]
    return opts, int(np.flatnonzero(order == 0)[0])


def _pattern_name(key: str) -> str:
    return "Bull / bear flag" if key in ("bull_flag", "bear_flag") else PATTERNS[key].name


def setup_question(sig, signals, rng) -> Question:
    correct = _pattern_name(sig.pattern)
    pool = sorted({_pattern_name(k) for k in TRADEABLE} - {correct})
    wrong = list(rng.choice(pool, size=3, replace=False))
    opts, ans = _shuffle(rng, correct, wrong)
    info = PATTERNS[sig.pattern]
    expl = (f"**{sig.title}.** {info.what}\n\n**What gave it away:** " + "; ".join(sig.reasons[:3]) + ".\n\n"
            f"**What traders infer:** {info.meaning}")
    return Question("setup", "Which setup just completed on the last candle?", opts, ans, expl, sig.pattern, sig.i)


def next_question(sig, ctx, horizon: int, rng) -> Question:
    label, _ = hindsight_label(ctx.h, ctx.l, ctx.c, sig.i, float(ctx.atr[sig.i]), horizon)
    texts = {"up": f"Price rose {MOVE_ATR} ATR before falling {MOVE_ATR} ATR",
             "down": f"Price fell {MOVE_ATR} ATR before rising {MOVE_ATR} ATR",
             "chop": "Nothing decisive - neither move happened"}
    opts = [texts["up"], texts["down"], texts["chop"]]
    ans = ["up", "down", "chop"].index(label)
    expected = {"long": "up", "short": "down"}.get(sig.direction)
    agree = "matched" if expected == label else "did NOT match"
    expl = (f"The chart showed a **{sig.title}**, which leans {sig.direction}. Over the next {horizon} bars "
            f"the market {texts[label].lower()} - it {agree} what the pattern suggests. Patterns tilt the odds at "
            "best; on any single chart the market can do anything, which is why the stop matters more than the call.")
    return Question("next", f"This setup just completed. What did price do over the next {horizon} bars?", opts, ans,
                    expl, sig.pattern, sig.i, {"label": label})


def stop_question(sig, ctx, inst, rng) -> Question | None:
    if sig.plan is None:
        return None
    p = sig.plan
    atr = float(ctx.atr[sig.i])
    d = 1 if p.side == "long" else -1
    good = p.stop
    tight = p.entry - d * 0.2 * atr
    wide = p.entry - d * 6 * atr
    fmt = inst.fmt_price
    pips = lambda x: abs(p.entry - x) / inst.pip  # noqa: E731
    correct = f"{fmt(good)} ({pips(good):.1f} pips) - beyond where the pattern is proven wrong"
    wrong = [f"{fmt(tight)} ({pips(tight):.1f} pips) - as close as possible to keep the loss small",
             f"{fmt(wide)} ({pips(wide):.1f} pips) - far away so it never gets hit"]
    opts, ans = _shuffle(rng, correct, wrong)
    expl = (f"The entry would be a **{p.order_type} at {fmt(p.entry)}**. A stop belongs where the idea is proven "
            f"wrong: here {PATTERNS[sig.pattern].invalidate.lower()} A stop of 0.2 ATR sits inside normal noise "
            "(about a fifth of a typical bar) and gets hit randomly; a 6-ATR stop makes each loss huge, forcing a "
            "tiny position or breaking your risk rule. Size the position FROM the stop, not the stop from the size.")
    return Question("stop", f"You want to take this {sig.direction} trade. Where should the stop-loss go?", opts, ans,
                    expl, "stops", sig.i, {"entry": p.entry})


def concept_question(rng, term: str | None = None) -> Question:
    names = list(GLOSSARY)
    term = term or str(rng.choice(names))
    t = GLOSSARY[term]
    others = [n for n in names if n != term and GLOSSARY[n].category == t.category] or [n for n in names if n != term]
    if len(others) < 3:
        others = [n for n in names if n != term]
    wrong = [GLOSSARY[n].short() for n in rng.choice(others, size=3, replace=False)]
    opts, ans = _shuffle(rng, t.short(), wrong)
    return Question("concept", f"Which statement describes **{term}**?", opts, ans, t.markdown(), term)


def make_question(qtype: str, analysis, inst, rng, horizon: int = 24, history: int = 100, settings=None,
                  topic: str | None = None) -> Question | None:
    if qtype == "concept":
        return concept_question(rng, topic if topic in GLOSSARY else None)
    from edgelab.live.alerts import passes_filters

    ctx = analysis.ctx
    n = len(ctx.c)
    cands = [s for s in analysis.signals if history <= s.i < n - horizon - 1 and s.plan is not None
             and (topic is None or s.pattern == topic or (topic == "flags" and "flag" in s.pattern))]
    good = [s for s in cands if settings is None or passes_filters(s, ctx, settings)]
    pool = good or cands
    if not pool:
        return None
    sig = pool[int(rng.integers(len(pool)))]
    if qtype == "setup":
        return setup_question(sig, analysis.signals, rng)
    if qtype == "next":
        return next_question(sig, ctx, horizon, rng)
    if qtype == "stop":
        return stop_question(sig, ctx, inst, rng)
    raise ValueError(qtype)


def weakest_topics(attempts, min_n: int = 3, k: int = 5):
    """Topics answered at least `min_n` times, worst accuracy first."""
    if attempts is None or len(attempts) == 0:
        return []
    g = attempts.groupby("topic")["correct"].agg(["mean", "count"])
    g = g[g["count"] >= min_n].sort_values("mean")
    return [(t, float(r["mean"]), int(r["count"])) for t, r in g.head(k).iterrows()]
