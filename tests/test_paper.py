import pandas as pd
import pytest

from edgelab.brokers import BrokerProfile
from edgelab.data.store import Store
from edgelab.paper.behaviour import CHECKLIST, behaviour_insights, pre_trade_checks, trades_frame
from edgelab.paper.engine import Env, PaperBook
from edgelab.paper.feeds import ReplayFeed

T0 = pd.Timestamp("2026-01-14 09:00", tz="UTC")
REF = {"GBPUSD": 1.25, "EURUSD": 1.10, "USDJPY": 150.0}


@pytest.fixture
def setup(tmp_path):
    st = Store(tmp_path / "p.db")
    book = PaperBook(st.path, trade_store=st)
    book.reset(10_000, "GBP")
    env = Env(BrokerProfile("T", commission_model="per_lot_round_turn", commission_value=5.0), "GBP", dict(REF),
              {"GBPUSD": 1.25})
    return st, book, env


def q(bid, ask):
    return {"bid": bid, "ask": ask, "tradeable": True}


def test_market_buy_fills_at_ask_and_take_profit_books_gbp(setup):
    st, book, env = setup
    r = book.place_order("EURUSD", "long", "market", 10_000, quote=q(1.1000, 1.1001), env=env, now=T0,
                         sl=1.0981, tp=1.1041, plan={"setup": "orb", "emotion": "calm"})
    assert r["ok"] and r["position_id"]
    pos = book.positions()[0]
    assert pos["open_price"] == 1.1001                       # paid the spread
    snap = book.snapshot({"EURUSD": q(1.1020, 1.1021)}, env)
    assert snap["unrealised"] == pytest.approx(19 / 1.25)    # 19 pips x $1 -> GBP at 1.25
    assert snap["used_margin"] == pytest.approx(10_000 * 1.10205 / 1.25 / 30)
    ev = book.on_price("EURUSD", 1.1041, 1.1042, T0 + pd.Timedelta(minutes=30), env)
    assert ev[0]["type"] == "take-profit"
    closed = book.positions("closed")[0]
    gross = (1.1041 - 1.1001) * 10_000 / 1.25
    assert closed["pnl"] == pytest.approx(gross - 0.5)       # GBP 5 per standard lot round turn x 0.1 lot
    assert closed["r"] == pytest.approx((gross - 0.5) / ((1.1001 - 1.0981) * 10_000 / 1.25))
    assert book.balance() == pytest.approx(10_000 + gross - 0.5)
    log = st.load_trades()
    assert len(log) == 1 and log["source"].iloc[0] == "paper" and log["setup"].iloc[0] == "orb"


def test_short_stop_loss_gap_fills_worse(setup):
    _, book, env = setup
    book.place_order("EURUSD", "short", "market", 10_000, quote=q(1.1000, 1.1001), env=env, now=T0, sl=1.1020)
    ev = book.on_price("EURUSD", 1.1029, 1.1030, T0 + pd.Timedelta(minutes=5), env)   # jumped through the stop
    assert ev[0]["type"] == "stop-loss" and ev[0]["price"] == 1.1030               # filled at the ask, not 1.1020
    assert book.positions("closed")[0]["r"] < -1


def test_pending_orders_trigger_expire_and_validate(setup):
    _, book, env = setup
    bad = book.place_order("EURUSD", "long", "stop", 10_000, quote=q(1.1000, 1.1001), env=env, now=T0, price=1.0990)
    assert not bad["ok"] and "above the current price" in bad["message"]
    bad_sl = book.place_order("EURUSD", "long", "market", 10_000, quote=q(1.1000, 1.1001), env=env, now=T0, sl=1.1005)
    assert not bad_sl["ok"] and "wrong side" in bad_sl["message"]
    ok = book.place_order("EURUSD", "long", "stop", 10_000, quote=q(1.1000, 1.1001), env=env, now=T0, price=1.1010,
                          sl=1.0995, tp=1.1040, expiry_minutes=30)
    assert ok["ok"] and not book.positions()
    book.on_price("EURUSD", 1.1005, 1.1006, T0 + pd.Timedelta(minutes=1), env)
    assert not book.positions()
    ev = book.on_price("EURUSD", 1.1011, 1.1012, T0 + pd.Timedelta(minutes=2), env)
    assert ev[0]["type"] == "filled" and book.positions()[0]["open_price"] == 1.1012
    lim = book.place_order("EURUSD", "short", "limit", 10_000, quote=q(1.1011, 1.1012), env=env, now=T0, price=1.1050,
                           expiry_minutes=10)
    assert lim["ok"]
    ev = book.on_price("EURUSD", 1.1012, 1.1013, T0 + pd.Timedelta(minutes=20), env)
    assert any(e["type"] == "expired" for e in ev)


