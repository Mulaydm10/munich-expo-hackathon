"""Regression tests for PR #24 / issue claim/14: five review-bot-flagged bugs in bid()/settle()/
energy_cost()/co2(), reproduced with inputs constructed to *maximally* express each defect
(not convenient ones), plus the mislabel-guard telemetry gap design flagged in parallel review.

Each test below fails against the pre-fix api.py and passes against the fixed one -- see the
task report for the verbatim red/green pytest output.
"""

import numpy as np
import pandas as pd
import pytest

from src.market import api


def _full_block(capacity_kw_target: float = 2001.0, start: str = "2026-05-04T00:00:00Z"):
    """One full, 16-row (4h @ 15min) aFRR block, bid at capacity_kw_target (pre-rounddown)."""
    t = pd.date_range(start, periods=16, freq="15min")
    pf = pd.DataFrame({"t": t, "pool_firm_kw": [capacity_kw_target] * 16})
    prices = pd.DataFrame({
        "t": t,
        "capacity_price_eur_mw_h": 20.0,
        "energy_price_eur_mwh": 80.0,
    })
    bids = api.bid(pf, "aFRR", prices)
    assert len(bids) == 1
    return t, bids, prices


# ---------------------------------------------------------------------------
# Claim 2: interval shortfalls cancel out
# ---------------------------------------------------------------------------

def test_claim2_intra_block_shortfalls_do_not_cancel_on_average():
    """Maximal expression of the defect: half the block delivers 2x capacity, the other half
    delivers ZERO -- same total/mean as delivering exactly at capacity every interval, so a
    mean-based settlement reports zero penalty despite a real, total non-delivery on 8 of 16
    quarter-hours. Alternating (not blocked) over/under so no contiguous-run assumption can
    accidentally save the naive implementation."""
    t, bids, prices = _full_block()
    capacity_kw = float(bids["capacity_kw"].iloc[0])
    assert capacity_kw == 2000.0
    delivered_vals = [4000.0, 0.0] * 8
    assert np.mean(delivered_vals) == pytest.approx(capacity_kw)  # a mean-based check would see "on average, fully delivered"
    delivered = pd.DataFrame({"t": t, "delivered_kw": delivered_vals})
    settled = api.settle(bids, delivered, prices)
    row = settled.iloc[0]
    # 8 intervals x 2000 kW shortfall x 0.25h = 4000 kWh; @ 80 EUR/MWh x 3.0x multiplier
    # = 0.24 EUR/kWh -> 960 EUR. Any nonzero value proves the cancellation didn't happen;
    # the exact figure proves the per-interval accounting is right, not just nonzero-by-luck.
    assert row["penalty_eur"] == pytest.approx(960.0), (
        "over-delivery in half the intervals must not offset a real, total non-delivery in "
        "the other half -- the promise is per-interval, not a block-level average"
    )
    assert row["net_eur"] < 0.0
    assert settled.attrs["penalty_bind_rate"] == 1.0


def test_claim2_sweep_random_over_under_splits_same_mean_always_penalised():
    """Parameter sweep: 10 seeds, each constructing a different random over/under-delivery
    split with the SAME block mean (== capacity), to rule out the single fixture above being a
    coincidence of that particular split. Every seed must show a nonzero penalty."""
    t, bids, prices = _full_block()
    capacity_kw = float(bids["capacity_kw"].iloc[0])
    for seed in range(10):
        rng = np.random.default_rng(seed)
        over = rng.uniform(3000.0, 6000.0, 8)
        under_total = 16 * capacity_kw - over.sum()
        under = rng.dirichlet(np.ones(8)) * under_total
        vals = np.concatenate([over, under])
        rng.shuffle(vals)
        assert np.mean(vals) == pytest.approx(capacity_kw)
        delivered = pd.DataFrame({"t": t, "delivered_kw": vals})
        settled = api.settle(bids, delivered, prices)
        assert settled.iloc[0]["penalty_eur"] > 0.0, f"seed={seed} produced zero penalty despite a same-mean over/under split with real shortfalls"


# ---------------------------------------------------------------------------
# Claim 3: incomplete blocks are bid
# ---------------------------------------------------------------------------

def test_claim3_partial_forecast_coverage_is_rejected_not_bid():
    """Maximal expression: only 4 of the 16 required 15-min rows exist (1h of a 4h product),
    so a full 4h commitment would be sold against a forecast that says nothing about 3h of it."""
    t = pd.date_range("2026-05-04T00:00:00Z", periods=4, freq="15min")
    pf = pd.DataFrame({"t": t, "pool_firm_kw": [2001.0] * 4})
    prices = pd.DataFrame({"t": t, "capacity_price_eur_mw_h": 20.0, "energy_price_eur_mwh": 80.0})
    with pytest.raises(api.MarketError):
        api.bid(pf, "aFRR", prices)


