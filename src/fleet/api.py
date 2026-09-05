"""src.fleet — public API.

Synthesise charging sessions from real charge-point sites, and turn those
sessions into the uncontrolled ("as soon as plugged in") baseline load curve
that everything downstream (forecast, grid, sched) is compared against.

This module is the lane's ONLY cross-lane surface: other lanes import
`src.fleet.api` and nothing else. See `contracts/src/fleet.md` for the
interface this lane owes the rest of the project, and
`contracts/CONVENTIONS.md` for units, time handling and the data layout.

Explicitly out of scope (see VISION.md non-goals): V2G and battery
degradation are not modelled anywhere in this lane.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

LANE = "src/fleet"

_BERLIN = ZoneInfo("Europe/Berlin")

Profile = Literal["depot", "workplace", "public_ac", "public_dc"]


@dataclass(frozen=True)
class FleetParams:
    vehicles_per_point: float          # utilisation assumption: vehicles served per point per day
    arrival_mode_h: float              # local-time hour of peak plug-in
    arrival_spread_h: float            # std-dev (hours) of arrival time around the mode
    dwell_mean_h: float                # mean dwell time in hours (exponential draw)
    energy_mean_kwh: float             # mean energy delivered per full (non-top-up) session
    energy_cv: float                   # coefficient of variation of energy_kwh
    soc_topup_share: float             # fraction of sessions that are short top-ups
    temp_penalty_pct_per_c: float      # % extra energy per degree C below 10 C
    weekday_factor: tuple[float, ...]  # Mon..Sun multipliers on expected session count
    profile: Profile


# Every number below is an assumption a judge may challenge (contracts/src/fleet.md). None of
# these are fitted to a real dataset yet — the organizers' sample (Q-0007), if it arrives, is what
# `validate_against` will eventually calibrate these against. Until then every value is a "guess"
# unless the comment says otherwise (a couple are pinned by the contract text itself).
PROFILES: dict[str, FleetParams] = {
    "depot": FleetParams(
        vehicles_per_point=1.2,        # guess: depot points are shared across >1 vehicle per shift turnover
        arrival_mode_h=18.0,           # contracts/src/fleet.md gives this exact value as the example
        arrival_spread_h=1.5,          # guess: fleet vehicles return from routes within a ~1.5h window
        dwell_mean_h=10.0,             # guess: overnight dwell, long enough to fully charge before next shift
        energy_mean_kwh=40.0,          # guess: light commercial van, ~150-250km/day at ~0.2kWh/km
        energy_cv=0.25,                # guess: fleet routes are repetitive day to day, low variability
        soc_topup_share=0.05,          # guess: overnight charging usually suffices, few daytime top-ups
        temp_penalty_pct_per_c=1.5,    # guess: common industry rule-of-thumb BEV efficiency loss below 10C
        weekday_factor=(1.0, 1.0, 1.0, 1.0, 1.0, 0.5, 0.2),  # guess: reduced weekend routes (Sat/Sun)
        profile="depot",
    ),
    "workplace": FleetParams(
        vehicles_per_point=1.0,        # guess: ~one commuter car per point per workday
        arrival_mode_h=8.5,            # guess: typical commute arrival just before a 9am start
        arrival_spread_h=1.0,          # guess: most commuters arrive within a tight morning window
        dwell_mean_h=8.0,              # guess: parked/charging for a full workday
        energy_mean_kwh=12.0,          # guess: average commute round-trip is short, small top-up
        energy_cv=0.4,                 # guess: commute distances vary more than fleet routes
        soc_topup_share=0.10,          # guess: some workers plug in briefly between meetings
        temp_penalty_pct_per_c=1.5,    # guess: same BEV cold-efficiency rule of thumb as depot
        weekday_factor=(1.0, 1.0, 1.0, 1.0, 1.0, 0.05, 0.02),  # guess: workplaces are ~closed on weekends
        profile="workplace",
    ),
    "public_ac": FleetParams(
        vehicles_per_point=2.0,        # guess: public AC points see more turnover than dedicated points
        arrival_mode_h=17.0,           # guess: peak aligns with early-evening errands/shopping
        arrival_spread_h=4.0,          # guess: public arrivals are spread widely across the day
        dwell_mean_h=2.5,              # guess: shopping/errand-length dwell
        energy_mean_kwh=15.0,          # guess: partial top-up typical of public AC use
        energy_cv=0.5,                 # guess: highly variable mix of near-empty/near-full arrivals
        soc_topup_share=0.30,          # guess: many public AC sessions are short top-ups
        temp_penalty_pct_per_c=1.5,    # guess: same BEV cold-efficiency rule of thumb
        weekday_factor=(0.9, 0.9, 0.9, 0.9, 1.0, 1.1, 1.0),  # guess: slightly busier Fri/Sat
        profile="public_ac",
    ),
    "public_dc": FleetParams(
        vehicles_per_point=4.0,        # guess: DC fast chargers turn over many short sessions per day
        arrival_mode_h=13.0,           # guess: peak motorway/travel charging around midday
        arrival_spread_h=5.0,          # guess: DC arrivals spread across daytime travel hours
        dwell_mean_h=0.5,              # guess: ~30 minute fast-charge session
        energy_mean_kwh=30.0,          # guess: larger single-session top-up on a long trip
        energy_cv=0.35,                # guess
        soc_topup_share=0.15,          # guess: still mostly meaningful top-ups, fewer trivial ones than AC
        temp_penalty_pct_per_c=2.0,    # guess: DC charge curves throttle more in the cold than AC
        weekday_factor=(1.0, 1.0, 1.0, 1.0, 1.05, 1.15, 1.1),  # guess: weekend travel raises DC usage
        profile="public_dc",
    ),
}

_SITE_COLUMNS = ("site_id", "rated_power_kw", "n_points", "is_dc", "operator")
_SESSION_LOAD_COLUMNS = ("site_id", "t_arrive", "t_depart", "energy_kwh", "max_power_kw")
_SESSION_FLEX_COLUMNS = ("site_id", "t_arrive", "deadline_t", "energy_kwh", "max_power_kw")


def _require_columns(df: pd.DataFrame, cols: Sequence[str], *, label: str) -> None:
    """Boundary assert used throughout this lane (src.data.api's own require_columns is not yet
    implemented upstream, so this is a small private equivalent kept local to this module)."""
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{label} missing required columns: {missing}")


def classify_sites(sites: pd.DataFrame) -> pd.DataFrame:
    """Assign exactly one `profile` to every row of `sites`, derived from rated power, point
    count and operator name. Rules (all guesses — no ground-truth labels exist yet):

    1. Operator name contains a fleet/logistics keyword -> "depot".
    2. DC-capable and >=50kW per point -> "public_dc" (typical fast/ultra-fast DC rating band).
    3. AC, small cluster (<=4 points) and <=22kW per point -> "workplace" (typical AC workplace
       charger rating).
    4. Everything else -> "public_ac" (catch-all; guarantees no NaN and full coverage).
    """
    _require_columns(sites, _SITE_COLUMNS, label="sites")
    out = sites.copy()

    n_points = out["n_points"].astype(float).clip(lower=1)
    power_per_point = out["rated_power_kw"].astype(float) / n_points
    is_dc = out["is_dc"].astype(bool)
    operator_lower = out["operator"].fillna("").astype(str).str.lower()

    depot_keywords = ("depot", "fleet", "spedition", "logistik", "logistics", "post", "bus")
    is_depot_operator = operator_lower.apply(lambda s: any(k in s for k in depot_keywords))

    profile = np.select(
        [
            is_depot_operator,
            is_dc & (power_per_point >= 50.0),
            (~is_dc) & (n_points <= 4) & (power_per_point <= 22.0),
        ],
        ["depot", "public_dc", "workplace"],
        default="public_ac",
    )
    out["profile"] = profile
    return out


def _site_seed(site_id: str, day: date, seed: int) -> int:
    """Derive an independent RNG seed per (site_id, date, seed). Using a SHA-256 hash of the key
    (rather than e.g. a single global `np.random.default_rng(seed)` shared across sites) means
    adding or removing a site never perturbs any other site's history: each key gets its own
    reproducible stream, so results stay comparable across runs."""
    digest = hashlib.sha256(f"{site_id}|{day.isoformat()}|{seed}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % (2**32 - 1)


def _lognormal_mu_sigma(mean: float, cv: float) -> tuple[float, float]:
    sigma2 = np.log1p(cv**2)
    mu = np.log(max(mean, 1e-9)) - sigma2 / 2.0
    return mu, float(np.sqrt(sigma2))


def _daily_mean_temp_c(weather: pd.DataFrame | None, day: date) -> float:
    if weather is None or weather.empty:
        return 10.0
    mask = weather["t"].dt.tz_convert("UTC").dt.date == day
    vals = weather.loc[mask, "temp_c"]
    if vals.empty:
        return 10.0
    return float(vals.mean())


def _local_hour_to_utc(day: date, hour: float) -> pd.Timestamp:
    naive = datetime.combine(day, time(0, 0)) + timedelta(hours=float(hour))
    ts = pd.Timestamp(naive)
    # ambiguous=False / nonexistent="shift_forward": deterministic, never raises, so a DST-shifted
    # day (25h / 23h, e.g. 2026-10-25) never crashes the pipeline (contracts/CONVENTIONS.md).
    localized = ts.tz_localize(_BERLIN, ambiguous=False, nonexistent="shift_forward")
    return localized.tz_convert("UTC")


def synthesise_sessions(
    sites: pd.DataFrame,
    weather: pd.DataFrame,
    days: Sequence[date],
    *,
    params: Mapping[str, FleetParams] = PROFILES,
    seed: int,
) -> pd.DataFrame:
    """Deterministic synthetic charging sessions for `sites` over `days`.

    Columns: session_id, site_id, t_arrive, t_depart, energy_kwh, max_power_kw, phase (1|3),
    deadline_t (== t_depart), profile.

    Feasibility invariant enforced for every emitted row: t_arrive < t_depart, energy_kwh > 0,
    and energy_kwh <= max_power_kw * dwell_hours (an infeasible draw is clipped, never emitted).
    """
    _require_columns(sites, _SITE_COLUMNS, label="sites")
    if "profile" not in sites.columns:
        sites = classify_sites(sites)

    columns = [
        "session_id", "site_id", "t_arrive", "t_depart", "energy_kwh",
        "max_power_kw", "phase", "deadline_t", "profile",
    ]
    rows: list[dict] = []

    for _, site in sites.iterrows():
        site_id = site["site_id"]
        n_points = max(int(site["n_points"]), 1)
        max_power_kw = max(float(site["rated_power_kw"]) / n_points, 1.0)
        phase = 1 if max_power_kw <= 7.4 else 3
        p = params[site["profile"]]

        for day in days:
            weekday = day.weekday()  # Mon=0..Sun=6 — matches weekday_factor's own Mon..Sun order
            temp_c = _daily_mean_temp_c(weather, day)
            temp_multiplier = 1.0 + (p.temp_penalty_pct_per_c / 100.0) * max(0.0, 10.0 - temp_c)

            rng = np.random.default_rng(_site_seed(site_id, day, seed))
            expected_sessions = p.vehicles_per_point * n_points * p.weekday_factor[weekday]
            n_sessions = int(rng.poisson(max(expected_sessions, 0.0)))

            for i in range(n_sessions):
                is_topup = bool(rng.random() < p.soc_topup_share)

                arrival_h = float(rng.normal(p.arrival_mode_h, p.arrival_spread_h)) % 24.0
                t_arrive = _local_hour_to_utc(day, arrival_h)

                dwell_scale = 0.3 if is_topup else 1.0
                dwell_hours = max(float(rng.exponential(p.dwell_mean_h * dwell_scale)), 0.05)
                t_depart = t_arrive + pd.Timedelta(dwell_hours, unit="h")

                energy_scale = 0.25 if is_topup else 1.0
                mu, sigma = _lognormal_mu_sigma(p.energy_mean_kwh * energy_scale, p.energy_cv)
                energy_kwh = float(rng.lognormal(mu, sigma)) * temp_multiplier

                cap = max_power_kw * dwell_hours
                # never let a session demand more energy than its own dwell time can deliver
                energy_kwh = min(energy_kwh, cap * 0.98)
                energy_kwh = max(energy_kwh, 1e-4)

                rows.append({
                    "session_id": f"{site_id}-{day.isoformat()}-{i:04d}-{seed}",
                    "site_id": site_id,
                    "t_arrive": t_arrive,
                    "t_depart": t_depart,
                    "energy_kwh": energy_kwh,
                    "max_power_kw": max_power_kw,
                    "phase": phase,
                    "deadline_t": t_depart,
                    "profile": site["profile"],
                })

    if not rows:
        empty = pd.DataFrame(columns=columns)
        empty["t_arrive"] = pd.to_datetime(empty["t_arrive"], utc=True)
        empty["t_depart"] = pd.to_datetime(empty["t_depart"], utc=True)
        empty["deadline_t"] = pd.to_datetime(empty["deadline_t"], utc=True)
        return empty

    out = pd.DataFrame(rows, columns=columns)
    out["t_arrive"] = pd.to_datetime(out["t_arrive"], utc=True)
    out["t_depart"] = pd.to_datetime(out["t_depart"], utc=True)
    out["deadline_t"] = out["t_depart"]
    return out.sort_values(["site_id", "t_arrive", "session_id"]).reset_index(drop=True)


def _bin_contributions(
    t_start: pd.Timestamp, t_end: pd.Timestamp, rate_kw: float, freq_delta: pd.Timedelta,
) -> list[tuple[pd.Timestamp, float]]:
    """Split a constant-rate interval [t_start, t_end) into interval-start-labelled bins of width
    `freq_delta`, apportioning the rate by time overlap so that
    sum(load_kw) * bin_hours == rate_kw * (t_end - t_start) exactly (energy-conserving by
    construction, regardless of grid alignment)."""
    if t_end <= t_start or rate_kw <= 0:
        return []
    bin_hours = freq_delta / pd.Timedelta(1, unit="h")
    first_bin = t_start.floor(freq_delta)
    n_bins = int(np.ceil((t_end - first_bin) / freq_delta))
    contributions = []
    for i in range(n_bins):
        bin_start = first_bin + i * freq_delta
        bin_end = bin_start + freq_delta
        overlap = min(bin_end, t_end) - max(bin_start, t_start)
        overlap_h = overlap / pd.Timedelta(1, unit="h")
        if overlap_h > 0:
            contributions.append((bin_start, rate_kw * overlap_h / bin_hours))
    return contributions


def to_load(
    sessions: pd.DataFrame, *, freq: str = "15min", policy: Literal["asap", "even"] = "asap",
) -> pd.DataFrame:
    """The uncontrolled baseline load: t, site_id, load_kw.

    `asap` charges at max_power_kw from t_arrive until energy_kwh is delivered (the behaviour
    this project exists to improve on). `even` spreads energy_kwh evenly across the whole dwell
    window [t_arrive, t_depart). Conserves energy per site: sum(load_kw) * bin_hours ==
    sum(energy_kwh), within float tolerance.
    """
    _require_columns(sessions, _SESSION_LOAD_COLUMNS, label="sessions")
    if policy not in ("asap", "even"):
        raise ValueError(f"unknown policy: {policy!r}")

    freq_delta = pd.Timedelta(freq)
    records: list[tuple[pd.Timestamp, object, float]] = []

    for row in sessions.itertuples(index=False):
        dwell_hours = (row.t_depart - row.t_arrive) / pd.Timedelta(1, unit="h")
        if dwell_hours <= 0:
            continue
        if policy == "asap":
            rate_kw = row.max_power_kw
            duration_h = row.energy_kwh / rate_kw if rate_kw > 0 else 0.0
            start, end = row.t_arrive, row.t_arrive + pd.Timedelta(duration_h, unit="h")
        else:  # even
            rate_kw = row.energy_kwh / dwell_hours
            start, end = row.t_arrive, row.t_depart

        for bin_start, load_kw in _bin_contributions(start, end, rate_kw, freq_delta):
            records.append((bin_start, row.site_id, load_kw))

    out = pd.DataFrame(records, columns=["t", "site_id", "load_kw"])
    if out.empty:
        return out
    out = out.groupby(["t", "site_id"], as_index=False)["load_kw"].sum()
    out["t"] = pd.to_datetime(out["t"], utc=True)
    return out.sort_values(["site_id", "t"]).reset_index(drop=True)


def flexible_energy(sessions: pd.DataFrame, *, freq: str = "15min") -> pd.DataFrame:
    """The headroom a scheduler is allowed to move: t, site_id, energy_kwh_due, latest_start_kw.

    `energy_kwh_due` is energy_kwh spread evenly across [t_arrive, deadline_t) per bin (the
    committed demand a scheduler must place somewhere in that window). `latest_start_kw` is the
    load if every session instead waited as long as possible and charged at max_power_kw right up
    to its deadline — the other edge of the schedulable window.
    """
    _require_columns(sessions, _SESSION_FLEX_COLUMNS, label="sessions")
    freq_delta = pd.Timedelta(freq)
    bin_hours = freq_delta / pd.Timedelta(1, unit="h")
    due_records: list[tuple[pd.Timestamp, object, float]] = []
    urgent_records: list[tuple[pd.Timestamp, object, float]] = []

    for row in sessions.itertuples(index=False):
        dwell_hours = (row.deadline_t - row.t_arrive) / pd.Timedelta(1, unit="h")
        if dwell_hours <= 0:
            continue

        even_rate = row.energy_kwh / dwell_hours
        for bin_start, load_kw in _bin_contributions(row.t_arrive, row.deadline_t, even_rate, freq_delta):
            due_records.append((bin_start, row.site_id, load_kw * bin_hours))

        latest_duration_h = row.energy_kwh / row.max_power_kw if row.max_power_kw > 0 else 0.0
        latest_start = row.deadline_t - pd.Timedelta(latest_duration_h, unit="h")
        for bin_start, load_kw in _bin_contributions(latest_start, row.deadline_t, row.max_power_kw, freq_delta):
            urgent_records.append((bin_start, row.site_id, load_kw))

    due_df = pd.DataFrame(due_records, columns=["t", "site_id", "energy_kwh_due"])
    urgent_df = pd.DataFrame(urgent_records, columns=["t", "site_id", "latest_start_kw"])

    if not due_df.empty:
        due_df = due_df.groupby(["t", "site_id"], as_index=False)["energy_kwh_due"].sum()
    if not urgent_df.empty:
        urgent_df = urgent_df.groupby(["t", "site_id"], as_index=False)["latest_start_kw"].sum()

    if due_df.empty and urgent_df.empty:
        return pd.DataFrame(columns=["t", "site_id", "energy_kwh_due", "latest_start_kw"])

    out = pd.merge(due_df, urgent_df, on=["t", "site_id"], how="outer").fillna(0.0)
    out["t"] = pd.to_datetime(out["t"], utc=True)
    return out.sort_values(["site_id", "t"]).reset_index(drop=True)
