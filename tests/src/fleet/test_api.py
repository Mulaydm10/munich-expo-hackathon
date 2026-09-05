"""Behavioural tests for src.fleet.api — the acceptance criteria from issue #9."""

from __future__ import annotations

import hashlib
from datetime import date

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
    # arrivals/dwells should be identical (only the energy draw is temperature-scaled)
    pd.testing.assert_series_equal(
        warm.sort_values(["site_id", "t_arrive"])["t_arrive"].reset_index(drop=True),
        cold.sort_values(["site_id", "t_arrive"])["t_arrive"].reset_index(drop=True),
    )


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