def test_claim3_sweep_coverage_fractions_all_rejected_except_full():
    """Sweep 1..15 of the 16 required rows (every incomplete fraction), plus the full 16 as a
    negative control that must still succeed -- proves the guard is exact-grid, not merely
    'at least N rows'."""
    full_t = pd.date_range("2026-05-04T00:00:00Z", periods=16, freq="15min")
    for n_rows in range(1, 16):
        t = full_t[:n_rows]
        pf = pd.DataFrame({"t": t, "pool_firm_kw": [2001.0] * n_rows})
        prices = pd.DataFrame({"t": t, "capacity_price_eur_mw_h": 20.0, "energy_price_eur_mwh": 80.0})
        with pytest.raises(api.MarketError):
            api.bid(pf, "aFRR", prices)
    pf_full = pd.DataFrame({"t": full_t, "pool_firm_kw": [2001.0] * 16})
    prices_full = pd.DataFrame({"t": full_t, "capacity_price_eur_mw_h": 20.0, "energy_price_eur_mwh": 80.0})
    out = api.bid(pf_full, "aFRR", prices_full)  # negative control: full coverage still bids fine
    assert len(out) == 1


# ---------------------------------------------------------------------------
# Claim 4: partial delivery settles full blocks
# ---------------------------------------------------------------------------

def test_claim4_partial_delivery_coverage_is_rejected_not_stretched_over_the_block():
    """Maximal expression: only the FIRST 15 minutes of a 4h block has a delivery reading at
    all (the remaining 3h45m has none), and that one reading is at 100% of capacity. A
    mean-over-available-rows settlement would report full delivery (zero penalty, full
    capacity revenue) for a block that is 15/240 minutes reported."""
    t, bids, prices = _full_block()
    delivered_one_row = pd.DataFrame({"t": t[:1], "delivered_kw": [2000.0]})
    with pytest.raises(api.MarketError):
        api.settle(bids, delivered_one_row, prices)


