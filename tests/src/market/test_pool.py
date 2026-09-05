"""pool(): diversification is not addition. Test both the guarantee
(pool(empirical/gaussian_copula) >= pool(sum)) and the correlation sign
(higher correlation -> lower poolable firm capacity, on the same inputs)."""

import numpy as np
import pandas as pd
import pytest

from src.market import api


def _firm_frame(values: list[float], n_t: int = 3, tau: float = 0.05) -> pd.DataFrame:
    t = pd.date_range("2026-02-01T00:00:00Z", periods=n_t, freq="15min")
    rows = []
    for tt in t:
        for i, v in enumerate(values):
            rows.append({"t": tt, "site_id": f"site-{i}", "firm_kw": v})
    df = pd.DataFrame(rows)
    df.attrs["tau"] = tau
    return df


def test_pool_diversification_guarantee_holds_for_every_t():
    firm = _firm_frame([50.0, 60.0, 55.0, 45.0, 70.0], n_t=4)
    pool_sum = api.pool(firm, method="sum")
    pool_gc = api.pool(firm, method="gaussian_copula")
    pool_emp = api.pool(firm, method="empirical", seed=42)

    merged = pool_sum.merge(pool_gc, on="t", suffixes=("_sum", "_gc")).merge(
        pool_emp.rename(columns={"pool_firm_kw": "pool_firm_kw_emp"}), on="t"
    )
    assert (merged["pool_firm_kw_gc"] >= merged["pool_firm_kw_sum"] - 1e-6).all()
    assert (merged["pool_firm_kw_emp"] >= merged["pool_firm_kw_sum"] - 1e-6).all()
    # guard the guard: diversification must actually be > 0 here, not vacuously equal
    assert (merged["pool_firm_kw_gc"] > merged["pool_firm_kw_sum"] + 1e-6).all()


def test_pool_sum_equals_naive_addition():
    values = [50.0, 60.0, 55.0]
    firm = _firm_frame(values, n_t=1)
    out = api.pool(firm, method="sum")
    assert out["pool_firm_kw"].iloc[0] == pytest.approx(sum(values))


def test_correlation_sign_private_helper_holds_inputs_fixed():
    """Increasing assumed correlation must *decrease* poolable firm capacity, same inputs."""
    firm_kw = np.array([50.0, 60.0, 55.0, 45.0, 70.0])
    low = api._pool_analytic(firm_kw, tau=0.05, rho=0.0, rel_uncertainty=0.3)
    mid = api._pool_analytic(firm_kw, tau=0.05, rho=0.5, rel_uncertainty=0.3)
    near_one = api._pool_analytic(firm_kw, tau=0.05, rho=0.99, rel_uncertainty=0.3)
    at_one = api._pool_analytic(firm_kw, tau=0.05, rho=1.0, rel_uncertainty=0.3)
    assert low > mid > near_one > at_one, (
        f"expected strictly decreasing with rho: {low}, {mid}, {near_one}, {at_one}"
    )
    # at rho == 1 the pool collapses exactly to the naive sum (no diversification benefit left)
    assert at_one == pytest.approx(sum(firm_kw), abs=1e-6)


def test_correlation_sign_through_public_pool_api(monkeypatch):
    """Same sign check, but exercised through the public pool() entry point by varying the
    module-level assumed correlation -- not just the private helper."""
    firm = _firm_frame([50.0, 60.0, 55.0, 45.0, 70.0], n_t=1)

    monkeypatch.setitem(
        api.ASSUMPTIONS, "pool_default_pairwise_correlation",
        api.Assumption(value=0.05, unit="dimensionless (Pearson r)", source="ASSUMED", note="test-low"),
    )
    low = api.pool(firm, method="gaussian_copula")["pool_firm_kw"].iloc[0]

    monkeypatch.setitem(
        api.ASSUMPTIONS, "pool_default_pairwise_correlation",
        api.Assumption(value=0.95, unit="dimensionless (Pearson r)", source="ASSUMED", note="test-high"),
    )
    high = api.pool(firm, method="gaussian_copula")["pool_firm_kw"].iloc[0]

    assert low > high, f"expected low-correlation pool ({low}) > high-correlation pool ({high})"


def test_unknown_pool_method_rejected():
    firm = _firm_frame([50.0, 60.0], n_t=1)
    with pytest.raises(api.MarketError):
        api.pool(firm, method="bogus")
