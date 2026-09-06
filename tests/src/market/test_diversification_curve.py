"""diversification_curve(): the frame src/ui animates, and the claim it makes.

`firm_kw_per_site` rising with `n_sites` IS the pitch. Two ways that could be
fake, both tested against:

* the rise could be an artefact of the aggregation rather than a property of
  the portfolio -- so a perfectly-correlated portfolio must produce a FLAT
  curve, and the rise must be measured against the sum of individual quantiles
  computed here in the test; and
* `shortfall_rate` could be simulated from the same distribution that produced
  the promise, in which case it would read ~tau whatever happened. It is
  measured against realised load, and three fixtures pin it at 0, at ~tau and
  at 1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.market import api

from .conftest import factor_portfolio

SIZES = [1, 2, 3, 4, 6]


# ---------------------------------------------------------------------------
# surface -- the column names are load-bearing for #30
# ---------------------------------------------------------------------------

def test_curve_returns_exactly_the_contract_columns(diversified):
    out = api.diversification_curve(diversified.firm, sizes=SIZES, seed=0,
                                    realised=diversified.realised)
    assert list(out.columns) == ["n_sites", "firm_kw_per_site", "shortfall_rate"]
    assert out["n_sites"].tolist() == SIZES
    assert np.isfinite(out["firm_kw_per_site"]).all()
    assert ((out["shortfall_rate"] >= 0.0) & (out["shortfall_rate"] <= 1.0)).all()


def test_curve_refuses_to_run_without_realised_load(diversified):
    """shortfall_rate has no default source. Deriving it from the promise's own
    generator would pin it at ~tau by construction -- a number that cannot move
    is not evidence."""
    with pytest.raises(api.MarketError, match="must be measured against realised"):
        api.diversification_curve(diversified.firm, sizes=SIZES, seed=0)


def test_curve_refuses_a_size_the_portfolio_cannot_support(diversified):
    with pytest.raises(api.MarketError, match="outside 1..6"):
        api.diversification_curve(diversified.firm, sizes=[1, 9], seed=0,
                                  realised=diversified.realised)
    with pytest.raises(api.MarketError, match="outside 1..6"):
        api.diversification_curve(diversified.firm, sizes=[0], seed=0,
                                  realised=diversified.realised)


def test_curve_refuses_realised_that_does_not_cover_the_promise(diversified):
    short = diversified.realised[diversified.realised["t"] < diversified.t[10]]
    with pytest.raises(api.MarketError, match="does not cover every timestamp"):
        api.diversification_curve(diversified.firm, sizes=[2], seed=0, realised=short)

    holed = diversified.realised.copy()
    holed.loc[holed.index[3], "realised_kw"] = np.nan
    with pytest.raises(api.MarketError, match="NaN realised_kw"):
        api.diversification_curve(diversified.firm, sizes=[2], seed=0, realised=holed)


# ---------------------------------------------------------------------------
# the headline: the safe promise per site rises with portfolio size
# ---------------------------------------------------------------------------

def test_firm_kw_per_site_rises_with_portfolio_size(diversified):
    """The project's central claim, measured against the naive baseline.

    The n=1 point is checked against a number computed here -- the mean over
    sites of each site's own mean firm capacity -- because that is what a
    one-site "pool" must be, and it anchors the whole curve to something the
    implementation did not choose.
    """
    out = api.diversification_curve(diversified.firm, sizes=SIZES, seed=0,
                                    realised=diversified.realised,
                                    method="gaussian_copula")
    per_site = out.set_index("n_sites")["firm_kw_per_site"]

    expected_single = float(
        diversified.firm.groupby("site_id")["firm_kw"].mean().mean()
    )
    assert per_site[1] == pytest.approx(expected_single, rel=1e-9)

    values = per_site.reindex(SIZES).to_numpy()
    assert (np.diff(values) > 0).all(), f"curve does not rise: {values}"
    assert out.attrs["monotone_in_n_sites"] is True
    assert out.attrs["monotonicity_violations"] == []

    # the rise is material, not a rounding artefact
    rise = (values[-1] - values[0]) / values[0]
    assert rise > 0.05, f"the whole portfolio bought only {rise:.2%} per site"

    # and the top of the curve beats the naive sum by the same margin `pool` reports
    naive_sum = diversified.sum_of_individual_quantiles_kw()
    whole = api.pool(diversified.firm, method="gaussian_copula", seed=0)
    assert float(np.mean(whole["pool_firm_kw"].to_numpy())) > float(np.mean(naive_sum))


def test_a_perfectly_correlated_portfolio_shows_no_diversification(comonotonic):
    """The falsifying case. Sites that move as one buy nothing by pooling, so
    the curve must be flat. A curve that rose here would be manufacturing the
    headline out of the estimator."""
    sizes = [1, 2, 3, 4, 5]
    out = api.diversification_curve(comonotonic.firm, sizes=sizes, seed=0,
                                    realised=comonotonic.realised,
                                    method="gaussian_copula")
    values = out.set_index("n_sites")["firm_kw_per_site"].reindex(sizes).to_numpy()
    spread = (values.max() - values.min()) / values.mean()
    assert spread < 0.01, f"comonotonic portfolio moved {spread:.2%} across the curve: {values}"

    # guard the guard: this fixture really is near-comonotonic
    whole = api.pool(comonotonic.firm, method="gaussian_copula", seed=0)
    assert whole.attrs["mean_pairwise_correlation"] > 0.9


def test_monotonicity_is_reported_rather_than_enforced(diversified, comonotonic):
    """The flag has to be able to read False, or it is decoration.

    A flat (comonotonic) curve wobbles around zero slope on estimator noise, so
    across a handful of seeds it reports a violation at least once; the
    genuinely diversifying portfolio reports none on any of them. Nothing is
    smoothed to make the claim look better.
    """
    sizes = [1, 2, 3, 4, 5]
    flat_flags, real_flags = [], []
    for seed in range(4):
        flat = api.diversification_curve(comonotonic.firm, sizes=sizes, seed=seed,
                                         realised=comonotonic.realised)
        real = api.diversification_curve(diversified.firm, sizes=sizes, seed=seed,
                                         realised=diversified.realised)
        flat_flags.append(flat.attrs["monotone_in_n_sites"])
        real_flags.append(real.attrs["monotone_in_n_sites"])
        # the violations reported are the ones actually present in the column
        column = flat.sort_values("n_sites")["firm_kw_per_site"].to_numpy()
        assert (len(flat.attrs["monotonicity_violations"])
                == int(np.sum(np.diff(column) < -1e-9)))

    assert any(flag is False for flag in flat_flags), (
        "monotone_in_n_sites never read False -- it cannot be a measurement"
    )
    assert all(real_flags), f"the diversifying portfolio reported a violation: {real_flags}"


def test_the_curve_averages_over_every_subset_when_it_can(diversified):
    """With few sites, sampling 12 random subsets is a noisy estimate of an
    average that can simply be computed exactly -- and the noise moved a 5-site
    fixture's n=1 point by +-50% on the seed alone, swamping the signal the
    curve exists to show."""
    out = api.diversification_curve(diversified.firm, sizes=[1, 2, 5, 6], seed=0,
                                    realised=diversified.realised)
    # C(6,1)=6 and C(6,5)=6 and C(6,6)=1 are all <= 12 replicates; C(6,2)=15 is not
    assert out.attrs["exhaustive_sizes"] == [1, 5, 6]

    exact = float(diversified.firm.groupby("site_id")["firm_kw"].mean().mean())
    for seed in (0, 1, 2):
        one = api.diversification_curve(diversified.firm, sizes=[1], seed=seed,
                                        realised=diversified.realised)
        assert float(one["firm_kw_per_site"].iloc[0]) == pytest.approx(exact, rel=1e-9)


# ---------------------------------------------------------------------------
# shortfall_rate is a measurement of realised load
# ---------------------------------------------------------------------------

def test_shortfall_rate_is_measured_against_realised_load_at_both_ends(diversified):
    """Three fixtures, three answers the model cannot produce on its own.

    A rate simulated from the promise's own distribution would read ~tau in all
    three. Here it reads 0 when the sites always deliver, 1 when they never do,
    and ~tau when they deliver from their true marginal.
    """
    always = diversified.realised.copy()
    always["realised_kw"] = 10_000.0
    never = diversified.realised.copy()
    never["realised_kw"] = 0.0

    generous = api.diversification_curve(diversified.firm, sizes=[1, 3], seed=0,
                                         realised=always)
    assert (generous["shortfall_rate"] == 0.0).all()
    assert generous.attrs["shortfall_intervals_observed"] == 0

    broken = api.diversification_curve(diversified.firm, sizes=[1, 3], seed=0,
                                       realised=never)
    assert (broken["shortfall_rate"] == 1.0).all()
    assert broken.attrs["shortfall_intervals_observed"] > 0

    honest = api.diversification_curve(diversified.firm, sizes=[1, 3], seed=0,
                                       realised=diversified.realised)
    single = float(honest.set_index("n_sites")["shortfall_rate"][1])
    assert 0.01 < single < 0.10, (
        f"a tau=0.05 promise on one site shortfalls {single:.2%} of intervals"
    )


def test_shortfall_rate_falls_as_the_portfolio_grows(diversified):
    """The second half of the claim: the bigger pool is not just larger, it is
    firmer. Measured against realised load, not asserted from tau."""
    out = api.diversification_curve(diversified.firm, sizes=SIZES, seed=0,
                                    realised=diversified.realised)
    rates = out.set_index("n_sites")["shortfall_rate"].reindex(SIZES).to_numpy()
    assert rates[0] > rates[-1], f"shortfall did not fall with portfolio size: {rates}"
    assert out.attrs["shortfall_intervals_observed"] > 0, (
        "no interval anywhere in the sweep was a shortfall -- the rate proves nothing"
    )


def test_a_promise_sized_off_the_median_shortfalls_far_more(diversified):
    """Independent check that the shortfall measurement responds to the promise.

    Same sites, same realised load; only the quantile the promise is sized off
    changes. A median-sized promise must fail roughly half the time.
    """
    median_firm = api.firm_capacity(diversified.quantiles, tau=0.5)
    conservative = api.diversification_curve(diversified.firm, sizes=[1], seed=0,
                                             realised=diversified.realised)
    reckless = api.diversification_curve(median_firm, sizes=[1], seed=0,
                                         realised=diversified.realised)
    assert float(reckless["shortfall_rate"].iloc[0]) > 0.35
    assert (float(reckless["shortfall_rate"].iloc[0])
            > float(conservative["shortfall_rate"].iloc[0]) + 0.25)


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------

def test_same_seed_is_byte_identical_and_a_different_seed_is_not(diversified):
    a = api.diversification_curve(diversified.firm, sizes=[2, 3], seed=0,
                                  realised=diversified.realised)
    b = api.diversification_curve(diversified.firm, sizes=[2, 3], seed=0,
                                  realised=diversified.realised)
    pd.testing.assert_frame_equal(a, b)

    c = api.diversification_curve(diversified.firm, sizes=[2, 3], seed=5,
                                  realised=diversified.realised)
    assert not np.array_equal(a["firm_kw_per_site"].to_numpy(),
                              c["firm_kw_per_site"].to_numpy()), (
        "the seed does not reach the draws"
    )


def test_the_seed_and_tau_are_carried_on_the_frame(diversified):
    out = api.diversification_curve(diversified.firm, sizes=[2], seed=3,
                                    realised=diversified.realised)
    assert out.attrs["seed"] == 3
    assert out.attrs["tau"] == pytest.approx(0.05)
    assert out.attrs["method"] == "gaussian_copula"


# ---------------------------------------------------------------------------
# a low-capacity portfolio must not crash the curve
# ---------------------------------------------------------------------------

def test_a_portfolio_with_a_near_zero_interval_still_produces_a_curve():
    """Regression: `pool`'s materiality guard used to raise "aggregation bug"
    on Monte-Carlo noise at an interval where the portfolio sells almost
    nothing, and `diversification_curve` -- which pools at the smaller draw
    count -- hit it reproducibly."""
    low = factor_portfolio(n_sites=2, n_days=4, rho=0.995, seed=2,
                           base=[100.0, 200.0], sigma=40.0, shock_kw=30.0,
                           shape_kw=60.0)
    # guard the guard: this portfolio's promise really does nearly vanish at
    # one interval (~4 kW against a ~175 kW mean) -- that is the shape that
    # made the curve raise.
    baseline = low.sum_of_individual_quantiles_kw()
    assert baseline.min() < 0.05 * baseline.mean()

    out = api.diversification_curve(low.firm, sizes=[1, 2], seed=3,
                                    realised=low.realised)
    assert len(out) == 2
    assert np.isfinite(out["firm_kw_per_site"]).all()
