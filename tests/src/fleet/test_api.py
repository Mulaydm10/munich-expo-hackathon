"""Behavioural tests for src.fleet.api — the acceptance criteria from issue #9."""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.fleet import api


def _sites() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "site_id": "DE-80331-depot1",
                "operator": "Stadtwerke Fleet Services",
                "lat": 48.13, "lon": 11.58, "postcode": "80331", "state": "BY",
                "rated_power_kw": 22.0, "n_points": 2, "is_dc": False,
                "commissioned": "2022-01-01",
            },
            {
                "site_id": "DE-80331-work1",
                "operator": "TechCorp Parking",
                "lat": 48.14, "lon": 11.57, "postcode": "80331", "state": "BY",
                "rated_power_kw": 44.0, "n_points": 4, "is_dc": False,
                "commissioned": "2021-06-01",
            },
            {
                "site_id": "DE-80331-pub1",
                "operator": "City Charge Public",
                "lat": 48.15, "lon": 11.56, "postcode": "80331", "state": "BY",
                "rated_power_kw": 22.0, "n_points": 10, "is_dc": False,
                "commissioned": "2020-03-01",
            },
            {
                "site_id": "DE-80331-dc1",
                "operator": "FastCharge Network",
                "lat": 48.16, "lon": 11.55, "postcode": "80331", "state": "BY",
                "rated_power_kw": 300.0, "n_points": 2, "is_dc": True,
                "commissioned": "2023-09-01",
            },
        ]
    )


def _weather(temp_c: float, days: list[date]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "t": pd.to_datetime([f"{d.isoformat()}T12:00:00" for d in days], utc=True),
            "station_id": ["10865"] * len(days),
            "temp_c": [temp_c] * len(days),
            "wind_ms": [3.0] * len(days),
            "ghi_w_m2": [200.0] * len(days),
        }
    )


def _frame_hash(df: pd.DataFrame) -> str:
    sortable = df.sort_values(list(df.columns)).reset_index(drop=True)
    return hashlib.sha256(pd.util.hash_pandas_object(sortable, index=False).values.tobytes()).hexdigest()


# ---------------------------------------------------------------------------
# classify_sites
# ---------------------------------------------------------------------------

def test_classify_sites_assigns_one_profile_no_nan() -> None:
    out = api.classify_sites(_sites())
    assert "profile" in out.columns
    assert out["profile"].isna().sum() == 0
    assert len(out) == len(_sites())
    assert set(out["profile"]).issubset(set(api.PROFILES.keys()))
    by_id = out.set_index("site_id")["profile"]
    assert by_id["DE-80331-depot1"] == "depot"
    assert by_id["DE-80331-work1"] == "workplace"
    assert by_id["DE-80331-dc1"] == "public_dc"
    assert by_id["DE-80331-pub1"] == "public_ac"


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------

def test_same_seed_is_byte_identical() -> None:
    sites = _sites()
    days = [date(2026, 3, 2)]
    weather = _weather(10.0, days)
    a = api.synthesise_sessions(sites, weather, days, seed=42)
    b = api.synthesise_sessions(sites, weather, days, seed=42)
    assert _frame_hash(a) == _frame_hash(b)


def test_different_seed_is_different() -> None:
    sites = _sites()
    days = [date(2026, 3, 2)]
    weather = _weather(10.0, days)
    a = api.synthesise_sessions(sites, weather, days, seed=42)
    b = api.synthesise_sessions(sites, weather, days, seed=43)
    assert _frame_hash(a) != _frame_hash(b)


# ---------------------------------------------------------------------------
# physical-feasibility invariant
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_feasibility_invariant_holds(seed: int) -> None:
    sites = _sites()
    days = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8),
            date(2026, 1, 9), date(2026, 1, 10), date(2026, 1, 11)]
    weather = _weather(-5.0, days)
    sessions = api.synthesise_sessions(sites, weather, days, seed=seed)
    assert len(sessions) > 0
    assert (sessions["t_arrive"] < sessions["t_depart"]).all()
    assert (sessions["energy_kwh"] > 0).all()
    dwell_hours = (sessions["t_depart"] - sessions["t_arrive"]) / pd.Timedelta(hours=1)
    cap = sessions["max_power_kw"] * dwell_hours
    assert (sessions["energy_kwh"] <= cap + 1e-9).all()


