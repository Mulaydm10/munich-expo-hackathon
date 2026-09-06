"""correlation_structure(): the measurement the whole claim rests on.

If the dependence structure were assumed rather than measured, the
diversification benefit would be an assumption wearing a number's clothes. Two
properties make it a measurement, and both are tested against expectations
computed here rather than read back from the frame:

* it correlates RESIDUALS, not raw load -- two depots that both fill at 08:00
  are not thereby correlated in the way pooling cares about, and raw load would
  read ~1.0 everywhere; and
* the regimes where correlation runs to 1 (cold snaps, holidays, peak load) are
  flagged AND the pooled promise measurably falls when that regime's matrix is
  fed back into `pool` -- the contract calls that a deliverable, not a caveat.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.market import api

from .conftest import (
    PERIODS_PER_DAY,
    load_frame,
    quantile_frame,
    site_table,
    utc_index,
)

# Four real-ish German sites; distances are what the great-circle formula gives.
SITE_IDS = ["berlin", "potsdam", "frankfurt", "muenchen"]
SITES = site_table(SITE_IDS, [52.52, 52.40, 50.11, 48.14], [13.40, 13.06, 8.68, 11.58])


def _shared_daily_profile(n_days: int, amplitude: float = 200.0) -> np.ndarray:
    one = 300.0 + amplitude * np.sin(np.arange(PERIODS_PER_DAY) / PERIODS_PER_DAY * 2 * np.pi)
    return np.tile(one, n_days)


# ---------------------------------------------------------------------------
# residuals, not raw load
# ---------------------------------------------------------------------------

def test_it_correlates_residuals_not_raw_load():
    """The same commuter shape at both sites, independent deviations from it.

    Raw Pearson correlation is computed here in the test and is ~0.99; the
    residual correlation the function reports must be near zero. If it ever
    reported the raw number the headline benefit would collapse to nothing,
    because every site in Germany shares the same daily shape.
    """
    n_days = 5
    t = utc_index(n_days, start="2026-03-02T00:00:00Z")
    rng = np.random.default_rng(31)
    profile = _shared_daily_profile(n_days)
    a = profile + rng.normal(0.0, 15.0, len(t))
    b = profile + rng.normal(0.0, 15.0, len(t))

    raw_pearson = float(np.corrcoef(a, b)[0, 1])
    assert raw_pearson > 0.9, "fixture is not sharing a daily shape, so it proves nothing"

    out = api.correlation_structure(
        load_frame({"berlin": a, "muenchen": b}, t),
        SITES[SITES["site_id"].isin(["berlin", "muenchen"])],
    )
    assert len(out) == 1
    residual = float(out["correlation"].iloc[0])
    assert abs(residual) < 0.20, (
        f"residual correlation {residual:.3f} is close to the raw {raw_pearson:.3f}: "
        "the time-of-day profile is not being divided out"
    )


def test_it_still_finds_correlation_that_is_actually_there():
    """The other end of the same measurement: shared *deviations* must show up."""
    n_days = 5
    t = utc_index(n_days, start="2026-03-02T00:00:00Z")
    rng = np.random.default_rng(32)
    profile = _shared_daily_profile(n_days)
    shock = rng.normal(0.0, 1.0, len(t))
    a = profile + 20.0 * shock + rng.normal(0.0, 3.0, len(t))
    b = profile + 20.0 * shock + rng.normal(0.0, 3.0, len(t))

    out = api.correlation_structure(
        load_frame({"berlin": a, "muenchen": b}, t),
        SITES[SITES["site_id"].isin(["berlin", "muenchen"])],
    )
    assert float(out["correlation"].iloc[0]) > 0.9


# ---------------------------------------------------------------------------
# distance
# ---------------------------------------------------------------------------

def _distance_portfolio(decay_km: float, n_days: int = 6, seed: int = 4):
    """Sites on one meridian whose shared-factor weight decays with distance."""
    t = utc_index(n_days, start="2026-01-05T00:00:00Z")
    rng = np.random.default_rng(seed)
    n = len(t)
    profile = _shared_daily_profile(n_days, amplitude=120.0)
    common = rng.normal(0.0, 1.0, n)
    lats = [48.0, 48.9, 50.7, 54.3]
    ids = [f"p{i}" for i in range(4)]
    loads = {}
    for site_id, lat in zip(ids, lats):
        d = api._haversine_km(48.0, 11.0, lat, 11.0)
        w = np.exp(-d / (2.0 * decay_km))
        idio = rng.normal(0.0, 1.0, n)
        loads[site_id] = profile + 25.0 * (w * common + np.sqrt(max(1.0 - w * w, 1e-6)) * idio)
    return load_frame(loads, t), site_table(ids, lats, [11.0] * 4)


def test_distance_is_great_circle_and_correlation_falls_with_it():
    load, sites = _distance_portfolio(decay_km=400.0)
    out = api.correlation_structure(load, sites)

    # distance is computed independently here from the same lat/lon
    for row in out.itertuples(index=False):
        la = float(sites.set_index("site_id").loc[row.site_a, "lat"])
        lb = float(sites.set_index("site_id").loc[row.site_b, "lat"])
        expected_km = api._haversine_km(la, 11.0, lb, 11.0)
        assert row.distance_km == pytest.approx(expected_km, rel=1e-9)

    assert out["distance_km"].min() > 50.0  # the sites are genuinely apart
    ranked = out.sort_values("distance_km")
    assert ranked["correlation"].iloc[0] > ranked["correlation"].iloc[-1] + 0.3, (
        "correlation does not fall with distance in a fixture built so that it does"
    )


def test_the_fitted_decay_length_tracks_the_designed_one():
    """Not a magic number: a portfolio designed to decorrelate twice as fast
    must fit a materially shorter decay length."""
    slow_load, sites = _distance_portfolio(decay_km=600.0)
    fast_load, _ = _distance_portfolio(decay_km=150.0)

    slow = api.correlation_structure(slow_load, sites).attrs["decay_length_km"]
    fast = api.correlation_structure(fast_load, sites).attrs["decay_length_km"]

    assert np.isfinite(slow) and slow > 0
    assert np.isfinite(fast) and fast > 0
    assert fast < 0.5 * slow, f"decay length did not track the fixture: fast={fast}, slow={slow}"


# ---------------------------------------------------------------------------
# the regimes where correlation -> 1
# ---------------------------------------------------------------------------

def _cold_snap_fixture(n_days: int = 30, cold_days=(12, 13, 14), cold_rho: float = 0.97,
                       calm_rho: float = 0.15, seed: int = 21):
    t = utc_index(n_days, start="2026-01-05T00:00:00Z")
    n = len(t)
    rng = np.random.default_rng(seed)
    day = np.repeat(np.arange(n_days), PERIODS_PER_DAY)
    cold = np.isin(day, list(cold_days))
    profile = _shared_daily_profile(n_days, amplitude=120.0)
    common = rng.normal(0.0, 1.0, n)

    loads = {}
    for site_id in SITE_IDS:
        idio = rng.normal(0.0, 1.0, n)
        rho = np.where(cold, cold_rho, calm_rho)
        shock = np.sqrt(rho) * common + np.sqrt(1.0 - rho) * idio
        loads[site_id] = profile + 25.0 * shock
    weather = pd.DataFrame({"t": t, "temp_c": np.where(cold, -8.0, 9.0) + rng.normal(0, 0.3, n)})
    return load_frame(loads, t), weather, cold


def test_cold_snap_regime_is_flagged_and_the_pooled_promise_falls():
    """The deliverable: the pool is weakest exactly when the grid needs it.

    Two separate claims, both asserted. (1) The flag fires -- the cold-snap
    correlation is above the ASSUMPTIONS threshold and above baseline. (2) The
    consequence is real: feeding the cold-snap matrix back into `pool` gives a
    materially SMALLER promise than the baseline matrix, measured against a
    naive sum computed from the fixture's own quantile columns.
    """
    load, weather, cold_mask = _cold_snap_fixture()
    out = api.correlation_structure(load, SITES, weather=weather)

    flags = out.attrs["regime_flags"]
    assert "cold_snap" in flags, out.attrs["regimes_unavailable"]
    cold = flags["cold_snap"]
    threshold = api.ASSUMPTIONS["correlation_regime_flag_threshold"].value
    assert cold["flagged"] is True
    assert cold["mean_correlation"] >= threshold
    assert cold["increase"] > 0.5
    assert cold["n_intervals"] == int(cold_mask.sum())

    # the other end of the same flag: the calm baseline is not flagged
    assert out.attrs["baseline_mean_correlation"] < threshold
    assert flags["peak_load"]["flagged"] is False

    # ... and the promise actually falls when that regime's matrix is used
    n_days = 4
    t = utc_index(n_days, start="2026-02-02T00:00:00Z")
    rng = np.random.default_rng(77)
    profile = _shared_daily_profile(n_days, amplitude=120.0)
    mu = {s: profile + 25.0 * rng.normal(0.0, 1.0, len(t)) for s in SITE_IDS}
    quantiles = quantile_frame(mu, sigma=50.0, t=t)
    firm = api.firm_capacity(quantiles, tau=0.05)
    naive_sum = quantiles.groupby("t")["q05"].sum().sort_index().to_numpy()

    baseline_pool = api.pool(firm, method="gaussian_copula", seed=0,
                             correlation=out.attrs["correlation_matrix"])
    cold_pool = api.pool(firm, method="gaussian_copula", seed=0,
                         correlation=out.attrs["correlation_matrix_by_regime"]["cold_snap"])

    baseline_benefit = float(np.mean(baseline_pool["pool_firm_kw"].to_numpy() - naive_sum))
    cold_benefit = float(np.mean(cold_pool["pool_firm_kw"].to_numpy() - naive_sum))

    assert baseline_benefit > 0.03 * float(np.mean(naive_sum)), (
        "the calm-regime pool shows no benefit, so a drop would prove nothing"
    )
    assert cold_benefit < 0.4 * baseline_benefit, (
        f"cold-snap correlation barely dented the promise: {cold_benefit:.1f} kW "
        f"vs {baseline_benefit:.1f} kW baseline"
    )
    assert (cold_pool["pool_firm_kw"].to_numpy()
            < baseline_pool["pool_firm_kw"].to_numpy() + 1e-9).all()


def test_the_flag_threshold_comes_from_ASSUMPTIONS(monkeypatch):
    """Move the named assumption, and the flag must move with it. A threshold
    hard-coded next to a docstring citing ASSUMPTIONS would pass every other
    test in this file."""
    load, weather, _ = _cold_snap_fixture(cold_rho=0.75, calm_rho=0.1)

    monkeypatch.setitem(api.ASSUMPTIONS, "correlation_regime_flag_threshold",
                        api.Assumption(value=0.99, unit="correlation (dimensionless)",
                                       source="ASSUMED", note="test override"))
    strict = api.correlation_structure(load, SITES, weather=weather)
    assert strict.attrs["regime_flags"]["cold_snap"]["flagged"] is False

    monkeypatch.setitem(api.ASSUMPTIONS, "correlation_regime_flag_threshold",
                        api.Assumption(value=0.50, unit="correlation (dimensionless)",
                                       source="ASSUMED", note="test override"))
    lax = api.correlation_structure(load, SITES, weather=weather)
    assert lax.attrs["regime_flags"]["cold_snap"]["flagged"] is True
    # the measured number itself is unchanged -- only the verdict moved
    assert (strict.attrs["regime_flags"]["cold_snap"]["mean_correlation"]
            == pytest.approx(lax.attrs["regime_flags"]["cold_snap"]["mean_correlation"]))


def test_german_public_holidays_are_a_regime():
    """A window across Christmas and New Year: three nationwide holidays."""
    n_days = 25
    t = utc_index(n_days, start="2025-12-20T00:00:00Z")
    rng = np.random.default_rng(41)
    profile = _shared_daily_profile(n_days, amplitude=100.0)
    loads = {s: profile + rng.normal(0.0, 15.0, len(t)) for s in SITE_IDS}

    out = api.correlation_structure(load_frame(loads, t), SITES)
    flags = out.attrs["regime_flags"]
    assert "holiday" in flags, out.attrs["regimes_unavailable"]
    # 25 Dec, 26 Dec, 1 Jan -- computed here, not read from the result
    assert flags["holiday"]["n_intervals"] == 3 * PERIODS_PER_DAY


def test_holiday_regime_is_reported_unavailable_when_the_window_has_none():
    """Both ends of `regimes_unavailable`: a regime with too few intervals is
    named as unavailable rather than silently omitted or filled with zeros."""
    n_days = 12
    t = utc_index(n_days, start="2026-05-04T00:00:00Z")  # no nationwide holiday
    rng = np.random.default_rng(42)
    profile = _shared_daily_profile(n_days, amplitude=100.0)
    loads = {s: profile + rng.normal(0.0, 15.0, len(t)) for s in SITE_IDS}

    out = api.correlation_structure(load_frame(loads, t), SITES)
    assert "holiday" not in out.attrs["regime_flags"]
    assert "holiday" in out.attrs["regimes_unavailable"]
    assert "peak_load" in out.attrs["regime_flags"]


def test_cold_snap_is_unavailable_without_a_weather_frame():
    load, weather, _ = _cold_snap_fixture(n_days=12, cold_days=(4, 5, 6))
    without = api.correlation_structure(load, SITES)
    assert "cold_snap" in without.attrs["regimes_unavailable"]
    assert "cold_snap" not in without.attrs["regime_flags"]

    with_weather = api.correlation_structure(load, SITES, weather=weather)
    assert "cold_snap" in with_weather.attrs["regime_flags"]
    assert "cold_snap" not in with_weather.attrs["regimes_unavailable"]


def test_a_weather_frame_that_misses_timestamps_is_refused():
    load, weather, _ = _cold_snap_fixture(n_days=12, cold_days=(4, 5, 6))
    with pytest.raises(api.MarketError, match="does not cover every load"):
        api.correlation_structure(load, SITES, weather=weather.iloc[:-10])


# ---------------------------------------------------------------------------
# coercions and refusals
# ---------------------------------------------------------------------------

def test_residual_degenerate_bucket_rate_moves_between_two_fixtures():
    """A time-of-day bucket seen once has an identically-zero residual, which
    deflates every correlation it touches. The rate must be able to move."""
    n_days = 3
    clean_t = utc_index(n_days, start="2026-03-02T00:00:00Z")
    rng = np.random.default_rng(51)
    loads = {s: 300.0 + rng.normal(0.0, 20.0, len(clean_t)) for s in SITE_IDS}
    clean = api.correlation_structure(load_frame(loads, clean_t), SITES)
    assert clean.attrs["residual_degenerate_bucket_rate"] == 0.0

    # two extra readings on an off-grid minute: those buckets occur once each
    odd_t = pd.DatetimeIndex(list(clean_t) + [pd.Timestamp("2026-03-05T00:07:00Z"),
                                              pd.Timestamp("2026-03-05T00:22:00Z")])
    odd_loads = {s: 300.0 + rng.normal(0.0, 20.0, len(odd_t)) for s in SITE_IDS}
    odd = api.correlation_structure(load_frame(odd_loads, odd_t), SITES)
    assert odd.attrs["residual_degenerate_bucket_rate"] > 0.0


def test_a_single_day_has_no_measurable_residual_and_is_refused():
    """Every bucket seen exactly once: all residuals are zero by construction,
    so a correlation computed from them would be an artefact, not a reading."""
    t = utc_index(1, start="2026-03-02T00:00:00Z")
    rng = np.random.default_rng(52)
    loads = {s: 300.0 + rng.normal(0.0, 20.0, len(t)) for s in SITE_IDS}
    with pytest.raises(api.MarketError, match="no correlation is measurable"):
        api.correlation_structure(load_frame(loads, t), SITES)


def test_nan_load_and_ragged_panels_and_lone_sites_are_refused():
    n_days = 3
    t = utc_index(n_days, start="2026-03-02T00:00:00Z")
    rng = np.random.default_rng(53)
    loads = {s: 300.0 + rng.normal(0.0, 20.0, len(t)) for s in SITE_IDS}
    load = load_frame(loads, t)

    with_nan = load.copy()
    with_nan.loc[with_nan.index[5], "load_kw"] = np.nan
    with pytest.raises(api.MarketError, match="NaN load_kw"):
        api.correlation_structure(with_nan, SITES)

    ragged = load.drop(load.index[load["site_id"] == "berlin"][:4])
    with pytest.raises(api.MarketError, match="ragged"):
        api.correlation_structure(ragged, SITES)

    one_site = load[load["site_id"] == "berlin"]
    with pytest.raises(api.MarketError, match="at least two sites"):
        api.correlation_structure(one_site, SITES)

    with pytest.raises(api.MarketError, match="no lat/lon"):
        api.correlation_structure(load, SITES[SITES["site_id"] != "berlin"])


def test_assumptions_used_names_keys_that_exist():
    """Provenance rule: a figure that leans on an assumption names its key."""
    load, weather, _ = _cold_snap_fixture(n_days=12, cold_days=(4, 5, 6))
    out = api.correlation_structure(load, SITES, weather=weather)
    used = out.attrs["assumptions_used"]
    assert used, "no assumption keys named at all"
    for key in used:
        assert key in api.ASSUMPTIONS, f"{key!r} is not an ASSUMPTIONS key"
        assert api.ASSUMPTIONS[key].source
    assert "cold_snap_temp_percentile" in used

    without = api.correlation_structure(load, SITES)
    assert "cold_snap_temp_percentile" not in without.attrs["assumptions_used"]


def test_pool_consumes_the_pairwise_frame_including_its_joint_ranks():
    """`pool(method='empirical')` needs the ranks, and the frame carries them.

    This is the wiring the acceptance criterion asks for: gaussian_copula uses
    the matrix `correlation_structure` fitted, not one it assumed.
    """
    n_days = 6
    t = utc_index(n_days, start="2026-04-06T00:00:00Z")
    rng = np.random.default_rng(61)
    profile = _shared_daily_profile(n_days, amplitude=120.0)
    common = rng.normal(0.0, 1.0, len(t))
    loads, mu = {}, {}
    for site_id in SITE_IDS:
        idio = rng.normal(0.0, 1.0, len(t))
        shock = np.sqrt(0.3) * common + np.sqrt(0.7) * idio
        loads[site_id] = profile + 25.0 * shock
        mu[site_id] = loads[site_id]
    structure = api.correlation_structure(load_frame(loads, t), SITES)
    firm = api.firm_capacity(quantile_frame(mu, sigma=50.0, t=t), tau=0.05)

    for method, expected_source in (("empirical", "supplied:correlation_structure"),
                                    ("gaussian_copula", "supplied:correlation_structure")):
        pooled = api.pool(firm, method=method, seed=0, correlation=structure)
        assert pooled.attrs["correlation_source"] == expected_source
        # the matrix it used is the one that was fitted, not an assumed constant
        fitted_mean = float(np.mean(structure["correlation"].to_numpy()))
        assert pooled.attrs["mean_pairwise_correlation"] == pytest.approx(fitted_mean, abs=1e-9)

    missing_pair = structure.iloc[1:].copy()
    missing_pair.attrs = dict(structure.attrs)
    with pytest.raises(api.MarketError, match="no entry for pair"):
        api.pool(firm, method="gaussian_copula", seed=0, correlation=missing_pair)
