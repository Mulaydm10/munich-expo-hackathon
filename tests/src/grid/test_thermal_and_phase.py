"""Acceptance tests for src/grid (issue #12).

All fixtures are local and synthetic -- no network, no cross-lane imports
(src/data and src/fleet are not yet merged to main; see the issue's
cross-lane note). `SiteElectrical` objects are constructed directly for most
tests so the thermal/phase logic is exercised independently of
`infer_electrical`'s own sizing rule, which gets its own dedicated test.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.grid import api


def _idx(periods: int, start: str = "2026-01-15T00:00:00Z") -> pd.DatetimeIndex:
    return pd.date_range(start=start, periods=periods, freq="15min", tz="UTC")


def _site(**overrides) -> api.SiteElectrical:
    defaults = dict(
        site_id="test-site",
        transformer_kva=250.0,
        phases=3,
        tau_oil_min=180.0,
        tau_winding_min=10.0,
        delta_top_oil_rated_c=60.0,  # see api.DEFAULT_DELTA_TOP_OIL_RATED_C's consistency note
        hotspot_limit_c=98.0,
        hotspot_emergency_c=120.0,
        point_phase={"cpA": 1, "cpB": 1, "cpC": 2, "cpD": 3},
    )
    defaults.update(overrides)
    return api.SiteElectrical(**defaults)


# ---------------------------------------------------------------------------
# The thermal model has memory
# ---------------------------------------------------------------------------

def test_hotspot_depends_on_load_history_not_just_instantaneous_load():
    """Identical instantaneous final load, very different preceding load:
    the hot-afternoon path must land at a materially higher hotspot. A
    memoryless model (hotspot(t) = f(load(t)) only) would make these equal.
    """
    site = _site()
    n = 96  # 24h at 15-min resolution
    idx = _idx(n)
    ambient = pd.Series(20.0, index=idx)  # hold ambient fixed: isolate history effect
    step_kw = 0.842 * site.rated_kw  # identical final instantaneous load in both paths

    # Cold path: unloaded for 22.5h (>> tau_oil), then the step for the last 1.5h.
    cold_load = np.zeros(n)
    cold_load[-6:] = step_kw
    hotspot_cold = api.hotspot_temperature(pd.Series(cold_load, index=idx), ambient, site)

    # Hot path: at that same load for the entire window (already settled).
    hot_load = np.full(n, step_kw)
    hotspot_hot = api.hotspot_temperature(pd.Series(hot_load, index=idx), ambient, site)

    # Same instantaneous load at the final row in both paths:
    assert cold_load[-1] == hot_load[-1]
    # But very different hotspot, because history differs:
    assert hotspot_hot.iloc[-1] - hotspot_cold.iloc[-1] > 5.0
    # And the cold path hasn't yet reached the hot path's settled value
    # (it's mid-transient, not there yet) -- a real asymptotic rise, not a
    # jump:
    assert hotspot_cold.iloc[-1] < hotspot_hot.iloc[-1]
    assert hotspot_cold.iloc[-6] < hotspot_cold.iloc[-1]  # still rising within the step


def test_hotspot_rises_with_roughly_the_oil_time_constant():
    """After holding a step load for exactly one tau_oil, the oil-rise
    component should have covered ~1 - e^-1 = 63.2% of the gap to its
    ultimate value -- the defining signature of an exponential, not an
    instant jump nor a linear ramp.
    """
    site = _site(tau_oil_min=180.0, tau_winding_min=180.0)  # match constants to isolate one exponential
    n = 12  # 12 * 15min = 180min = exactly 1 tau_oil
    idx = _idx(n)
    ambient = pd.Series(20.0, index=idx)
    load = pd.Series(site.rated_kw, index=idx)  # K = 1
    hotspot = api.hotspot_temperature(load, ambient, site)

    ultimate_rise = api._ultimate_oil_rise(1.0, site) + api._ultimate_hotspot_gradient(1.0, site)
    achieved_rise = hotspot.iloc[-1] - 20.0
    fraction = achieved_rise / ultimate_rise
    assert 0.55 < fraction < 0.70  # ~0.632 expected, generous tolerance for discretisation


# ---------------------------------------------------------------------------
# The envelope moves the right way with ambient + history
# ---------------------------------------------------------------------------

def test_envelope_above_nameplate_on_cold_night_from_cold_start():
    site = _site()
    idx = _idx(8)
    ambient = pd.Series(-5.0, index=idx)
    env = api.thermal_envelope(site, ambient)  # cold start: no prior_load_kw

    assert (env["max_kw"] > site.rated_kw).all()
    assert (env["max_kw"] <= api.MAX_ENVELOPE_MULTIPLE_OF_NAMEPLATE * site.rated_kw + 1e-6).all()


def test_envelope_below_nameplate_on_hot_evening_after_loaded_afternoon():
    site = _site()
    warmup_n = 48  # 12h, well beyond tau_oil, settles near steady state
    warmup_idx = _idx(warmup_n, start="2026-07-15T06:00:00Z")
    prior_load = pd.Series(0.9 * site.rated_kw, index=warmup_idx)

    idx = _idx(8, start="2026-07-15T18:00:00Z")
    ambient = pd.Series(35.0, index=idx)
    env = api.thermal_envelope(site, ambient, prior_load_kw=prior_load)

    assert (env["max_kw"] < site.rated_kw).all()
    assert (env["max_kw"] >= 0.0).all()


def test_envelope_reduces_to_nameplate_at_reference_ambient_and_steady_rated_load():
    """Trivial-case check: ambient at IEC's reference, load already settled
    at rated (K=1) -- max_kw must equal the nameplate rating to within 2%,
    and the emergency clamp must not be involved in a scenario this benign.
    """
    site = _site()
    warmup_n = 96  # long enough (>> tau_oil) to reach steady state at K=1
    warmup_idx = _idx(warmup_n, start="2026-03-01T00:00:00Z")
    prior_load = pd.Series(site.rated_kw, index=warmup_idx)

    idx = _idx(4, start="2026-03-05T00:00:00Z")
    ambient = pd.Series(api.IEC_REFERENCE_AMBIENT_C, index=idx)
    env = api.thermal_envelope(site, ambient, prior_load_kw=prior_load)

    assert abs(env["max_kw"].iloc[0] - site.rated_kw) / site.rated_kw < 0.02
    assert not env["clipped"].any()


def test_envelope_tight_not_merely_safe():
    """Holding load exactly at max_kw for the whole window keeps hotspot at
    or (numerically) just under the limit; +10% breaches it somewhere in the
    same window. This is the "tight, not merely safe" acceptance bullet.

    Warmed up to steady state at K=1 first (see the nameplate test) so the
    window itself starts and stays clear of the emergency clamp -- the
    clamp's own behaviour (binding transiently right after a cold start,
    then releasing) is exercised separately below.
    """
    site = _site()
    warmup_idx = _idx(96, start="2026-03-01T00:00:00Z")
    prior_load = pd.Series(site.rated_kw, index=warmup_idx)

    idx = _idx(24, start="2026-03-05T00:00:00Z")  # 6h window
    ambient = pd.Series(20.0, index=idx)

    env = api.thermal_envelope(site, ambient, prior_load_kw=prior_load)
    assert not env["clipped"].any()

    at_envelope = pd.Series(env["max_kw"].to_numpy(), index=idx)
    hotspot_at_envelope = api.hotspot_temperature(at_envelope, ambient, site)
    assert (hotspot_at_envelope <= site.hotspot_limit_c + 1e-2).all()

    over_envelope = at_envelope * 1.10
    hotspot_over = api.hotspot_temperature(over_envelope, ambient, site)
    assert (hotspot_over > site.hotspot_limit_c).any()


def test_envelope_clip_binds_only_transiently_after_a_cold_start_and_is_recorded():
    """Right after a genuinely cold start, one interval's worth of headroom
    is enormous (the oil hasn't had time to heat up at all yet) -- exactly
    the "briefly overloaded... not at all for an hour" physics the task
    describes. MAX_ENVELOPE_MULTIPLE_OF_NAMEPLATE bounds that first spike,
    and the `clipped` column records exactly when it did -- per the
    project's clip-visibility rule, this must never be silent, and it must
    not dominate the window (it should release once the oil warms up).
    """
    site = _site()
    idx = _idx(24, start="2026-05-01T00:00:00Z")  # 6h window, cold start
    ambient = pd.Series(20.0, index=idx)
    env = api.thermal_envelope(site, ambient)

    cap_kw = api.MAX_ENVELOPE_MULTIPLE_OF_NAMEPLATE * site.rated_kw
    clip_fraction = env["clipped"].mean()

    # It does bind (a cold-start pulse really does want more than the
    # emergency ceiling) but it is not the whole story for a multi-hour
    # window -- a "sane range", not 0% (hidden) or 100% (the clamp doing all
    # the work).
    assert 0.0 < clip_fraction < 0.5
    # Every clipped row sits exactly at the recorded ceiling...
    assert np.allclose(env.loc[env["clipped"], "max_kw"].to_numpy(), cap_kw)
    # ...and once the clamp releases later in the window it never re-binds
    # (oil has been warming up monotonically the whole time):
    assert not env["clipped"].iloc[-1]


# ---------------------------------------------------------------------------
# Three-phase allocation is not division by three
# ---------------------------------------------------------------------------

def test_phase_allocate_catches_single_phase_saturation_invisible_in_the_aggregate():
    """Aggregate demand comfortably inside the transformer rating, but one
    phase (where two single-phase chargers happen to land) is saturated.
    A correct allocator curtails only that phase; an "average across 3
    phases" implementation would miss this entirely.
    """
    site = _site(transformer_kva=150.0)  # rated_kw = 142.5, phase_limit_kw = 47.5
    demand = {"cpA": 40.0, "cpB": 40.0, "cpC": 5.0, "cpD": 5.0}  # phase1=80, phase2=5, phase3=5
    assert sum(demand.values()) < site.rated_kw  # aggregate looks fine

    allocation = api.phase_allocate(demand, site)
    phase_limit_kw = site.rated_kw / site.phases

    totals = {1: 0.0, 2: 0.0, 3: 0.0}
    for point, kw in allocation.items():
        totals[site.point_phase[point]] += kw
    assert totals[1] <= phase_limit_kw + 1e-6
    # Phase 1 was genuinely curtailed (below what was asked):
    assert allocation["cpA"] < demand["cpA"]
    assert allocation["cpB"] < demand["cpB"]
    # Uncongested phases are untouched:
    assert allocation["cpC"] == pytest.approx(demand["cpC"])
    assert allocation["cpD"] == pytest.approx(demand["cpD"])
    # Never allocate more than requested:
    for point, kw in allocation.items():
        assert kw <= demand[point] + 1e-9


def test_phase_allocate_never_exceeds_demand_and_is_order_independent():
    site = _site()
    demand_a = {"cpA": 30.0, "cpB": 30.0, "cpC": 20.0, "cpD": 15.0}
    demand_b = {"cpD": 15.0, "cpC": 20.0, "cpB": 30.0, "cpA": 30.0}  # same content, different order

    alloc_a = api.phase_allocate(demand_a, site)
    alloc_b = api.phase_allocate(demand_b, site)
    assert alloc_a == pytest.approx(alloc_b)
    for point, kw in alloc_a.items():
        assert kw <= demand_a[point] + 1e-9


def test_phase_allocate_rejects_unknown_point():
    site = _site()
    with pytest.raises(KeyError):
        api.phase_allocate({"not-wired": 10.0}, site)


def test_phase_imbalance_worst_vs_mean():
    site = _site()  # cpA,cpB -> phase1; cpC -> phase2; cpD -> phase3
    setpoints = {"cpA": 20.0, "cpB": 20.0, "cpC": 0.0, "cpD": 0.0}
    # totals: phase1=40, phase2=0, phase3=0; mean = 40/3
    mean = 40.0 / 3.0
    expected_worst = max(abs(40.0 - mean), abs(0.0 - mean), abs(0.0 - mean))
    assert api.phase_imbalance(setpoints, site) == pytest.approx(expected_worst)


def test_phase_imbalance_zero_when_perfectly_balanced():
    site = _site(point_phase={"cpA": 1, "cpB": 2, "cpC": 3})
    setpoints = {"cpA": 10.0, "cpB": 10.0, "cpC": 10.0}
    assert api.phase_imbalance(setpoints, site) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# envelope_violations
# ---------------------------------------------------------------------------

def test_envelope_violations_empty_for_compliant_schedule():
    idx = _idx(4)
    schedule = pd.DataFrame({"t": idx, "load_kw": [10.0, 20.0, 15.0, 5.0]})
    envelopes = pd.DataFrame({"t": idx, "max_kw": [50.0, 50.0, 50.0, 50.0]})
    result = api.envelope_violations(schedule, envelopes)
    assert result.empty


def test_envelope_violations_flags_exact_offending_intervals():
    idx = _idx(4)
    schedule = pd.DataFrame({"t": idx, "load_kw": [10.0, 60.0, 15.0, 55.0]})
    envelopes = pd.DataFrame({"t": idx, "max_kw": [50.0, 50.0, 50.0, 50.0]})
    result = api.envelope_violations(schedule, envelopes)
    assert list(result["t"]) == [idx[1], idx[3]]
    assert result["excess_kw"].tolist() == pytest.approx([10.0, 5.0])


def test_envelope_violations_joins_on_site_id_when_present():
    idx = _idx(2)
    schedule = pd.DataFrame({
        "t": list(idx) * 2,
        "site_id": ["a", "a", "b", "b"],
        "load_kw": [60.0, 10.0, 10.0, 10.0],
    })
    envelopes = pd.DataFrame({
        "t": list(idx) * 2,
        "site_id": ["a", "a", "b", "b"],
        "max_kw": [50.0, 50.0, 50.0, 50.0],
    })
    result = api.envelope_violations(schedule, envelopes)
    assert len(result) == 1
    assert result.iloc[0]["site_id"] == "a"


# ---------------------------------------------------------------------------
# infer_electrical
# ---------------------------------------------------------------------------

def _sites_fixture() -> pd.DataFrame:
    return pd.DataFrame({
        "site_id": ["DE-80331-abc12345", "DE-10115-def67890"],
        "operator": ["Stadtwerke A", "Stadtwerke B"],
        "lat": [48.137, 52.520],
        "lon": [11.575, 13.405],
        "postcode": ["80331", "10115"],
        "state": ["BY", "BE"],
        "rated_power_kw": [180.0, 55.0],
        "n_points": [8, 4],
        "is_dc": [True, False],
        "commissioned": ["2023-01-01", "2022-06-15"],
    })


def test_infer_electrical_is_deterministic_for_a_given_seed():
    sites = _sites_fixture()
    result_a = api.infer_electrical(sites, seed=42)
    result_b = api.infer_electrical(sites, seed=42)

    assert set(result_a) == set(result_b) == set(sites["site_id"])
    for site_id in result_a:
        a, b = result_a[site_id], result_b[site_id]
        assert a.transformer_kva == b.transformer_kva
        assert dict(a.point_phase) == dict(b.point_phase)
        assert a.provenance == b.provenance


def test_infer_electrical_sizing_and_phase_assignment():
    sites = _sites_fixture()
    result = api.infer_electrical(sites, seed=7)

    site_a = result["DE-80331-abc12345"]
    assert site_a.transformer_kva in api.STANDARD_TRANSFORMER_KVA
    needed = (180.0 / api.ASSUMED_POWER_FACTOR) * api.SIZING_HEADROOM_FACTOR
    assert site_a.transformer_kva >= needed
    assert site_a.phases == 3
    assert len(site_a.point_phase) == 8
    assert set(site_a.point_phase.values()) <= {1, 2, 3}
    assert site_a.provenance  # non-empty, documents the rule

    # Round-robin assignment: counts per phase differ by at most 1.
    counts = {1: 0, 2: 0, 3: 0}
    for phase in site_a.point_phase.values():
        counts[phase] += 1
    assert max(counts.values()) - min(counts.values()) <= 1


def test_infer_electrical_different_seeds_can_differ():
    sites = _sites_fixture()
    result_a = api.infer_electrical(sites, seed=1)
    result_b = api.infer_electrical(sites, seed=2)
    site_id = "DE-80331-abc12345"
    # Not asserting they MUST differ (a collision is technically possible),
    # just that both are internally valid and independently reproducible.
    assert len(result_a[site_id].point_phase) == len(result_b[site_id].point_phase) == 8
