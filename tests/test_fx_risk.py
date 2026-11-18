"""Money maths for a GBP account: conversions, pip values, margin, costs, sizing. Hand-checked numbers."""
import pandas as pd
import pytest

from edgelab.brokers import BUILTIN_BROKERS, BrokerProfile
from edgelab.config import Settings, load_settings, save_settings
from edgelab.data.sessions import session_label, session_times_today
from edgelab.data.store import Store
from edgelab.data.tradelog import build_trades, guess_trade_mapping
from edgelab.fx import FxConverter, converter_from_store
from edgelab.instruments import get_instrument
from edgelab.risk import (analyse_position, margin_required, notional, pip_value, trade_costs, units_for_risk,
                          value_per_price_unit)

REF = {"GBPUSD": 1.25, "EURUSD": 1.10, "USDJPY": 150.0, "BTCUSD": 60000.0}
EURUSD, GBPUSD, USDJPY, EURGBP, BTCUSD = (get_instrument(s) for s in ("EURUSD", "GBPUSD", "USDJPY", "EURGBP", "BTCUSD"))


@pytest.fixture
def conv():
    return FxConverter(reference=dict(REF))


@pytest.fixture
def broker():
    return BrokerProfile("Test", spreads_pips={"EURUSD": 1.0, "USDJPY": 1.0, "GBPUSD": 1.0}, slippage_pips=0.2,
                         commission_model="per_lot_round_turn", commission_value=5.0)


# ------------------------------------------------------------------ conversion engine
def test_direct_inverse_and_triangulated_rates(conv):
    assert conv.rate("GBP", "USD") == pytest.approx(1.25)
    assert conv.rate("USD", "GBP") == pytest.approx(0.8)
    assert conv.rate("JPY", "GBP") == pytest.approx(1 / 150 / 1.25)      # via USD
    assert conv.rate("EUR", "GBP") == pytest.approx(1.10 / 1.25)         # EUR->USD->GBP
    assert conv.rate("GBP", "GBP") == 1.0
    assert conv.rate("USDT", "GBP") == pytest.approx(0.8)                # stablecoin treated as USD
    for a, b in [("EUR", "JPY"), ("GBP", "JPY"), ("BTC", "GBP")]:
        assert conv.rate(a, b) * conv.rate(b, a) == pytest.approx(1.0)
    assert conv.rate("CHF", "GBP") is None                                # no rate -> None, never a guess


def test_hints_beat_series_and_series_beat_reference():
    s = pd.Series([1.30, 1.20], index=pd.to_datetime(["2026-01-01", "2026-01-02"], utc=True))
    conv = FxConverter(reference={"GBPUSD": 1.25}, series={"GBPUSD": s})
    assert conv.rate("USD", "GBP", at="2026-01-01 12:00") == pytest.approx(1 / 1.30)
    assert conv.lookup("USD", "GBP", at="2026-01-01 12:00").source == "data"
    assert conv.rate("USD", "GBP", at="2026-01-02 12:00", hints={"GBPUSD": 1.5}) == pytest.approx(1 / 1.5)
    # long after the series ends (stale) -> falls back to the reference rate and says so
    late = conv.lookup("USD", "GBP", at="2026-03-01")
    assert late.rate == pytest.approx(0.8) and late.source == "reference"


def test_converter_from_store_ignores_synthetic_samples(tmp_path):
    from edgelab.data.sample import generate_bars

    st = Store(tmp_path / "x.db")
    bars = generate_bars("GBPUSD", start="2026-01-05", end="2026-01-07")
    st.save_dataset(bars, name="sample", symbol="GBPUSD", asset_class="forex", timeframe="5m", is_sample=True)
    assert "GBPUSD" not in converter_from_store(st).series
    st.save_dataset(bars, name="mine", symbol="GBPUSD", asset_class="forex", timeframe="5m")
    assert "GBPUSD" in converter_from_store(st).series


# ------------------------------------------------------------------ pip value, notional, margin
@pytest.mark.parametrize("inst,price,expected", [
    (EURUSD, 1.10, 0.80),            # $1/pip -> GBP 0.80
    (GBPUSD, 1.25, 0.80),            # converted with the pair's own price
    (USDJPY, 150.0, 100 / 150 / 1.25),  # JPY 100/pip -> $0.667 -> GBP 0.533
    (EURGBP, 0.88, 1.00),            # profit already in GBP
])
def test_pip_value_one_mini_lot_in_gbp(conv, inst, price, expected):
    assert pip_value(inst, 10_000, price, conv, "GBP") == pytest.approx(expected)


