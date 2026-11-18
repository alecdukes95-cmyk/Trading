import numpy as np
import pandas as pd
import pytest

from edgelab.brokers import BrokerProfile
from edgelab.data.store import Store
from edgelab.journal import analytics as J
from edgelab.paper.engine import Env, PaperBook

T0 = pd.Timestamp("2026-01-14 09:00", tz="UTC")


def _trade(st, k, pnl, r, tags, src="manual", **j):
    tid = st.add_trade({"symbol": "EURUSD", "side": "long", "entry_time": T0 + pd.Timedelta(days=k),
                        "exit_time": T0 + pd.Timedelta(days=k, hours=1), "entry_price": 1.1, "exit_price": 1.101,
                        "size": 10_000, "fees": 0.0, "stop_price": 1.099 if r is not None else np.nan, "setup": "",
                        "notes": "", "pnl": pnl, "r_multiple": r if r is not None else np.nan, "pnl_currency": "GBP"}, src)
    st.journal_upsert(tid, tags=tags, **j)
    return tid


def test_journal_store_roundtrip_and_attachments(tmp_path):
    st = Store(tmp_path / "j.db")
    tid = _trade(st, 0, 12.0, 1.5, ["Opening range breakout"], emotion="calm", followed="yes", grade="B",
                 mistakes=["none"], lesson="wait for the close")
    j = st.journal_get(tid)
    assert j["tags"] == ["Opening range breakout"] and j["grade"] == "B" and j["mistakes"] == ["none"]
    st.journal_upsert(tid, grade="A")                      # partial update keeps the rest
    assert st.journal_get(tid)["lesson"] == "wait for the close" and st.journal_get(tid)["grade"] == "A"
    st.add_attachment(tid, str(tmp_path / "x.png"), "entry")
    assert len(st.attachments(tid)) == 1
    df = st.journal_frame()
    assert df.loc[df["id"] == tid, "tags"].iloc[0] == ["Opening range breakout"]
    st.delete_trade(tid)
    assert st.journal_frame().empty and st.attachments(tid) == []


def test_paper_trades_land_in_the_journal_with_plan_and_review(tmp_path):
    st = Store(tmp_path / "p.db")
    book = PaperBook(st.path, trade_store=st)
    env = Env(BrokerProfile("T"), "GBP", {"GBPUSD": 1.25})
    q = {"bid": 1.1, "ask": 1.1001, "tradeable": True}
    pid = book.place_order("EURUSD", "long", "market", 10_000, quote=q, env=env, now=T0, sl=1.098, tp=1.104,
                           plan={"setup": "Pin bar (rejection candle)", "emotion": "calm", "why": "rejected the low"})["position_id"]
    book.on_price("EURUSD", 1.1041, 1.1042, T0 + pd.Timedelta(minutes=10), env)
    book.save_review(pid, {"followed": "yes", "grade": "A", "mistakes": ["none"], "lesson": "patience"})
    df = J.prepare(st.journal_frame())
    row = df.iloc[0]
    assert row["group"] == "paper" and row["tags"] == ["Pin bar (rejection candle)"]
    assert row["emotion"] == "calm" and row["followed"] == "yes" and row["grade"] == "A" and row["lesson"] == "patience"


def test_analytics_summaries_calendar_and_lessons(tmp_path):
    st = Store(tmp_path / "a.db")
    for k in range(8):
        win = k % 2 == 0
        _trade(st, k, 20.0 if win else -15.0, 1.5 if win else -1.0, ["Opening range breakout"] if k < 6 else ["News"],
               followed="yes" if win else "no", mistakes=["none"] if win else ["moved my stop further away"])
    d = J.prepare(st.journal_frame())
    h = J.headline(d)
    assert h["trades"] == 8 and h["win_rate"] == 0.5 and h["total"] == pytest.approx(4 * 20 - 4 * 15)
    by_tag = J.summary(d, "tags", explode=True).set_index("tags")
    assert by_tag.loc["Opening range breakout", "Trades"] == 6
    mist = J.summary(d, "mistakes", explode=True).set_index("mistakes")
    assert mist.loc["moved my stop further away", "Total P&L"] == pytest.approx(-60)
    grid = J.calendar_grid(J.daily_pnl(d))
    assert grid.shape[1] == 7 and np.nansum(grid.to_numpy()) == pytest.approx(h["total"])
    text = " ".join(J.lessons(d))
    assert "moved my stop further away" in text and "Following your plan" in text


def test_execution_gap_maps_tags_to_patterns():
    d = pd.DataFrame({"tags": [["Opening range breakout"], ["Alert: Bullish pin bar (rejection candle)"], ["News"]],
                      "r_multiple": [1.0, -1.0, 2.0]})
    bt = pd.DataFrame({"pattern": ["orb", "orb", "pin_bar"], "r": [0.5, 0.1, 0.2]})
    gap = J.execution_gap(d, bt).set_index("Setup")
    assert gap.loc["Opening range breakout", "Gap (you - rules)"] == pytest.approx(1.0 - 0.3)
    assert gap.loc["Pin bar (rejection candle)", "Your trades"] == 1
    assert J.pattern_for_tag("Bull flag on EURUSD") == "bull_flag" and J.pattern_for_tag("random") is None
