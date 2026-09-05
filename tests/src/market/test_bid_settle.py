"""bid(): rounds down, drops under minimum. settle(): can go negative, penalises shortfall,
and is arithmetically consistent on every row."""

import numpy as np
import pandas as pd
import pytest

from src.market import api


def _pool_firm(values: list[float], start: str = "2026-05-04T00:00:00Z") -> pd.DataFrame:
    """One 4h aFRR block's worth of 15-min pool_firm_kw rows (16 rows)."""
    t = pd.date_range(start, periods=len(values), freq="15min")
    df = pd.DataFrame({"t": t, "pool_firm_kw": values})
    return df


def _flat_prices(t: pd.DatetimeIndex, capacity_price=20.0, energy_price=80.0) -> pd.DataFrame:
    return pd.DataFrame({
        "t": t,
        "capacity_price_eur_mw_h": capacity_price,
        "energy_price_eur_mwh": energy_price,
    })


def test_bid_rounds_down_never_up():
    # 16 rows all at 2499 kW: floor to 1 MW granularity -> 2000 kW, never 3000
    values = [2499.0] * 16
    pf = _pool_firm(values)
    prices = _flat_prices(pf["t"])
    out = api.bid(pf, "aFRR", prices)
    assert len(out) == 1
    assert out["capacity_kw"].iloc[0] == 2000.0
    assert out.attrs["blocks_rounddown_bind_rate"] == 1.0  # guard: rounding actually bound


def test_bid_drops_blocks_below_minimum():
    values = [500.0] * 16  # below aFRR's 1000 kW minimum bid
    pf = _pool_firm(values)
    prices = _flat_prices(pf["t"])
    out = api.bid(pf, "aFRR", prices)
    assert len(out) == 0
    assert out.attrs["blocks_dropped_below_minimum"] == 1
    assert out.attrs["blocks_dropped_rate"] == 1.0  # guard: the drop actually happened


def test_bid_sizes_off_the_blocks_worst_sub_interval():
    values = [5000.0] * 15 + [1200.0]  # one bad quarter-hour must cap the whole block
    pf = _pool_firm(values)
    prices = _flat_prices(pf["t"])
    out = api.bid(pf, "aFRR", prices)
    assert out["capacity_kw"].iloc[0] == 1000.0  # floor(1200/1000)*1000, not floor(5000/1000)*1000


def test_bid_unknown_product_rejected():
    pf = _pool_firm([2000.0] * 16)
    prices = _flat_prices(pf["t"])
    with pytest.raises(api.MarketError):
        api.bid(pf, "not-a-product", prices)


def _settle_fixture(capacity_kw: float, delivered_kw: float):
    t = pd.date_range("2026-05-04T00:00:00Z", periods=16, freq="15min")
    values = [capacity_kw / 1000.0 * 1000.0 + 1.0] * 16  # comfortably above the target granularity
    pf = _pool_firm(values, start="2026-05-04T00:00:00Z")
    prices = _flat_prices(t)
    bids = api.bid(pf, "aFRR", prices)
    assert len(bids) == 1
    delivered = pd.DataFrame({"t": t, "delivered_kw": [delivered_kw] * 16})
    settled = api.settle(bids, delivered, prices)
    return bids, settled


def test_settle_over_delivery_yields_zero_penalty():
    # capacity price high relative to energy price so the profitable case is realistic:
    # a capacity-market payment for standing ready, a comparatively cheap activation cost.
    t = pd.date_range("2026-05-04T00:00:00Z", periods=16, freq="15min")
    pf = _pool_firm([2001.0] * 16)
    prices = _flat_prices(t, capacity_price=200.0, energy_price=10.0)
    bids = api.bid(pf, "aFRR", prices)
    delivered = pd.DataFrame({"t": t, "delivered_kw": [2500.0] * 16})
    settled = api.settle(bids, delivered, prices)
    row = settled.iloc[0]
    assert row["penalty_eur"] == 0.0, "over-delivery must never be penalised"
    assert row["net_eur"] > 0.0, "a profitable, always-honoured promise must show a net gain"


def test_settle_shortfall_is_penalised_and_net_goes_negative():
    """Deliberate shortfall must actually cost money -- the core acceptance bullet."""
    bids, settled = _settle_fixture(capacity_kw=2000.0, delivered_kw=0.0)  # never delivers
    row = settled.iloc[0]
    assert row["penalty_eur"] > 0.0, "a full shortfall must be penalised, not silently absorbed"
    assert row["net_eur"] < 0.0, "a settlement that never delivers must show a net loss"
    assert settled.attrs["penalty_bind_rate"] == 1.0  # guard: the penalty actually bound


def test_settle_arithmetic_identity_holds_on_every_row():
    bids, settled = _settle_fixture(capacity_kw=2000.0, delivered_kw=1000.0)  # partial shortfall
    for row in settled.itertuples(index=False):
        assert row.net_eur == pytest.approx(
            row.capacity_revenue_eur - row.energy_cost_eur - row.penalty_eur
        )


def test_settle_missing_delivery_record_is_rejected_not_defaulted():
    t = pd.date_range("2026-05-04T00:00:00Z", periods=16, freq="15min")
    pf = _pool_firm([2000.0] * 16)
    prices = _flat_prices(t)
    bids = api.bid(pf, "aFRR", prices)
    empty_delivered = pd.DataFrame({"t": pd.Series([], dtype="datetime64[ns, UTC]"), "delivered_kw": pd.Series([], dtype="float64")})
    with pytest.raises(api.MarketError):
        api.settle(bids, empty_delivered, prices)