def test_feasibility_invariant_near_boundary_short_dwell() -> None:
    # A public_dc-like profile with an extremely short mean dwell stresses the clip logic hardest:
    # short dwell * modest power gives very little headroom to deliver energy_mean_kwh.
    tight = api.FleetParams(
        vehicles_per_point=20.0, arrival_mode_h=12.0, arrival_spread_h=6.0,
        dwell_mean_h=0.05, energy_mean_kwh=30.0, energy_cv=0.5, soc_topup_share=0.0,
        temp_penalty_pct_per_c=0.0, weekday_factor=(1.0,) * 7, profile="public_dc",
    )
    sites = _sites().iloc[[3]].copy()  # the DC site
    days = [date(2026, 4, 1)]
    weather = _weather(10.0, days)
    for seed in range(10):
        sessions = api.synthesise_sessions(
            sites, weather, days, params={"public_dc": tight}, seed=seed,
        )
        if sessions.empty:
            continue
        dwell_hours = (sessions["t_depart"] - sessions["t_arrive"]) / pd.Timedelta(hours=1)
        cap = sessions["max_power_kw"] * dwell_hours
        assert (sessions["energy_kwh"] > 0).all()
        assert (sessions["energy_kwh"] <= cap + 1e-9).all()
        assert (sessions["t_arrive"] < sessions["t_depart"]).all()


# ---------------------------------------------------------------------------
# to_load conserves energy
# ---------------------------------------------------------------------------

def test_to_load_asap_conserves_energy_per_site() -> None:
    sites = _sites()
    days = [date(2026, 3, 2), date(2026, 3, 3)]
    weather = _weather(5.0, days)
    sessions = api.synthesise_sessions(sites, weather, days, seed=7)
    load = api.to_load(sessions, policy="asap")

    energy_from_load = load.groupby("site_id")["load_kw"].sum() * 0.25
    energy_from_sessions = sessions.groupby("site_id")["energy_kwh"].sum()

    for site_id in energy_from_sessions.index:
        got = energy_from_load.get(site_id, 0.0)
        want = energy_from_sessions[site_id]
        assert got == pytest.approx(want, abs=1e-6)


# ---------------------------------------------------------------------------
# cold-weather sign
# ---------------------------------------------------------------------------

def test_cold_weather_strictly_more_energy() -> None:
    sites = _sites()
    days = [date(2026, 1, 12), date(2026, 1, 13), date(2026, 1, 14)]
    warm = api.synthesise_sessions(sites, _weather(25.0, days), days, seed=11)
    cold = api.synthesise_sessions(sites, _weather(-5.0, days), days, seed=11)

    assert cold["energy_kwh"].sum() > warm["energy_kwh"].sum()
    # The underlying arrival *process* (Poisson session count, arrival-hour draw, top-up draw) is
    # untouched by temperature -- only energy_kwh is temperature-scaled. Since issue #9, energy_kwh
    # causally drives dwell_hours, which in turn feeds the per-site occupancy allocation, so a
    # session's *emitted* t_arrive can legitimately shift (queueing) or the session can vanish
    # entirely (dropped) between warm and cold runs even though the raw arrival draw was identical
    # -- that is the occupancy fix working as designed, not a determinism regression. What must
    # still match exactly is the arrival count each run's occupancy allocator was fed.
    warm_arrivals = {site_id: occ["arrivals"] for site_id, occ in warm.attrs["occupancy"].items()}
    cold_arrivals = {site_id: occ["arrivals"] for site_id, occ in cold.attrs["occupancy"].items()}
    assert warm_arrivals == cold_arrivals


# ---------------------------------------------------------------------------
# weekday shape
# ---------------------------------------------------------------------------

def test_weekday_shape_sunday_below_tuesday_for_depot() -> None:
    sites = _sites().iloc[[0]].copy()  # depot site only
    tuesday = date(2026, 2, 3)   # 2026-02-03 is a Tuesday
    sunday = date(2026, 2, 1)    # same week's Sunday
    assert tuesday.weekday() == 1
    assert sunday.weekday() == 6

    days = [sunday, tuesday]
    weather = _weather(10.0, days)
    sessions = api.synthesise_sessions(sites, weather, days, seed=99)

    by_day = sessions.assign(day=sessions["t_arrive"].dt.tz_convert("Europe/Berlin").dt.date)
    tue_energy = by_day.loc[by_day["day"] == tuesday, "energy_kwh"].sum()
    sun_energy = by_day.loc[by_day["day"] == sunday, "energy_kwh"].sum()
    assert sun_energy < tue_energy


# ---------------------------------------------------------------------------
# flexible_energy — basic sanity (not explicitly required by the issue, but part of the contract)
# ---------------------------------------------------------------------------

def test_flexible_energy_no_nan_and_matches_columns() -> None:
    sites = _sites()
    days = [date(2026, 3, 2)]
    weather = _weather(10.0, days)
    sessions = api.synthesise_sessions(sites, weather, days, seed=3)
    flex = api.flexible_energy(sessions)
    assert list(flex.columns) == ["t", "site_id", "energy_kwh_due", "latest_start_kw"]
    assert flex.isna().sum().sum() == 0
    assert (flex["energy_kwh_due"] >= 0).all()
    assert (flex["latest_start_kw"] >= 0).all()


