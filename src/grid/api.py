"""src.grid — public API.

Transformer thermal + three-phase feasible power envelope per site.

This module is the lane's ONLY cross-lane surface: other lanes import
`src.grid.api` and nothing else. See `contracts/src/grid.md` for the
interface this lane owes the rest of the project, and
`contracts/CONVENTIONS.md` for units, time handling and the data layout.

## Model implemented

IEC 60076-7 ("Loading guide for oil-immersed power transformers"), clause 7
ultimate-value / difference-equation form, in its simplified two-exponential
shape:

    delta_oil_ultimate(K)  = delta_top_oil_rated_c * ((1 + R*K**2) / (1 + R)) ** x
    delta_hotspot_ultimate(K) = hotspot_gradient_rated_c * K ** y
    state(t+dt) = state(t) + (ultimate(K) - state(t)) * (1 - exp(-dt / tau))
    hotspot(t)  = ambient(t) + delta_oil(t) + delta_hotspot(t)

applied once per row as a recursive difference equation, so the result at row
i depends on every row before it (the whole load path), not on load(i) alone.
This implementation omits the full annex's k11/k21/k22 top-of-winding-oil
correction factors — a documented simplification, adequate at this project's
15-minute resolution, not a claim of exact conformance to every clause.

`thermal_envelope` inverts the same one-step update by bisection: for each
row it solves for the largest constant K that, applied for exactly that one
step from the *inherited* thermal state, keeps the resulting hotspot at or
below `hotspot_limit_c`. Chaining these steps (using each solved K as the
"actual" load feeding the next row's inherited state, exactly as a caller
that dispatches at the envelope would) is what makes "hold load at max_kw for
the whole window" come out tight against the limit rather than merely safe.

## Assumption discipline (contracts/src/grid.md)

Every constant below is a named module attribute with a comment saying where
it came from. Several are flagged GUESS: this project has no manufacturer
heat-run test report for any site in the registry, so IEC 60076-7's own
"default value" fallback tables cannot be looked up per-transformer — a
single registry-wide assumption stands in for them. Do not read GUESS values
as sourced; they are documented placeholders a real deployment would replace
with test-report data.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

LANE = "src/grid"

# ---------------------------------------------------------------------------
# IEC 60076-7 thermal-model constants
# ---------------------------------------------------------------------------

# Reference ambient temperature nameplate ratings are quoted against (IEC
# 60076 series "average ambient temperature" reference). A genuine standard
# constant, not a project guess.
IEC_REFERENCE_AMBIENT_C = 20.0

# Oil exponent (x) and winding exponent (y): IEC 60076-7's own stated DEFAULT
# exponents "to be used when no [heat-run test] data is otherwise available",
# for ONAN-cooled distribution-class transformers (the class this project's
# depot/workplace sites use). Commonly cited in secondary transformer-
# engineering literature; treat as reasonably sourced, not exact-for-our-
# equipment (we have no test report for any site in the registry).
IEC_OIL_EXPONENT_X = 0.8
IEC_WINDING_EXPONENT_Y = 1.6

# R: ratio of rated load loss to no-load loss at rated current. The standard
# keys its own default off a heat-run test.
# GUESS: no manufacturer loss data exists for this project's registry sites;
# a commonly quoted mid-range distribution-transformer value is assumed.
ASSUMED_LOSS_RATIO_R = 6.0

# Rated hot-spot-to-top-oil gradient, expressed as a fraction of the rated
# top-oil rise carried on SiteElectrical.delta_top_oil_rated_c. The standard
# derives this figure from the same heat-run test.
# GUESS: assumed proportion, not sourced from a specific test report.
ASSUMED_HOTSPOT_GRADIENT_RATIO = 0.3

# Registry-wide thermal defaults used by infer_electrical (no per-site test
# report exists to derive these from).
# GUESS: tau_oil_min is in the range IEC 60076-7 commonly cites for
# distribution transformers (~180 min); tau_winding_min and the rated
# top-oil rise are round, documented placeholders, not manufacturer figures.
# DEFAULT_DELTA_TOP_OIL_RATED_C is deliberately chosen so that, together with
# ASSUMED_HOTSPOT_GRADIENT_RATIO and IEC_HOTSPOT_SUSTAINED_LIMIT_C below, K=1
# at IEC_REFERENCE_AMBIENT_C lands exactly on the sustained hot-spot limit
# (60 * (1 + 0.3) + 20 == 98) -- this is what makes the contract's "steady
# load at the rated reference reduces to nameplate" guarantee hold exactly
# for infer_electrical's own output, rather than only approximately. A
# caller assembling a custom SiteElectrical should preserve this relation
# (delta_top_oil_rated_c * (1 + ASSUMED_HOTSPOT_GRADIENT_RATIO) ==
# hotspot_limit_c - IEC_REFERENCE_AMBIENT_C) if it wants the same guarantee.
DEFAULT_TAU_OIL_MIN = 180.0
DEFAULT_TAU_WINDING_MIN = 10.0
DEFAULT_DELTA_TOP_OIL_RATED_C = 60.0

# Sustained / long-time-emergency hot-spot temperature limits as commonly
# cited from the IEC 60076-7 loading guide (normal cyclic-loading life
# expectancy limit, and long-time emergency limit).
# GUESS-flagged: figures as commonly cited from the guide; not re-derived
# here from the standard's ageing-rate table for a specific edition.
IEC_HOTSPOT_SUSTAINED_LIMIT_C = 98.0
IEC_HOTSPOT_EMERGENCY_LIMIT_C = 120.0

# Power factor assumed to convert transformer_kva (apparent power) into a kW
# figure comparable to charging demand.
# GUESS: EVSE loads are typically close to unity but not exactly 1; no
# measured figure exists for these sites.
ASSUMED_POWER_FACTOR = 0.95

# Ceiling on the envelope, as a multiple of nameplate-equivalent kW, even
# under extreme cold. Without this an arbitrarily cold ambient could imply an
# unbounded short-term rating. This IS the "record how often the clamp
# binds" case the task calls out: `thermal_envelope` returns a `clipped`
# column precisely so a binding clamp is visible, not silently hidden.
# GUESS: chosen as a conservative short-term-emergency multiple; not itself
# an IEC figure.
MAX_ENVELOPE_MULTIPLE_OF_NAMEPLATE = 1.8

# Standard distribution-transformer kVA ratings infer_electrical's sizing
# rule rounds up to.
# GUESS: an illustrative common-ratings ladder, not sourced from one
# manufacturer's or DSO's actual catalogue.
STANDARD_TRANSFORMER_KVA: tuple[float, ...] = (
    50.0, 100.0, 160.0, 250.0, 315.0, 400.0, 500.0,
    630.0, 800.0, 1000.0, 1250.0, 1600.0, 2000.0, 2500.0,
)

# infer_electrical's sizing headroom: installed EV capacity is multiplied by
# this before rounding up to a standard transformer size, to leave room for
# site auxiliary load and future points.
# GUESS: a round, documented margin, not derived from a specific DSO rule.
SIZING_HEADROOM_FACTOR = 1.15

# CONVENTIONS.md: native resolution is 15 minutes. Used as the assumed
# interval length for the first row of a series (no previous timestamp to
# diff against) and as a fallback when an index carries no timestamps at all.
DEFAULT_RESOLUTION_MINUTES = 15.0

# thermal_envelope's per-row bisection: search domain and iteration count.
# 60 halvings of a [0, 5] pu window resolve K to well under 1e-9 pu, far
# tighter than the 2% acceptance tolerance; this is a numerics choice, not a
# physical assumption.
_ENVELOPE_K_SEARCH_HI = 5.0
_ENVELOPE_BISECTION_ITERS = 60


@dataclass(frozen=True)
class SiteElectrical:
    site_id: str
    transformer_kva: float
    phases: int  # 1 or 3
    tau_oil_min: float  # oil time constant, minutes
    tau_winding_min: float
    delta_top_oil_rated_c: float  # rated top-oil rise
    hotspot_limit_c: float  # sustained limit (default from IEC 60076-7 loading guide)
    hotspot_emergency_c: float
    point_phase: Mapping[str, int]  # charge point -> phase it is wired to
    # Not in contracts/src/grid.md's code sample, but infer_electrical's own
    # docstring requires recording its sizing rule "in the returned object's
    # provenance field" -- added as an additive field with a default so it
    # does not disturb any positional construction already matching the
    # contract's eight fields.
    provenance: str = ""

    @property
    def rated_kw(self) -> float:
        """kW-equivalent of transformer_kva at ASSUMED_POWER_FACTOR."""
        return self.transformer_kva * ASSUMED_POWER_FACTOR


# ---------------------------------------------------------------------------
# infer_electrical
# ---------------------------------------------------------------------------

def infer_electrical(sites: pd.DataFrame, *, seed: int) -> dict[str, SiteElectrical]:
    """Derive a SiteElectrical per row of a `sites` table (contracts/src/data.md
    schema: site_id, rated_power_kw, n_points, ... ) that has no transformer
    rating of its own.

    Sizing rule (documented, deterministic, recorded in `.provenance`):
    required kVA = rated_power_kw / ASSUMED_POWER_FACTOR * SIZING_HEADROOM_FACTOR,
    rounded up to the nearest entry in STANDARD_TRANSFORMER_KVA. Charge
    points (synthesised as `<site_id>-cp<i>` since the sites table carries
    only a count) are round-robin-assigned across the 3 phases after a
    seeded, per-site shuffle -- deterministic for a given seed, independent
    of PYTHONHASHSEED (uses zlib.crc32, not the built-in `hash`).
    """
    _require_columns(sites, ["site_id", "rated_power_kw", "n_points"])
    result: dict[str, SiteElectrical] = {}
    for row in sites.itertuples(index=False):
        site_id = str(row.site_id)
        rated_power_kw = float(row.rated_power_kw)
        n_points = int(row.n_points)

        needed_kva = (rated_power_kw / ASSUMED_POWER_FACTOR) * SIZING_HEADROOM_FACTOR
        transformer_kva = next(
            (s for s in STANDARD_TRANSFORMER_KVA if s >= needed_kva),
            STANDARD_TRANSFORMER_KVA[-1],
        )

        site_seed = zlib.crc32(f"{seed}:{site_id}".encode("utf-8"))
        rng = np.random.default_rng(site_seed)
        point_ids = [f"{site_id}-cp{i:03d}" for i in range(n_points)]
        order = rng.permutation(n_points) if n_points else np.array([], dtype=int)
        point_phase = {point_ids[order[i]]: (i % 3) + 1 for i in range(n_points)}

        provenance = (
            f"transformer_kva={transformer_kva} sized from rated_power_kw="
            f"{rated_power_kw:.1f}kW via needed_kva=rated_power_kw/"
            f"ASSUMED_POWER_FACTOR({ASSUMED_POWER_FACTOR})*SIZING_HEADROOM_FACTOR"
            f"({SIZING_HEADROOM_FACTOR})={needed_kva:.1f}kVA, rounded up to the "
            f"nearest entry in STANDARD_TRANSFORMER_KVA (GUESS ladder, see "
            f"src/grid/api.py module docstring). n_points={n_points} synthetic "
            f"charge points ({point_ids[:1] or ['<none>']}...) round-robin "
            f"assigned across 3 phases after a seed={seed}-derived shuffle."
        )

        result[site_id] = SiteElectrical(
            site_id=site_id,
            transformer_kva=float(transformer_kva),
            phases=3,
            tau_oil_min=DEFAULT_TAU_OIL_MIN,
            tau_winding_min=DEFAULT_TAU_WINDING_MIN,
            delta_top_oil_rated_c=DEFAULT_DELTA_TOP_OIL_RATED_C,
            hotspot_limit_c=IEC_HOTSPOT_SUSTAINED_LIMIT_C,
            hotspot_emergency_c=IEC_HOTSPOT_EMERGENCY_LIMIT_C,
            point_phase=point_phase,
            provenance=provenance,
        )
    return result


# ---------------------------------------------------------------------------
# Thermal model
# ---------------------------------------------------------------------------

def _dt_minutes(index: pd.Index) -> np.ndarray:
    """Interval length feeding each row's difference-equation step.

    Real gap between consecutive timestamps for a DatetimeIndex; the first
    row (no predecessor) and any non-timestamp index fall back to
    DEFAULT_RESOLUTION_MINUTES (CONVENTIONS.md's native 15-minute cadence).
    """
    n = len(index)
    if n == 0:
        return np.array([], dtype=float)
    if isinstance(index, pd.DatetimeIndex) and n > 1:
        deltas = np.diff(index.values).astype("timedelta64[s]").astype(float) / 60.0
        return np.concatenate(([DEFAULT_RESOLUTION_MINUTES], deltas))
    return np.full(n, DEFAULT_RESOLUTION_MINUTES)


def _ultimate_oil_rise(k: float, site: SiteElectrical) -> float:
    return site.delta_top_oil_rated_c * (
        ((1.0 + ASSUMED_LOSS_RATIO_R * k * k) / (1.0 + ASSUMED_LOSS_RATIO_R))
        ** IEC_OIL_EXPONENT_X
    )


def _ultimate_hotspot_gradient(k: float, site: SiteElectrical) -> float:
    rated_gradient = ASSUMED_HOTSPOT_GRADIENT_RATIO * site.delta_top_oil_rated_c
    return rated_gradient * (max(k, 0.0) ** IEC_WINDING_EXPONENT_Y)


def _step_state(
    delta_o: float, delta_h: float, k: float, dt_min: float, site: SiteElectrical
) -> tuple[float, float]:
    """One difference-equation step: state(t+dt) given state(t) and load K."""
    ult_o = _ultimate_oil_rise(k, site)
    ult_h = _ultimate_hotspot_gradient(k, site)
    alpha_o = 1.0 - math.exp(-dt_min / site.tau_oil_min)
    alpha_h = 1.0 - math.exp(-dt_min / site.tau_winding_min)
    new_o = delta_o + (ult_o - delta_o) * alpha_o
    new_h = delta_h + (ult_h - delta_h) * alpha_h
    return new_o, new_h


def _validate_aligned(load_kw: pd.Series, ambient_c: pd.Series) -> None:
    if len(load_kw) != len(ambient_c):
        raise ValueError("load_kw and ambient_c must be the same length")
    if isinstance(load_kw.index, pd.DatetimeIndex) and isinstance(
        ambient_c.index, pd.DatetimeIndex
    ):
        if not load_kw.index.equals(ambient_c.index):
            raise ValueError("load_kw and ambient_c must share the same time index")


def _simulate(
    load_kw: pd.Series,
    ambient_c: pd.Series,
    site: SiteElectrical,
    delta_o0: float,
    delta_h0: float,
) -> tuple[np.ndarray, float, float]:
    """Run the difference equation across a whole load path.

    Returns (hotspot array, final delta_o, final delta_h) -- the final state
    is what lets thermal_envelope warm up its own state from `prior_load_kw`
    and chain windows without re-deriving the recursion twice.
    """
    dt = _dt_minutes(load_kw.index)
    n = len(load_kw)
    hotspot = np.empty(n)
    delta_o, delta_h = delta_o0, delta_h0
    load_vals = load_kw.to_numpy(dtype=float)
    ambient_vals = ambient_c.to_numpy(dtype=float)
    for i in range(n):
        k = max(0.0, load_vals[i] / site.rated_kw)
        delta_o, delta_h = _step_state(delta_o, delta_h, k, float(dt[i]), site)
        hotspot[i] = ambient_vals[i] + delta_o + delta_h
    return hotspot, delta_o, delta_h


def hotspot_temperature(
    load_kw: pd.Series, ambient_c: pd.Series, site: SiteElectrical
) -> pd.Series:
    """Exponential top-oil + winding model (IEC 60076-7 clause 7, difference-
    equation form). Stateful in time: the answer at t depends on the whole
    preceding load path (state starts cold: 0 oil rise, 0 hot-spot gradient,
    at the series' first row), which is the point -- see module docstring.
    """
    _validate_aligned(load_kw, ambient_c)
    hotspot, _, _ = _simulate(load_kw, ambient_c, site, 0.0, 0.0)
    return pd.Series(hotspot, index=load_kw.index, name="hotspot_c")


def _solve_k_for_target(
    delta_o: float, delta_h: float, target_rise: float, dt_min: float, site: SiteElectrical
) -> float:
    """Largest K such that one step from (delta_o, delta_h) keeps
    delta_o' + delta_h' <= target_rise. Both terms are monotone increasing in
    K (ultimate values increase with K, and a difference-equation step is
    monotone in its ultimate target), so their sum is monotone increasing in
    K and bisection is exact up to the iteration budget below.
    """
    lo, hi = 0.0, _ENVELOPE_K_SEARCH_HI
    for _ in range(_ENVELOPE_BISECTION_ITERS):
        mid = (lo + hi) / 2.0
        new_o, new_h = _step_state(delta_o, delta_h, mid, dt_min, site)
        if (new_o + new_h) > target_rise:
            hi = mid
        else:
            lo = mid
    return lo


def thermal_envelope(
    site: SiteElectrical,
    ambient_c: pd.Series,
    *,
    prior_load_kw: pd.Series | None = None,
) -> pd.DataFrame:
    """t, max_kw, clipped -- the largest constant-over-interval load that
    keeps hotspot <= hotspot_limit_c given ambient and thermal history.

    Each row's max_kw is solved by one-step bisection (`_solve_k_for_target`)
    from the thermal state inherited from the previous row, so a caller that
    always dispatches exactly at max_kw sees the state advance exactly as if
    that load had really been carried -- "hold load at max_kw for the whole
    window" is then tight against hotspot_limit_c, not merely safe under it.

    `prior_load_kw`, if given, is a load history strictly before this
    window's first timestamp. It warms up the starting thermal state by
    replaying the difference equation over it. We are not given a separate
    ambient history for that period, so ambient is held at `ambient_c`'s
    first value throughout the warm-up -- a documented simplification, not a
    claim that ambient was actually constant historically.

    `clipped` is True where MAX_ENVELOPE_MULTIPLE_OF_NAMEPLATE truncated the
    physically-solved value. Recorded rather than silently applied, per the
    project's clip-visibility rule (contracts/src/grid.md; see the #9
    post-mortem this rule exists to avoid repeating) -- assert this stays
    rare/False in normal-range scenarios in any caller that cares.
    """
    idx = ambient_c.index
    dt = _dt_minutes(idx)

    delta_o, delta_h = 0.0, 0.0
    if prior_load_kw is not None and len(prior_load_kw) > 0:
        warm_ambient = pd.Series(float(ambient_c.iloc[0]), index=prior_load_kw.index)
        _, delta_o, delta_h = _simulate(prior_load_kw, warm_ambient, site, 0.0, 0.0)

    n = len(idx)
    max_kw = np.empty(n)
    clipped = np.empty(n, dtype=bool)
    ambient_vals = ambient_c.to_numpy(dtype=float)
    cap_kw = MAX_ENVELOPE_MULTIPLE_OF_NAMEPLATE * site.rated_kw

    for i in range(n):
        target_rise = site.hotspot_limit_c - ambient_vals[i]
        k = _solve_k_for_target(delta_o, delta_h, target_rise, float(dt[i]), site)
        solved_kw = k * site.rated_kw
        kw = min(solved_kw, cap_kw)
        kw = max(0.0, kw)
        clipped[i] = solved_kw > cap_kw + 1e-9
        max_kw[i] = kw
        k_actual = kw / site.rated_kw
        delta_o, delta_h = _step_state(delta_o, delta_h, k_actual, float(dt[i]), site)

    return pd.DataFrame({"t": idx, "max_kw": max_kw, "clipped": clipped})


# ---------------------------------------------------------------------------
# Per-phase allocation
# ---------------------------------------------------------------------------

def _validate_points_known(mapping: Mapping[str, float], site: SiteElectrical) -> None:
    unknown = sorted(p for p in mapping if p not in site.point_phase)
    if unknown:
        raise KeyError(f"point(s) {unknown} not wired in site.point_phase")


def phase_allocate(demand_kw: Mapping[str, float], site: SiteElectrical) -> dict[str, float]:
    """Per-point setpoints respecting each phase's own limit.

    The transformer's balanced per-phase electrical limit is
    `site.rated_kw / site.phases` (a static, per-phase-of-a-balanced-source
    figure -- distinct from the dynamic, whole-transformer thermal envelope
    computed by `thermal_envelope`). Demand is grouped by each point's fixed
    `site.point_phase` wiring -- NOT divided evenly across phases -- and any
    phase whose aggregate demand exceeds its limit is scaled down
    proportionally; other phases are untouched. Never allocates more than a
    point's own requested demand. Iterates in sorted-key order throughout so
    the result never depends on the input mapping's iteration order.
    """
    _validate_points_known(demand_kw, site)
    phase_limit_kw = site.rated_kw / site.phases

    ordered_points = sorted(demand_kw)
    totals: dict[int, float] = {}
    for point in ordered_points:
        ph = site.point_phase[point]
        totals[ph] = totals.get(ph, 0.0) + max(0.0, float(demand_kw[point]))

    scale = {
        ph: (1.0 if total <= phase_limit_kw or total <= 0.0 else phase_limit_kw / total)
        for ph, total in totals.items()
    }

    return {
        point: max(0.0, float(demand_kw[point])) * scale[site.point_phase[point]]
        for point in ordered_points
    }


def phase_imbalance(setpoints: Mapping[str, float], site: SiteElectrical) -> float:
    """kW, worst phase vs mean, across all `site.phases` phases (a phase with
    no point wired to it still counts as 0 kW in the mean)."""
    _validate_points_known(setpoints, site)
    totals = {p: 0.0 for p in range(1, site.phases + 1)}
    for point, kw in setpoints.items():
        totals[site.point_phase[point]] += float(kw)
    mean = sum(totals.values()) / len(totals)
    return max(abs(v - mean) for v in totals.values())


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def _require_columns(df: pd.DataFrame, cols: Sequence[str]) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"missing required column(s) {missing}; have {list(df.columns)}")


def envelope_violations(
    schedule: pd.DataFrame, envelopes: pd.DataFrame, *, tol_kw: float = 1e-6
) -> pd.DataFrame:
    """The audit function the scheduler is tested against and the UI shows.
    Empty frame = clean.

    `schedule` needs `t, load_kw` (+ `site_id` if multi-site); `envelopes`
    needs `t, max_kw` (+ `site_id` to match). Joined on whichever of those
    keys both frames carry; rows where load_kw exceeds max_kw by more than
    `tol_kw` are returned with an added `excess_kw` column.
    """
    _require_columns(schedule, ["t", "load_kw"])
    _require_columns(envelopes, ["t", "max_kw"])
    join_keys = ["t"]
    if "site_id" in schedule.columns and "site_id" in envelopes.columns:
        join_keys.append("site_id")

    merged = schedule.merge(envelopes, on=join_keys, how="inner", suffixes=("", "_env"))
    breach = merged["load_kw"] > (merged["max_kw"] + tol_kw)
    out = merged.loc[breach].copy()
    out["excess_kw"] = out["load_kw"] - out["max_kw"]
    return out.reset_index(drop=True)