def test_gbpusd_pip_value_uses_live_price_not_reference(conv):
    assert pip_value(GBPUSD, 10_000, 1.60, conv, "GBP") == pytest.approx(1 / 1.60)


@pytest.mark.parametrize("inst,price,notional_gbp", [
    (EURUSD, 1.10, 8_800.0),     # 10k EUR = $11,000 = GBP 8,800
    (GBPUSD, 1.25, 10_000.0),    # 10k GBP
    (USDJPY, 150.0, 8_000.0),    # 10k USD = GBP 8,000
    (EURGBP, 0.88, 8_800.0),
])
def test_notional_and_margin_at_uk_leverage(conv, broker, inst, price, notional_gbp):
    assert notional(inst, 10_000, price, conv, "GBP") == pytest.approx(notional_gbp)
    assert margin_required(inst, 10_000, price, conv, "GBP", broker) == pytest.approx(notional_gbp / 30)


def test_leverage_classes_follow_fca_definition(broker):
    assert get_instrument("EURUSD").leverage_class == "fx_major"
    assert get_instrument("GBPJPY").leverage_class == "fx_major"     # both majors
    assert get_instrument("AUDUSD").leverage_class == "fx_minor"     # AUD is not a 'major' currency
    assert BTCUSD.leverage_class == "crypto"
    assert BUILTIN_BROKERS["Generic UK broker (spread only)"].leverage_crypto == 1.0


def test_costs_round_trip_in_gbp(conv, broker):
    c = trade_costs(EURUSD, 10_000, 1.10, broker, conv, "GBP")
    assert c.spread == pytest.approx(0.80)                 # 1 pip x GBP 0.80
    assert c.slippage == pytest.approx(2 * 0.2 * 0.80)     # both sides
    assert c.commission == pytest.approx(5.0 * 0.1)        # GBP 5 per standard lot, 0.1 lot
    assert c.total == pytest.approx(0.80 + 0.32 + 0.50)
    pct = BrokerProfile("P", commission_model="percent_per_side", commission_value=0.002, spreads_pips={},
                        default_spread_pips=0, slippage_pips=0)
    assert trade_costs(EURUSD, 10_000, 1.10, pct, conv, "GBP").commission == pytest.approx(2 * 0.00002 * 8_800)


def test_crypto_costs_and_value(conv):
    b = BrokerProfile("C", crypto_spread_pct=0.1, crypto_fee_pct=0.25, crypto_slippage_pct=0.02)
    c = trade_costs(BTCUSD, 0.1, 60_000, b, conv, "GBP")   # 0.1 BTC = $6,000 = GBP 4,800
    assert c.total == pytest.approx(4_800 * (0.001 + 2 * 0.0025 + 2 * 0.0002))
    assert value_per_price_unit(BTCUSD, 0.1, 60_000, conv, "GBP") == pytest.approx(0.08)


# ------------------------------------------------------------------ sizing and the full report
def test_units_for_risk_rounds_down():
    # risk GBP 65, 20-pip stop on EURUSD: GBP 0.00008 per unit per pip -> 40,625 units -> 40,000
    assert units_for_risk(65, 20 * 0.0001, 0.8, step=1_000) == 40_000
    assert units_for_risk(65, 0, 0.8) == 0