def test_margin_rejection(setup):
    _, book, env = setup
    book.reset(500, "GBP")
    # 20,000 EUR ~ GBP 17,600 -> GBP 587 margin at 30:1: more than the GBP 500 account
    rej = book.place_order("EURUSD", "long", "market", 20_000, quote=q(1.1, 1.1001), env=env, now=T0)
    assert not rej["ok"] and "margin" in rej["message"]
    ok = book.place_order("EURUSD", "long", "market", 10_000, quote=q(1.1, 1.1001), env=env, now=T0)  # ~GBP 293
    assert ok["ok"]


def test_margin_closeout_triggers(setup):
    _, book, env = setup
    book.reset(1_000, "GBP")
    assert book.place_order("EURUSD", "long", "market", 30_000, quote=q(1.1, 1.1001), env=env, now=T0)["ok"]
    small = book.on_price("EURUSD", 1.0975, 1.0976, T0 + pd.Timedelta(minutes=1), env)   # -26 pips: ~GBP 62 loss
    assert not small and book.positions()
    # margin ~GBP 860; equity must fall to 50% of it -> a loss of ~GBP 570 = ~240 pips on 30,000 units
    ev = book.on_price("EURUSD", 1.0760, 1.0761, T0 + pd.Timedelta(minutes=3), env)
    assert any(e["type"] == "margin close-out" for e in ev) and not book.positions()


def test_modify_flags_widened_stop(setup):
    _, book, env = setup
    pid = book.place_order("EURUSD", "long", "market", 10_000, quote=q(1.1, 1.1001), env=env, now=T0,
                           sl=1.0990)["position_id"]
    msg = book.modify_position(pid, T0, sl=1.0970)
    assert "stop widened" in msg
    book.modify_position(pid, T0, sl=1.1001)                      # to breakeven: not flagged
    flags = [f for e in book.position(pid)["events"] for f in (e.get("flags") or [])]
    assert flags == ["stop widened"]


def test_pre_trade_checks_and_insights():
    w = pre_trade_checks(has_sl=False, risk_pct=1.2, rule_pct=0.65, rr=1.0, emotion="revenge (winning it back)",
                         ticked=[], closed=[{"pnl": -20, "close_ts": str(T0 - pd.Timedelta(minutes=5)),
                                             "open_ts": str(T0), "units": 10_000}],
                         open_count=0, now=T0, units=10_000)
    text = " ".join(m for _, m in w)
    assert "No stop-loss" in text and "above your 0.65% rule" in text and "revenge" in text
    assert "less than 15 minutes ago" in text and "checklist" in text
    good = pre_trade_checks(has_sl=True, risk_pct=0.5, rule_pct=0.65, rr=2.0, emotion="calm", ticked=list(CHECKLIST),
                            closed=[], open_count=0, now=T0, units=10_000)
    assert all(level == "ok" for level, _ in good)


def test_replay_feed_quotes_and_partial_candle():
    idx = pd.date_range("2026-01-14 08:00", periods=6, freq="5min", tz="UTC")
    df = pd.DataFrame({"open": [1.1, 1.101, 1.102, 1.103, 1.104, 1.105], "high": [1.1015, 1.1025, 1.1035, 1.1045, 1.1055, 1.1065],
                       "low": [1.0995, 1.1005, 1.1015, 1.1025, 1.1035, 1.1045], "close": [1.101, 1.102, 1.103, 1.104, 1.105, 1.106],
                       "volume": 100.0}, index=idx)
    feed = ReplayFeed({"EURUSD": df}, {"EURUSD": 0.0002}, idx[2])
    qq = feed.quotes(["EURUSD"])["EURUSD"]
    assert qq["ask"] - qq["bid"] == pytest.approx(0.0002) and (qq["bid"] + qq["ask"]) / 2 == pytest.approx(1.102)
    feed.advance()
    feed.advance()
    ch = feed.chart("EURUSD", "5m")
    assert ch.index[-1] == idx[2] and ch["close"].iloc[-1] == pytest.approx(1.1035)   # o -> low -> HIGH so far
    assert ch.index[-2] == idx[1]                                                       # no future bars
    assert idx[3] not in ch.index
