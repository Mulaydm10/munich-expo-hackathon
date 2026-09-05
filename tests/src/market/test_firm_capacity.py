"""firm_capacity: the project's thesis expressed as code -- size off the lower quantile,
never the median, and never silently over-promise."""

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from src.market import api

QUANTILES = (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)


def _quantile_frame(mu: float, sigma: float, n_rows: int, site_id: str = "site-a") -> pd.DataFrame:
    """A quantiles frame with the *known* analytical quantiles of Normal(mu, sigma), repeated
    over n_rows independent 15-min timestamps -- "known ground truth" for shortfall testing."""
    t = pd.date_range("2026-01-05T00:00:00Z", periods=n_rows, freq="15min")
    data = {"t": t, "site_id": [site_id] * n_rows}
    for tau in QUANTILES:
        data[api._quantile_col(tau)] = mu + norm.ppf(tau) * sigma
    return pd.DataFrame(data)


def test_firm_capacity_uses_lower_quantile_not_median():
    q = _quantile_frame(mu=100.0, sigma=40.0, n_rows=5)
    out = api.firm_capacity(q, tau=0.05)
    expected_q05 = 100.0 + norm.ppf(0.05) * 40.0
    assert expected_q05 < 50.0  # q05 is far below the median (100) for this spread
    assert np.allclose(out["firm_kw"], expected_q05)
    assert not np.allclose(out["firm_kw"], 100.0), "firm_capacity must not fall back to the median"


def test_shortfall_rate_at_q05_is_near_5pct_and_median_is_materially_worse(rng_seed=0):
    """Acceptance bullet: known-ground-truth fixture, firm_capacity(tau=0.05) shortfall <= 5%
    (with sampling slack), median-based alternative on the *same fixture* is materially worse."""
    mu, sigma, n = 100.0, 25.0, 4000
    q = _quantile_frame(mu=mu, sigma=sigma, n_rows=n)

    rng = np.random.default_rng(rng_seed)
    realised = rng.normal(mu, sigma, size=n)

    firm_q05 = api.firm_capacity(q, tau=0.05)["firm_kw"].to_numpy()
    firm_median = api.firm_capacity(q, tau=0.5)["firm_kw"].to_numpy()

    shortfall_q05 = float(np.mean(realised < firm_q05))
    shortfall_median = float(np.mean(realised < firm_median))

    # guard the guard: the scenario must actually be capable of showing a shortfall
    assert shortfall_median > 0.0

    assert shortfall_q05 <= 0.07, f"q05-sized promise shortfall {shortfall_q05:.3%} exceeds the 5% target by more than sampling noise"
    assert shortfall_median >= 0.40, f"median-sized promise shortfall {shortfall_median:.3%} should be close to 50%"
    gap = shortfall_median - shortfall_q05
    assert gap > 0.30, f"median-based sizing must be *materially* worse than q05-based; gap was only {gap:.3%}"


def test_floor_kw_clamps_and_records_how_often_it_binds():
    q = _quantile_frame(mu=10.0, sigma=2.0, n_rows=10)
    out = api.firm_capacity(q, tau=0.05, floor_kw=1000.0)  # floor far above any promise
    assert (out["firm_kw"] == 0.0).all()
    # guard the guard: the clamp must actually have bound on real rows, not vacuously
    assert out.attrs["floor_clamp_rate"] == 1.0


def test_floor_kw_does_not_bind_when_it_should_not():
    q = _quantile_frame(mu=100.0, sigma=5.0, n_rows=10)
    out = api.firm_capacity(q, tau=0.05, floor_kw=0.0)
    assert out.attrs["floor_clamp_rate"] == 0.0
    assert (out["firm_kw"] > 0).all()


def test_nan_quantile_is_treated_as_zero_not_permissive():
    q = _quantile_frame(mu=100.0, sigma=10.0, n_rows=3)
    q.loc[1, "q05"] = float("nan")
    out = api.firm_capacity(q, tau=0.05)
    assert out.loc[1, "firm_kw"] == 0.0
    assert out.attrs["nan_quantile_rate"] == pytest.approx(1 / 3)


def test_mislabelled_quantiles_are_rejected_not_silently_used():
    """The #22-shaped bug: q05 and q95 columns swapped throughout -- same numbers, inverted
    labels. firm_capacity must refuse rather than silently bid the upper quantile as firm."""
    q = _quantile_frame(mu=100.0, sigma=30.0, n_rows=20)
    q["q05"], q["q95"] = q["q95"].copy(), q["q05"].copy()
    with pytest.raises(api.MarketError):
        api.firm_capacity(q, tau=0.05)


def test_missing_quantile_column_is_rejected():
    q = _quantile_frame(mu=100.0, sigma=10.0, n_rows=3).drop(columns=["q05"])
    with pytest.raises(api.MarketError):
        api.firm_capacity(q, tau=0.05)