def test_claim4_sweep_coverage_fractions_all_rejected_except_full():
    t, bids, prices = _full_block()
    for n_rows in range(1, 16):
        delivered = pd.DataFrame({"t": t[:n_rows], "delivered_kw": [2000.0] * n_rows})
        with pytest.raises(api.MarketError):
            api.settle(bids, delivered, prices)
    delivered_full = pd.DataFrame({"t": t, "delivered_kw": [2000.0] * 16})
    settled = api.settle(bids, delivered_full, prices)  # negative control: full coverage settles fine
    assert settled.iloc[0]["penalty_eur"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Claim 5: negative prices reverse penalties
# ---------------------------------------------------------------------------

def test_claim5_negative_price_never_reduces_penalty_below_positive_price_case():
    """Maximal expression: total non-delivery (0 kW against a full commitment) settled once at
    +80 EUR/MWh and once at -80 EUR/MWh (a real German day-ahead value, and correlated with
    exactly the low-demand hours a downward-flexibility product would be dispatched in). A
    sign-preserving penalty calculation turns the second case into a net-positive reward for
    total non-delivery -- this must not happen: same magnitude price must give the same
    (non-negative) penalty regardless of sign."""
    t, bids, prices_pos = _full_block()
    prices_neg = prices_pos.assign(energy_price_eur_mwh=-80.0)
    delivered_zero = pd.DataFrame({"t": t, "delivered_kw": [0.0] * 16})

    settled_pos = api.settle(bids, delivered_zero, prices_pos)
    settled_neg = api.settle(bids, delivered_zero, prices_neg)

    assert settled_pos.iloc[0]["penalty_eur"] > 0.0  # guard: the positive-price case is a real, nonzero penalty
    assert settled_neg.iloc[0]["penalty_eur"] == pytest.approx(settled_pos.iloc[0]["penalty_eur"]), (
        "a negative energy price must not shrink or reverse the penalty for the identical shortfall"
    )
    assert settled_neg.iloc[0]["net_eur"] == pytest.approx(settled_pos.iloc[0]["net_eur"]), (
        "total non-delivery must not become more profitable just because the price went negative"
    )
    # bind-rate telemetry must reflect the physical shortfall, not the sign of the resulting euro amount
    assert settled_neg.attrs["penalty_bind_rate"] == 1.0
    assert settled_pos.attrs["penalty_bind_rate"] == 1.0
    # pinned at both ends: the clamp fires for the negative-price fixture and does not for the positive one
    assert settled_neg.attrs["negative_energy_price_rate"] == 1.0
    assert settled_pos.attrs["negative_energy_price_rate"] == 0.0


def test_claim5_sweep_negative_price_magnitudes_and_partial_shortfalls():
    """Sweep price magnitude and shortfall fraction together: for every combination, the
    negative-price penalty must equal the same-magnitude positive-price penalty, and never go
    negative itself."""
    t, bids, prices_pos = _full_block()
    capacity_kw = float(bids["capacity_kw"].iloc[0])
    for price_mag in (1.0, 10.0, 80.0, 500.0, 3000.0):  # includes an extreme negative-price-event magnitude
        for delivered_frac in (0.0, 0.25, 0.5, 0.9):
            delivered_kw = capacity_kw * delivered_frac
            prices_pos_mag = prices_pos.assign(energy_price_eur_mwh=price_mag)
            prices_neg_mag = prices_pos.assign(energy_price_eur_mwh=-price_mag)
            delivered = pd.DataFrame({"t": t, "delivered_kw": [delivered_kw] * 16})
            settled_pos = api.settle(bids, delivered, prices_pos_mag)
            settled_neg = api.settle(bids, delivered, prices_neg_mag)
            assert settled_neg.iloc[0]["penalty_eur"] >= 0.0
            assert settled_neg.iloc[0]["penalty_eur"] == pytest.approx(settled_pos.iloc[0]["penalty_eur"]), (
                f"price_mag={price_mag} delivered_frac={delivered_frac}"
            )


def test_settle_penalty_multiplier_is_an_explicit_overridable_scenario_parameter():
    """penalty_multiplier is an invented placeholder (see PRODUCTS['aFRR'].source); it must be
    runnable as an explicit what-if scenario rather than baked in silently."""
    t, bids, prices = _full_block()
    delivered_zero = pd.DataFrame({"t": t, "delivered_kw": [0.0] * 16})

    settled_default = api.settle(bids, delivered_zero, prices)
    settled_override = api.settle(bids, delivered_zero, prices, penalty_multiplier=1.0)

    assert settled_default.attrs["penalty_multiplier"] == 3.0  # aFRR's stated default
    assert settled_override.attrs["penalty_multiplier"] == 1.0
    assert "override" in settled_override.attrs["penalty_multiplier_source"]
    assert settled_override.iloc[0]["penalty_eur"] == pytest.approx(
        settled_default.iloc[0]["penalty_eur"] / 3.0
    )


# ---------------------------------------------------------------------------
# Claim 6: missing loads return NaN
# ---------------------------------------------------------------------------

def test_claim6_energy_cost_rejects_nan_load_rather_than_returning_nan():
    t = pd.to_datetime(["2026-06-01T00:00:00Z", "2026-06-01T00:15:00Z"], utc=True)
    load = pd.DataFrame({"t": t, "load_kw": [100.0, float("nan")]})
    prices = pd.DataFrame({"t": t, "price_eur_mwh": [100.0, 100.0]})
    with pytest.raises(api.MarketError):
        api.energy_cost(load, prices)


def test_claim6_co2_rejects_nan_load_rather_than_returning_nan():
    t = pd.to_datetime(["2026-06-01T00:00:00Z", "2026-06-01T00:15:00Z"], utc=True)
    load = pd.DataFrame({"t": t, "load_kw": [100.0, float("nan")]})
    carbon = pd.DataFrame({"t": t, "intensity_g_kwh": [300.0, 300.0]})
    with pytest.raises(api.MarketError):
        api.co2(load, carbon)


def test_claim6_multi_site_nan_load_does_not_silently_sum_to_a_wrong_total():
    """The multi-site (groupby-sum) path is a distinct code path from the single-site one: a
    naive groupby().sum() has skipna=True by default and would silently treat a missing site's
    reading as zero rather than unknown, understating the true total instead of raising. This
    is a maximal-expression variant of claim 6, not just the single-site case."""
    t = pd.to_datetime(["2026-06-01T00:00:00Z"], utc=True)
    load = pd.DataFrame({
        "t": list(t) * 2,
        "site_id": ["A", "B"],
        "load_kw": [100.0, float("nan")],
    })
    prices = pd.DataFrame({"t": t, "price_eur_mwh": [100.0]})
    with pytest.raises(api.MarketError):
        api.energy_cost(load, prices)


# ---------------------------------------------------------------------------
# Bonus fix: firm_capacity's mislabel-guard rate was discarded below threshold
# ---------------------------------------------------------------------------

def test_lower_above_median_rate_is_exposed_even_when_it_does_not_trip_the_raise():
    """Pinned at both ends: a 40%-inverted frame must show lower_above_median_rate == 0.4 and
    must NOT raise (below the 0.5 threshold); a fully-clean frame must show 0.0. A rate that is
    only ever asserted where it binds cannot tell a 0% measurement from a broken one."""
    t = pd.date_range("2026-01-05T00:00:00Z", periods=10, freq="15min")
    q = pd.DataFrame({
        "t": t, "site_id": ["s"] * 10,
        "q05": [100.0] * 10, "q50": [50.0] * 4 + [200.0] * 6,  # 4/10 = 40% rows where q05 > q50
    })
    out = api.firm_capacity(q, tau=0.05)
    assert out.attrs["lower_above_median_rate"] == pytest.approx(0.4)

    q_clean = q.assign(q50=[200.0] * 10)  # q05 always below median now
    out_clean = api.firm_capacity(q_clean, tau=0.05)
    assert out_clean.attrs["lower_above_median_rate"] == 0.0
