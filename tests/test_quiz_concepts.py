import numpy as np
import pytest

from edgelab.brokers import BUILTIN_BROKERS
from edgelab.data.sample import generate_bars
from edgelab.data.sessions import FOREX_SESSION
from edgelab.instruments import get_instrument
from edgelab.learning.concepts import CONCEPTS
from edgelab.learning.glossary import GLOSSARY
from edgelab.learning.quiz import concept_question, make_question, weakest_topics
from edgelab.patterns import analyse

EURUSD = get_instrument("EURUSD")


@pytest.fixture(scope="module")
def analysis():
    df = generate_bars("EURUSD", start="2026-01-05", end="2026-02-20")
    return analyse(df, FOREX_SESSION, EURUSD, 5, BUILTIN_BROKERS["OANDA"])


@pytest.mark.parametrize("qtype", ["setup", "next", "stop"])
def test_chart_questions_are_well_formed(analysis, qtype):
    rng = np.random.default_rng(0)
    for _ in range(10):
        q = make_question(qtype, analysis, EURUSD, rng)
        assert q is not None and 0 <= q.answer < len(q.options) and len(set(q.options)) == len(q.options)
        assert q.signal_i is not None and q.explanation
        sig = next(s for s in analysis.signals if s.i == q.signal_i)
        if qtype == "setup":
            name = "Bull / bear flag" if "flag" in sig.pattern else sig.info.name
            assert q.options[q.answer] == name and len(q.options) == 4
        if qtype == "next":
            from edgelab.learning.replay import hindsight_label

            label, _ = hindsight_label(analysis.ctx.h, analysis.ctx.l, analysis.ctx.c, sig.i,
                                       float(analysis.ctx.atr[sig.i]), 24)
            assert ["up", "down", "chop"][q.answer] == label
        if qtype == "stop":
            assert "proven wrong" in q.options[q.answer]


def test_topic_drill_and_concept_questions(analysis):
    rng = np.random.default_rng(1)
    q = make_question("setup", analysis, EURUSD, rng, topic="orb")
    assert q.topic == "orb"
    for term in list(GLOSSARY)[:15]:
        cq = concept_question(rng, term)
        assert cq.options[cq.answer] == GLOSSARY[term].short() and len(cq.options) == 4


def test_weakest_topics():
    import pandas as pd

    t = pd.DataFrame({"topic": ["orb"] * 4 + ["pin_bar"] * 4 + ["vwap"], "correct": [1, 1, 1, 0, 0, 0, 1, 0, 0]})
    assert [w[0] for w in weakest_topics(t)] == ["pin_bar", "orb"]


def test_every_concept_has_glossary_terms():
    for c in CONCEPTS:
        assert c.body and c.example and all(t in GLOSSARY for t in c.terms), c.key