# ---------------------------------------------------------------------------
# occupancy: sessions may never exceed a site's n_points concurrently (issue #9)
# ---------------------------------------------------------------------------

def _dc_site(rated_power_kw: float, n_points: int) -> pd.DataFrame:
    return pd.DataFrame([{
        "site_id": "DE-tight-dc1", "operator": "FastCharge Network",
        "lat": 48.16, "lon": 11.55, "postcode": "80331", "state": "BY",
        "rated_power_kw": rated_power_kw, "n_points": n_points, "is_dc": True,
        "commissioned": "2023-09-01",
    }])


@pytest.mark.parametrize("seed", list(range(25)))
def test_occupancy_never_exceeds_rated_power(seed: int) -> None:
    # Same repro shape as the issue #9 report: a busy 2-point DC site over 30 cold days.
    sites = _dc_site(rated_power_kw=300.0, n_points=2)
    days = [date(2026, 1, 1) + timedelta(days=d) for d in range(30)]
    weather = _weather(2.0, days)
    sessions = api.synthesise_sessions(sites, weather, days, seed=seed)
    if sessions.empty:
        return
    load = api.to_load(sessions, policy="asap")
    worst = load.groupby("site_id")["load_kw"].max()
    for site_id in sessions["site_id"].unique():
        assert worst.get(site_id, 0.0) <= 300.0 + 1e-6


def test_occupancy_counts_queueing_and_dropping() -> None:
    # A single, very busy point: guarantees both queueing and dropping fire (unlike the milder
    # repro shape above, where contention is real but rarely severe enough to force a drop).
    sites = _dc_site(rated_power_kw=150.0, n_points=1)
    busy = api.FleetParams(
        vehicles_per_point=20.0, arrival_mode_h=13.0, arrival_spread_h=5.0,
        dwell_mean_h=3.0, energy_mean_kwh=30.0, energy_cv=0.35, soc_topup_share=0.15,
        temp_penalty_pct_per_c=2.0, weekday_factor=(1.0,) * 7, profile="public_dc",
    )
    days = [date(2026, 1, 1) + timedelta(days=d) for d in range(30)]
    weather = _weather(2.0, days)
    sessions = api.synthesise_sessions(sites, weather, days, params={"public_dc": busy}, seed=6)

    occupancy = sessions.attrs["occupancy"]
    assert isinstance(occupancy, dict)  # plain dict, not a DataFrame -- see synthesise_sessions docstring
    row = occupancy["DE-tight-dc1"]
    assert "queued" in row and "dropped" in row
    assert row["arrivals"] == row["served"] + row["dropped"]
    assert row["queued"] > 0
    assert row["dropped"] > 0
    # every served session is either immediate or counted as queued via queued_h
    served = sessions[sessions["site_id"] == "DE-tight-dc1"]
    assert (served["queued_h"] >= 0).all()
    assert int((served["queued_h"] > 0).sum()) == row["queued"]


# ---------------------------------------------------------------------------
# residual energy clip (issue #9): still counted, but should now stay rare
# ---------------------------------------------------------------------------

def test_residual_energy_clip_rate_stays_low() -> None:
    sites = pd.concat([
        _dc_site(rated_power_kw=150.0, n_points=5).assign(site_id="DE-public-dc"),
        _sites().iloc[[1]],  # workplace site
    ], ignore_index=True)
    days = [date(2026, 2, 1) + timedelta(days=d) for d in range(28)]
    weather = _weather(2.0, days)
    sessions = api.synthesise_sessions(sites, weather, days, seed=7)

    assert "energy_clipped" in sessions.columns
    clip_rate = sessions["energy_clipped"].mean()
    assert clip_rate < 0.02  # the causal energy->dwell link (issue #9) should make this near-zero


# ---------------------------------------------------------------------------
# national-temperature limitation is documented (issue #9, non-blocking design note)
# ---------------------------------------------------------------------------

def test_docstring_documents_national_temperature_limitation() -> None:
    assert "national" in api.synthesise_sessions.__doc__.lower()


# ---------------------------------------------------------------------------
# phase/n_phases naming collision with src/grid's point_phase (issue #9, non-blocking)
# ---------------------------------------------------------------------------

def test_phase_column_renamed_to_n_phases() -> None:
    sites = _sites()
    days = [date(2026, 3, 2)]
    weather = _weather(10.0, days)
    sessions = api.synthesise_sessions(sites, weather, days, seed=3)
    assert "n_phases" in sessions.columns
    assert "phase" not in sessions.columns
    assert set(sessions["n_phases"].unique()).issubset({1, 3})