def test_position_report_mini_lot(conv, broker):
    r = analyse_position(EURUSD, 10_000, 1.10, balance=10_000, risk_pct=0.65, broker=broker, conv=conv, ccy="GBP",
                         stop_distance=20 * 0.0001, target_distance=40 * 0.0001)
    assert r.lots == pytest.approx(0.1)
    assert r.pip_value == pytest.approx(0.8)
    assert r.margin_required == pytest.approx(8_800 / 30)
    assert r.free_margin == pytest.approx(10_000 - 8_800 / 30)
    assert r.margin_level_pct == pytest.approx(10_000 / (8_800 / 30) * 100)
    assert r.price_risk == pytest.approx(16.0)                    # 20 pips x GBP 0.80
    assert r.total_risk == pytest.approx(16.0 + 1.62)
    assert r.risk_pct == pytest.approx((16.0 + 1.62) / 10_000 * 100)
    assert r.risk_limit == pytest.approx(65.0)
    assert r.max_stop_for_limit / 0.0001 == pytest.approx((65 - 1.62) / 0.8)  # ~79 pips
    assert r.reward == pytest.approx(32.0 - 1.62)
    assert r.rr == pytest.approx((32.0 - 1.62) / (16.0 + 1.62))
    # close-out: equity can fall to 50% of used margin
    assert r.closeout_loss == pytest.approx(10_000 - 0.5 * 8_800 / 30)
    assert not r.warnings


def test_position_report_warns_when_rule_or_margin_broken(conv, broker):
    big = analyse_position(EURUSD, 10_000, 1.10, balance=1_000, risk_pct=0.65, broker=broker, conv=conv,
                           ccy="GBP", stop_distance=30 * 0.0001)
    assert any("above your 0.65% rule" in w for w in big.warnings)
    tiny = analyse_position(EURUSD, 100_000, 1.10, balance=1_000, risk_pct=0.65, broker=broker, conv=conv, ccy="GBP")
    assert tiny.free_margin < 0 and any("free margin" in w for w in tiny.warnings)


# ------------------------------------------------------------------ trade log in GBP
def test_trade_log_pnl_converted_to_gbp():
    raw = pd.DataFrame({"Open Time": ["2026-01-14 08:05"] * 3, "Close Time": ["2026-01-14 09:05"] * 3,
                        "Symbol": ["EURUSD", "GBPUSD", "USDJPY"], "Type": ["Buy", "Sell", "Buy"],
                        "Units": [10_000] * 3, "Open Price": [1.1000, 1.2600, 150.00],
                        "Close Price": [1.1050, 1.2500, 151.50], "S/L": [1.0980, 1.2620, 149.50],
                        "Commission": [0.5, 0.5, 0.5]})
    res = build_trades(raw, guess_trade_mapping(raw.columns), account_ccy="GBP", conv=FxConverter(reference=dict(REF)))
    t = res.trades.set_index("symbol")
    assert t.loc["EURUSD", "pnl"] == pytest.approx(50 / 1.25 - 0.5)          # $50 at reference GBPUSD
    assert t.loc["GBPUSD", "pnl"] == pytest.approx(100 / 1.25 - 0.5)         # $100 at its own exit price 1.25
    assert t.loc["USDJPY", "pnl"] == pytest.approx(15_000 / 151.5 / 1.25 - 0.5)  # yen -> USD (exit) -> GBP
    assert t.loc["EURUSD", "r_multiple"] == pytest.approx((40 - 0.5) / (20 * 0.8))
    assert (t["pnl_currency"] == "GBP").all()
    assert "reference_rates" in {i.code for i in res.issues}


# ------------------------------------------------------------------ settings and sessions
def test_settings_roundtrip_and_migration(tmp_path):
    p = tmp_path / "s.json"
    s = Settings(account_balance=2_500, active_broker="IG")
    save_settings(s, p)
    back = load_settings(p)
    assert back.account_balance == 2_500 and back.broker().name == "IG"
    assert back.account_currency == "GBP" and back.forex_fixed_units == 10_000
    p.write_text('{"account_size": 5000}')
    assert load_settings(p).account_balance == 5000  # old settings file still works


def test_session_overlaps_in_uk_time():
    winter = session_times_today("Europe/London", pd.Timestamp("2026-01-15"))
    assert winter["New York"] == ("13:00", "22:00") and winter["Asia (Tokyo)"] == ("00:00", "09:00")
    gap_weeks = session_times_today("Europe/London", pd.Timestamp("2026-03-20"))  # US on DST, UK not yet
    assert gap_weeks["New York"] == ("12:00", "21:00")
    labels = session_label(pd.to_datetime(["2026-01-15 08:30", "2026-01-15 14:00", "2026-01-15 11:00",
                                           "2026-01-17 12:00"], utc=True))
    assert labels == ["Asia/London overlap", "London/New York overlap", "London session", "outside the main sessions"]
