"""pool(): the project's central claim, and the boundaries that falsify it.

The claim is that aggregating imperfectly-correlated sites yields a firm
promise LARGER than the sum of the per-site conservative quantiles. Two things
make that claim testable rather than decorative:

* the baseline is the sum of the individual q05 columns, recomputed in the
  test from the fixture, never read back from the implementation; and
* both boundaries are pinned -- perfect correlation must collapse the answer
  onto that baseline, perfect anti-correlation must lift it to a value the
  test derives analytically.

A benefit that only ever came out positive because the code chose its own
baseline would be unfalsifiable, which is the failure mode this lane has
already shipped once (#24's penalty_bind_rate).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.market import api

from .conftest import (
    constant_matrix,
    daily_shape,
    factor_portfolio,
    quantile_frame,
    utc_index,
)


# ---------------------------------------------------------------------------
# surface
# ---------------------------------------------------------------------------

def test_pool_returns_the_contract_frame_on_a_tz_aware_15min_grid(diversified):
    out = api.pool(diversified.firm, method="gaussian_copula", seed=0)

    assert list(out.columns) == ["t", "pool_firm_kw"]
    assert str(out["t"].dtype) == "datetime64[ns, UTC]"
    assert out["t"].is_monotonic_increasing
    assert not out["t"].duplicated().any()
    assert len(out) == len(diversified.t)
    assert set(out["t"].diff().dropna().dt.total_seconds()) == {15 * 60.0}


@pytest.mark.parametrize("method", ["sum", "empirical", "gaussian_copula"])
def test_pool_is_positive_and_finite(diversified, method):
    out = api.pool(diversified.firm, method=method, seed=0)
    assert np.isfinite(out["pool_firm_kw"]).all()
    assert (out["pool_firm_kw"] > 0).all()


def test_pool_rejects_an_unknown_method(diversified):
    with pytest.raises(api.MarketError, match="unknown pool method"):
        api.pool(diversified.firm, method="magic", seed=0)


def test_pool_refuses_a_site_it_does_not_have(diversified):
    with pytest.raises(api.MarketError, match="absent from the firm frame"):
        api.pool(diversified.firm, method="sum", sites=["s0", "nope"], seed=0)


def test_pool_refuses_a_ragged_panel(diversified):
    firm = diversified.firm
    ragged = firm.drop(firm.index[(firm["site_id"] == "s0")][:3])
    with pytest.raises(api.MarketError, match="ragged panel"):
        api.pool(ragged, method="sum", seed=0)


def test_pool_refuses_nan_firm_kw(diversified):
    firm = diversified.firm.copy()
    firm.loc[firm.index[0], "firm_kw"] = np.nan
    with pytest.raises(api.MarketError, match="NaN firm_kw"):
        api.pool(firm, method="sum", seed=0)


def test_pool_refuses_to_invent_a_spread_from_a_single_quantile(diversified):
    """Aggregating distributions needs the whole marginal curve.

    Given only `firm_kw`, the only way to produce a pooled distribution is to
    make a spread up -- which would turn the diversification benefit into an
    assumption. It must refuse rather than guess.
    """
    stripped = diversified.firm[["t", "site_id", "firm_kw"]].copy()
    with pytest.raises(api.MarketError, match="marginal quantile curve"):
        api.pool(stripped, method="gaussian_copula", seed=0)


# ---------------------------------------------------------------------------
# boundary case 1: perfect correlation must collapse onto the naive sum
# ---------------------------------------------------------------------------

def _comonotonic_portfolio(n_days: int = 4, sigma: float = 50.0):
    """Three sites driven by one identical shock: correlation is exactly 1."""
    t = utc_index(n_days, start="2026-01-05T00:00:00Z")
    rng = np.random.default_rng(101)
    shock = rng.normal(0.0, 1.0, len(t))
    shape = daily_shape(n_days)
    mu = {s: 600.0 + shape + 40.0 * shock for s in ("a", "b", "c")}
    q = quantile_frame(mu, sigma, t)
    return q, api.firm_capacity(q, tau=0.05)


@pytest.mark.parametrize("method", ["empirical", "gaussian_copula"])
def test_perfect_correlation_collapses_the_pool_onto_the_sum(method):
    """Comonotonic sites diversify nothing: VaR is additive, so pool == sum.

    This is the boundary that makes the claim falsifiable. If a pool of three
    sites that move as one still reported a benefit, the benefit would be an
    artefact of the aggregation rather than a property of the portfolio.
    """
    quantiles, firm = _comonotonic_portfolio()
    expected = quantiles.groupby("t")["q05"].sum().sort_index().to_numpy()

    pooled = api.pool(firm, method=method, seed=0)
    got = pooled["pool_firm_kw"].to_numpy()

    # guard the guard: the fixture really is comonotonic, and the sum is not
    # trivially zero (so "equal to the sum" is a real constraint).
    assert pooled.attrs["mean_pairwise_correlation"] == pytest.approx(1.0, abs=1e-9)
    assert expected.min() > 100.0

    rel = np.abs(got - expected) / expected
    assert rel.max() < 1e-3, f"comonotonic pool departs from the sum by {rel.max():.4%}"
    benefit = float(np.mean(got - expected))
    assert abs(benefit) < 0.001 * float(np.mean(expected)), (
        f"perfectly correlated sites reported a diversification benefit of {benefit:.3f} kW"
    )


# ---------------------------------------------------------------------------
# boundary case 2: perfect anti-correlation, with an analytic expectation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method", ["empirical", "gaussian_copula"])
def test_perfect_anti_correlation_lifts_the_pool_to_the_sum_of_medians(method):
    """Two antithetic sites with equal spread: their total is deterministic.

    Site a draws mu_a + sigma*Z, site b draws mu_b - sigma*Z, so a + b is
    exactly mu_a + mu_b at every t whatever Z does. The pool's 5th percentile
    is therefore the sum of the two MEDIANS -- a number this test computes from
    the fixture's q50 columns, with no reference to anything `pool` returned.
    """
    n_days = 5
    t = utc_index(n_days, start="2026-02-02T00:00:00Z")
    rng = np.random.default_rng(13)
    z = rng.normal(0.0, 1.0, len(t))
    shape = daily_shape(n_days)
    mu = {"a": 600.0 + shape + 40.0 * z, "b": 600.0 - shape - 40.0 * z}
    quantiles = quantile_frame(mu, sigma=50.0, t=t)
    firm = api.firm_capacity(quantiles, tau=0.05)

    expected = quantiles.groupby("t")["q50"].sum().sort_index().to_numpy()
    naive_sum = quantiles.groupby("t")["q05"].sum().sort_index().to_numpy()

    pooled = api.pool(firm, method=method, seed=0)
    got = pooled["pool_firm_kw"].to_numpy()

    assert pooled.attrs["mean_pairwise_correlation"] == pytest.approx(-1.0, abs=1e-6)
    # guard the guard: there is a real gap to find, so "close to the medians"
    # is not the same statement as "close to the naive sum".
    assert float(np.mean(expected - naive_sum)) > 100.0

    rel = np.abs(got - expected) / expected
    assert rel.max() < 5e-3, f"antithetic pool missed its analytic value by {rel.max():.3%}"


# ---------------------------------------------------------------------------
# boundary case 3: a one-site "pool" is that site
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method", ["sum", "empirical", "gaussian_copula"])
def test_single_site_pool_is_exactly_that_sites_firm_capacity(diversified, method):
    """Not "within tolerance": exactly. There is nothing to aggregate, so no
    estimator is allowed anywhere near the answer -- a one-site pool that only
    approximated the site's own promise would mean the Monte Carlo had leaked
    into a case that has a closed form."""
    one = diversified.firm[diversified.firm["site_id"] == "s0"]
    expected = one.sort_values("t")["firm_kw"].to_numpy()

    pooled = api.pool(diversified.firm, method=method, sites=["s0"], seed=0)

    assert np.array_equal(pooled["pool_firm_kw"].to_numpy(), expected)
    assert pooled.attrs["single_site_exact"] is True
    assert pooled.attrs["n_draws"] == 0
    # and the seed cannot move a closed form
    other = api.pool(diversified.firm, method=method, sites=["s0"], seed=7)
    assert np.array_equal(other["pool_firm_kw"].to_numpy(), expected)


# ---------------------------------------------------------------------------
# the contract guarantee, over a seed sweep
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method", ["empirical", "gaussian_copula"])
def test_pool_is_never_below_the_naive_sum_across_seeds(diversified, method):
    """`pool(method='empirical') >= pool(method='sum')` at EVERY t, on a sweep.

    The baseline is recomputed from the fixture's own q05 columns rather than
    from `pool(method='sum')`, so a bug shared by both paths cannot hide.
    """
    baseline = diversified.sum_of_individual_quantiles_kw()
    assert np.allclose(
        baseline, api.pool(diversified.firm, method="sum", seed=0)["pool_firm_kw"].to_numpy()
    ), "method='sum' is not the sum of the per-site q05 columns"

    benefits = []
    for seed in range(8):
        got = api.pool(diversified.firm, method=method, seed=seed)["pool_firm_kw"].to_numpy()
        worst = float(np.min(got - baseline))
        assert worst >= -1e-9, f"seed {seed}: pool fell {abs(worst):.3f} kW below the naive sum"
        benefits.append(float(np.mean(got - baseline)))

    # guard the guard: an assertion that only ever compares equal numbers is
    # not evidence that the aggregation does anything.
    assert min(benefits) > 0.02 * float(np.mean(baseline)), (
        f"the >= guarantee held only because the benefit was ~0: {benefits}"
    )


def test_the_benefit_is_measured_against_the_sum_not_against_an_internal_baseline(diversified):
    """`diversification_benefit_kw_mean` must equal (pool - externally computed sum)."""
    baseline = diversified.sum_of_individual_quantiles_kw()
    pooled = api.pool(diversified.firm, method="gaussian_copula", seed=0)
    expected = float(np.mean(pooled["pool_firm_kw"].to_numpy() - baseline))
    assert pooled.attrs["diversification_benefit_kw_mean"] == pytest.approx(expected, abs=1e-6)
    assert pooled.attrs["sum_firm_kw_mean"] == pytest.approx(float(np.mean(baseline)), abs=1e-6)


# ---------------------------------------------------------------------------
# the benefit is DERIVED from the correlation: it has to move when rho moves
# ---------------------------------------------------------------------------

def test_diversification_benefit_falls_monotonically_as_correlation_rises(diversified):
    """Same sites, same marginals, same seed -- only the correlation changes.

    This is the acceptance criterion that stops `gaussian_copula` being `sum`
    with a fudge factor: if the benefit were anything other than a function of
    the dependence structure, it could not track rho across the whole range.
    Both ends are pinned in one test: ~0 at rho -> 1, wide open at rho = 0.
    """
    baseline = diversified.sum_of_individual_quantiles_kw()
    mean_sum = float(np.mean(baseline))
    benefits = {}
    for rho in (0.0, 0.3, 0.6, 0.9, 0.999):
        pooled = api.pool(diversified.firm, method="gaussian_copula", seed=0,
                          correlation=constant_matrix(diversified.site_ids, rho))
        benefits[rho] = float(np.mean(pooled["pool_firm_kw"].to_numpy() - baseline))

    ordered = [benefits[r] for r in (0.0, 0.3, 0.6, 0.9, 0.999)]
    assert ordered == sorted(ordered, reverse=True), f"benefit not monotone in rho: {benefits}"
    assert benefits[0.999] < 0.005 * mean_sum, (
        f"near-comonotonic sites still showed a {benefits[0.999] / mean_sum:.2%} benefit"
    )
    assert benefits[0.0] > 0.05 * mean_sum, (
        f"near-independent sites showed only a {benefits[0.0] / mean_sum:.2%} benefit"
    )
    assert benefits[0.0] > 5.0 * benefits[0.9], "the benefit barely moved with the correlation"


def test_pool_uses_the_supplied_matrix_rather_than_measuring_its_own(diversified):
    pooled = api.pool(diversified.firm, method="gaussian_copula", seed=0,
                      correlation=constant_matrix(diversified.site_ids, 0.8))
    assert pooled.attrs["correlation_source"] == "supplied:mapping"
    assert pooled.attrs["mean_pairwise_correlation"] == pytest.approx(0.8)

    measured = api.pool(diversified.firm, method="gaussian_copula", seed=0)
    assert measured.attrs["correlation_source"] == "measured:firm_kw residuals"
    # the fixture was built at rho = 0.3, so a supplied 0.8 must not agree
    assert measured.attrs["mean_pairwise_correlation"] < 0.5
    assert not np.allclose(pooled["pool_firm_kw"], measured["pool_firm_kw"])


def test_pool_refuses_a_correlation_that_does_not_cover_every_site(diversified):
    partial = constant_matrix(diversified.site_ids[:-1], 0.3)
    with pytest.raises(api.MarketError, match="missing site"):
        api.pool(diversified.firm, method="gaussian_copula", seed=0, correlation=partial)


def test_empirical_refuses_a_bare_matrix_because_it_needs_the_joint_ranks(diversified):
    with pytest.raises(api.MarketError, match="joint ranks"):
        api.pool(diversified.firm, method="empirical", seed=0,
                 correlation=constant_matrix(diversified.site_ids, 0.3))


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method", ["empirical", "gaussian_copula"])
def test_same_seed_reproduces_the_frame_and_different_seeds_do_not(diversified, method):
    a = api.pool(diversified.firm, method=method, seed=3)
    b = api.pool(diversified.firm, method=method, seed=3)
    pd.testing.assert_frame_equal(a, b)

    c = api.pool(diversified.firm, method=method, seed=4)
    assert not np.array_equal(a["pool_firm_kw"].to_numpy(), c["pool_firm_kw"].to_numpy()), (
        "the seed does not reach the draws: two seeds gave identical answers"
    )


def test_sum_is_seed_independent_because_it_does_not_sample(diversified):
    a = api.pool(diversified.firm, method="sum", seed=0)
    b = api.pool(diversified.firm, method="sum", seed=99)
    pd.testing.assert_frame_equal(a, b)
    assert a.attrs["n_draws"] == 0


# ---------------------------------------------------------------------------
# every coercion observable, pinned at BOTH ends
# ---------------------------------------------------------------------------

def _near_comonotonic_forcing_portfolio():
    """Near-comonotonic sites: the only regime where the sample quantile can
    land under the naive sum, because there the true answer IS the sum."""
    return factor_portfolio(n_sites=3, n_days=3, rho=0.97, seed=5,
                            base=[500.0, 700.0, 1200.0], sigma=45.0)


def test_sum_floor_bind_rate_moves_between_a_forcing_and_an_avoiding_fixture(diversified):
    forcing = _near_comonotonic_forcing_portfolio()
    bound = api.pool(forcing.firm, method="gaussian_copula", seed=2, n_draws=100)
    assert bound.attrs["sum_floor_bind_rate"] > 0.0, (
        "no fixture could make the sum floor bind -- a counter pinned at zero is not evidence"
    )
    assert bound.attrs["sum_floor_max_gap_kw"] > 0.0
    # it is estimator noise being floored, not an aggregation failure
    assert bound.attrs["sum_floor_max_gap_kw"] < 0.05 * bound.attrs["sum_firm_kw_mean"]

    avoided = api.pool(diversified.firm, method="gaussian_copula", seed=0)
    assert avoided.attrs["sum_floor_bind_rate"] == 0.0
    assert avoided.attrs["sum_floor_max_gap_kw"] == 0.0


def test_a_material_gap_raises_instead_of_being_floored_away(monkeypatch):
    """The refuse-rather-than-float branch is live, not decoration."""
    forcing = _near_comonotonic_forcing_portfolio()
    monkeypatch.setattr(api, "_SUM_FLOOR_MATERIAL_REL", 1e-9)
    with pytest.raises(api.MarketError, match="BELOW the naive sum"):
        api.pool(forcing.firm, method="gaussian_copula", seed=2, n_draws=100)


def test_a_near_zero_capacity_interval_does_not_fake_a_material_gap():
    """Regression: materiality is judged against what the pool SELLS.

    A small site whose 5th percentile lands near zero makes one interval where
    the naive sum is a couple of kilowatts. Judged against that interval's own
    sum, a fraction of a kilowatt of Monte-Carlo noise is a 30% relative gap,
    and `pool` raised "aggregation bug" on a healthy portfolio -- reproducibly
    inside `diversification_curve`, which pools at the smaller draw count.
    """
    low = factor_portfolio(n_sites=2, n_days=4, rho=0.995, seed=2,
                           base=[100.0, 200.0], sigma=40.0, shock_kw=30.0,
                           shape_kw=60.0)
    baseline = low.sum_of_individual_quantiles_kw()
    # guard the guard: the fixture must actually contain a near-zero interval,
    # otherwise it cannot exercise the bug it is here to pin. This one dips to
    # ~4 kW against a ~175 kW mean.
    assert baseline.min() < 0.05 * baseline.mean()
    assert baseline.min() < 10.0

    pooled = api.pool(low.firm, method="gaussian_copula", seed=3, n_draws=2000)
    assert (pooled["pool_firm_kw"].to_numpy() >= baseline - 1e-9).all()
    # the noise that used to be called an aggregation bug is a rounding error
    # next to what this pool actually sells
    assert pooled.attrs["sum_floor_max_gap_kw"] < 0.02 * float(baseline.mean())


def test_draw_clip_rate_moves_between_a_forcing_and_an_avoiding_fixture(diversified):
    """Draws below zero are clipped (no site delivers negative reduction);
    the rate has to be able to move, or it is not a measurement."""
    # a site promising ~1 kW with a ~3.5 kW forecast spread: the simulated
    # left tail runs through zero, so the clip has something to do.
    tiny = factor_portfolio(n_sites=3, n_days=3, rho=0.2, seed=17,
                            base=6.0, sigma=3.5, shock_kw=0.5, shape_kw=1.0)
    clipped = api.pool(tiny.firm, method="gaussian_copula", seed=0)
    assert clipped.attrs["draw_clip_rate"] > 0.01

    roomy = api.pool(diversified.firm, method="gaussian_copula", seed=0)
    assert roomy.attrs["draw_clip_rate"] == 0.0


def test_correlation_psd_repair_moves_between_a_forcing_and_an_avoiding_fixture(diversified):
    ids = diversified.site_ids[:3]
    firm = diversified.firm[diversified.firm["site_id"].isin(ids)]

    # -0.9 everywhere off-diagonal is not a possible correlation matrix for
    # three variables: the smallest eigenvalue is negative.
    indefinite = pd.DataFrame(
        np.where(np.eye(3, dtype=bool), 1.0, -0.9), index=ids, columns=ids
    )
    repaired = api.pool(firm, method="gaussian_copula", seed=0, correlation=indefinite)
    assert repaired.attrs["correlation_psd_repair"] > 0.1

    proper = pd.DataFrame(np.where(np.eye(3, dtype=bool), 1.0, 0.3), index=ids, columns=ids)
    untouched = api.pool(firm, method="gaussian_copula", seed=0, correlation=proper)
    assert untouched.attrs["correlation_psd_repair"] == 0.0


def test_pool_refuses_a_non_finite_correlation(diversified):
    ids = diversified.site_ids[:3]
    firm = diversified.firm[diversified.firm["site_id"].isin(ids)]
    nan_matrix = pd.DataFrame(np.where(np.eye(3, dtype=bool), 1.0, np.nan), index=ids, columns=ids)
    with pytest.raises(api.MarketError, match="non-finite"):
        api.pool(firm, method="gaussian_copula", seed=0, correlation=nan_matrix)


def test_pool_refuses_a_site_with_no_residual_variation(diversified):
    """Zero variance means undefined correlation. Treating it as independent
    would be the flattering error -- it overstates the pooled promise."""
    firm = diversified.firm.copy()
    flat = firm["site_id"] == "s0"
    for col in [c for c in firm.columns if c.startswith("firm_q")] + ["firm_kw"]:
        firm.loc[flat, col] = 400.0
    with pytest.raises(api.MarketError, match="zero residual variation"):
        api.pool(firm, method="gaussian_copula", seed=0)
