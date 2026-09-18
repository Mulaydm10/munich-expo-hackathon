"""src.sched — public API.

Deadline-feasible charging schedule inside the envelope, holding the sold floor.

This module is the lane's ONLY cross-lane surface: other lanes import
`src.sched.api` and nothing else. See `contracts/src/sched.md` for the
interface this lane owes the rest of the project, and
`contracts/CONVENTIONS.md` for units, time handling and the data layout.

Priority order (fixed, asserted in tests, never traded against the objective):
deadlines first, physics (the envelope) second, the sold floor third, price last.

## Input shapes this lane depends on (contracts, not imports)
`src/fleet` and `src/grid` are unmerged lanes; this module never imports them.
It is built only against their *contracts* (`contracts/src/fleet.md`,
`contracts/src/grid.md`), and the shapes below are how those contracts'
outputs are expected to combine into this lane's inputs:

- `sessions`: one row per charging session — `session_id, site_id, t_arrive,
  t_depart, energy_kwh, max_power_kw, deadline_t` (== `t_depart`), all tz-aware
  UTC per CONVENTIONS.md. (`src/fleet.synthesise_sessions` also emits `phase`
  and `profile`; this lane ignores columns it doesn't need.)
- `envelope`: `site_id, t, max_kw` — `src/grid.thermal_envelope` returns
  `t, max_kw` for one site; this lane needs the multi-site union, so the
  caller adds `site_id` when combining several sites' envelopes into one
  frame. ASSUMPTION (flagged): this `site_id` column is not shown in
  `contracts/src/sched.md`'s one-line `envelope: pd.DataFrame`, but is
  required to decompose `schedule()` per site as the contract's own
  "per-site LPs, not one giant program" note demands.
- `prices`: `t, price_eur_mwh` — ASSUMPTION (flagged): a single national/zonal
  price series applied to every site, since `contracts/src/market.md` prices
  a pool, not a per-site series, and `sched.md` does not spell out the
  columns either.

## Commitment / ReductionEvent apply per *call*, not per site
`Commitment` (defined below, exactly as shown in `contracts/src/sched.md`) and
`ReductionEvent` (see its docstring — not defined in any contract file) carry
no `site_id`. ASSUMPTION (flagged): both apply to every site present in the
`sessions`/`schedule` passed to that call. Call `schedule()`/`dispatch()` once
per site if a commitment or event is meant for only one of several sites
passed together.

## How `dispatch()` gets the inputs its signature doesn't carry
`contracts/src/sched.md` fixes `dispatch(schedule: pd.DataFrame, event:
ReductionEvent) -> pd.DataFrame` — no sessions/envelope/prices parameter, yet
recovering deferred energy safely needs all three. `schedule()` attaches them
to the returned frame's `.attrs` (a pandas DataFrame's own side-channel for
exactly this kind of provenance metadata — it changes nothing about the
documented columns `t, site_id, session_id, power_kw`, so the public surface
is not widened). `dispatch()` reads `schedule.attrs` for its working inputs.
Passing a `schedule` that was not produced by this module's `schedule()` is a
usage error and raises `ValueError` rather than guessing.

## NaN policy (deliberate, tested)
Nothing in this lane treats NaN as a neutral, "permissive" default — the
project's own history (`src/grid`'s bisection returning maximum permission on
a NaN because `NaN > x` is always False) is exactly the failure mode to avoid.
Every numeric input (`sessions[energy_kwh, max_power_kw]`, `envelope[max_kw]`,
`prices[price_eur_mwh]`, `schedule[power_kw]` when it is evaluate()'s input)
is checked for NaN up front and rejected with `ValueError` naming the column
and lane. A scheduler that silently treated an unknown envelope value as "no
limit" or "zero" would either overload a transformer or manufacture a fake
deadline miss — neither is this lane's call to make quietly.

Empty-input paths get the same treatment, not an exemption (issue #27 finding
6): a site whose LP has zero variables (no session window intersects the
envelope grid) is not "nothing to check" — a live floor commitment over that
site's times still cannot be met by zero power, so `_solve_lp`'s `n == 0`
branch checks the floor before ever reporting success.

## No numerical-clip coercion metric (issue #27 finding 9)
An earlier version of this module clipped each LP variable into its `[0,
max_power_kw]` bound whenever HiGHS returned a value outside it by less than
`1e-4` (raising past that), and counted how often the clip actually moved a
value as `.attrs['numerical_clip_count']`. That counter was observed at zero
on every fixture this lane has, which makes it unfalsified, not verified
(`contracts/CONVENTIONS.md`: a coercion must be pinned at both ends — a
fixture that forces it and one that avoids it). Forcing HiGHS off its own
bound just to give the counter something to report would be manufacturing a
defect to measure it, so the coercion is removed instead: a bounded HiGHS
result outside `[0, max_power_kw]` by more than solver noise is now always a
hard `RuntimeError` naming the session and interval, never a silent clip —
the band between `_TOL` (1e-6) and the old 1e-4 threshold was never
physically meaningful, only a margin for the clip to hide in. There is
nothing left to coerce, so nothing to measure: no returned frame carries
`numerical_clip_count` or `numerical_clip_total_vars` any more.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import linprog

LANE = "src/sched"

_TOL = 1e-6

# Demand charge applied to a site's daily peak. German DSO Netzentgelte bill a
# Leistungspreis on the annual peak (order 100-150 EUR/kW/a for >2500 h/a
# customers); spread over 365 days. Source: ASSUMED (range from published DSO
# price sheets), not a specific tariff.
DEMAND_CHARGE_EUR_PER_KW_DAY = 0.30

_SESSION_COLUMNS = [
    "session_id", "site_id", "t_arrive", "t_depart", "energy_kwh", "max_power_kw", "deadline_t",
]
_ENVELOPE_COLUMNS = ["site_id", "t", "max_kw"]
_PRICE_COLUMNS = ["t", "price_eur_mwh"]
_SCHEDULE_COLUMNS = ["t", "site_id", "session_id", "power_kw"]


# ---------------------------------------------------------------------------
# Contract-defined public types
# ---------------------------------------------------------------------------


class Infeasible(Exception):
    """Raised by `schedule()` (and, internally, by `dispatch()`'s re-solve) when the
    problem as posed cannot satisfy every hard constraint. Names the binding
    constraint (deadline / envelope / floor) in the message. `schedule()` never
    catches this to fall back to a partial result: contracts/src/sched.md is explicit
    that a silent under-delivery is worse than an honest refusal, because the
    shortfall would otherwise surface as a broken flexibility promise in src/market,
    not as an error here.
    """


@dataclass(frozen=True)
class Commitment:
    """A reduction floor sold to the grid: total site load must be >= `reduction_kw`
    for every interval in `[t_start, t_end)` at every site being scheduled in this
    call (see the module docstring's "apply per call" note)."""

    t_start: datetime
    t_end: datetime
    reduction_kw: float


@dataclass(frozen=True)
class ReductionEvent:
    """GUESS: this type is named in `contracts/src/sched.md`'s `dispatch` signature and
    in `contracts/src/voice.md` / `contracts/src/service.md`, but its fields are not
    specified anywhere in `contracts/`. Shape chosen to match sched.md's prose
    verbatim: "Grid operator calls mid-window: pause/curtail points to deliver
    `event.reduction_kw` within the notice period, then recover the deferred energy
    before every deadline."

    call_t: when the grid operator places the call.
    notice_min: minutes of notice before compliance is required (compliance window
        starts at `call_t + notice_min`).
    duration_min: how long the reduction must be sustained.
    reduction_kw: requested reduction in total site load vs. the committed schedule.
    """

    call_t: datetime
    notice_min: float
    duration_min: float
    reduction_kw: float


# ---------------------------------------------------------------------------
# Validation helpers (private)
# ---------------------------------------------------------------------------


def _require_columns(df: pd.DataFrame, columns: Sequence[str], name: str) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"src.sched: `{name}` is missing required column(s) {missing}")


def _reject_nan(df: pd.DataFrame, columns: Sequence[str], name: str) -> None:
    """NaN is never treated as a neutral default in this lane -- see module docstring.
    Refuses loudly rather than guessing 0 or "unlimited" for a missing value."""
    for col in columns:
        bad = df[col].isna()
        if bad.any():
            n = int(bad.sum())
            raise ValueError(
                f"src.sched: `{name}.{col}` has {n} NaN value(s); refusing to guess whether "
                f"that means 0, unlimited, or something else. Fix the input."
            )


def _as_utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True)


def _reject_nan_commitments(commitments: Sequence[Commitment]) -> None:
    """NaN is never neutral here either: a `Commitment.reduction_kw` of NaN must not be
    silently treated as "no floor" (0) or "unlimited". See module docstring."""
    for c in commitments:
        if isinstance(c.reduction_kw, float) and np.isnan(c.reduction_kw):
            raise ValueError(
                "src.sched: a Commitment.reduction_kw is NaN; refusing to guess whether "
                "that means no floor or an unlimited one."
            )


def _infer_interval_hours(t: pd.Series) -> pd.Series:
    """Interval length in hours for each timestamp, inferred from consecutive gaps in a
    sorted `t` (never hard-coded /4 -- CONVENTIONS.md). The last interval repeats the
    previous gap. A single-timestamp series falls back to the native 15-minute
    resolution documented in CONVENTIONS.md.
    GUESS: that single-row fallback (0.25 h) -- there is no second timestamp to infer a
    gap from; flagged since it is a genuine guess, not a computed value.
    Returned Series is indexed by the (sorted) timestamp values themselves, so callers
    look up `hours.loc[t]`.
    """
    # NOTE: index with `t_sorted` (or a DatetimeIndex built from it), never `.values` --
    # `.values` on a tz-aware Series silently drops the tzinfo, which then makes every
    # later `.loc[<tz-aware Timestamp>]` lookup raise a tz-naive/tz-aware KeyError.
    t_sorted = pd.Series(_as_utc(pd.Series(t))).sort_values().reset_index(drop=True)
    idx = pd.DatetimeIndex(t_sorted)
    n = len(t_sorted)
    if n == 0:
        return pd.Series([], dtype=float)
    if n == 1:
        return pd.Series([0.25], index=idx)  # GUESS: see docstring
    gaps = t_sorted.shift(-1) - t_sorted
    hours = gaps.dt.total_seconds().to_numpy() / 3600.0
    hours[-1] = hours[-2]  # last interval repeats the previous gap
    return pd.Series(hours, index=idx)


# ---------------------------------------------------------------------------
# Per-site problem assembly (private)
# ---------------------------------------------------------------------------


class _SiteProblem:
    """Everything needed to solve (or diagnose) one site's LP/greedy problem."""

    def __init__(
        self,
        site_id: str,
        sessions: pd.DataFrame,
        envelope: pd.DataFrame,
        prices: pd.DataFrame,
        commitments: Sequence[Commitment],
    ) -> None:
        self.site_id = site_id
        self.sessions = sessions.reset_index(drop=True)
        env = envelope.sort_values("t").drop_duplicates(subset="t")
        self.times = list(_as_utc(env["t"]))
        self.max_kw = dict(zip(_as_utc(env["t"]), env["max_kw"].astype(float)))
        self.dt = _infer_interval_hours(env["t"])
        pr = prices.sort_values("t")
        price_t = _as_utc(pr["t"])
        self.price = dict(zip(price_t, pr["price_eur_mwh"].astype(float)))
        self.commitments = list(commitments)

        # per-session window: sorted times within [t_arrive, t_depart) that also
        # appear in this site's envelope grid.
        self.windows: dict[str, list[pd.Timestamp]] = {}
        self.max_power: dict[str, float] = {}
        self.energy_due: dict[str, float] = {}
        for row in self.sessions.itertuples(index=False):
            sid = row.session_id
            arrive = pd.Timestamp(row.t_arrive)
            depart = pd.Timestamp(row.t_depart)
            window = [t for t in self.times if arrive <= t < depart]
            self.windows[sid] = window
            self.max_power[sid] = float(row.max_power_kw)
            self.energy_due[sid] = float(row.energy_kwh)

    def floor_at(self, t: pd.Timestamp) -> float:
        req = 0.0
        for c in self.commitments:
            if c.t_start <= t < c.t_end:
                req = max(req, c.reduction_kw)
        return req

    def check_deadline_feasibility(self) -> None:
        """Necessary condition, ignoring envelope sharing and the floor: could this
        session reach its energy_kwh_due even if it had the *whole* site to itself?
        Cheap, and lets us name "deadline" specifically before ever building an LP.
        Also does a cheap, upfront floor-vs-envelope physical conflict check: a
        commitment that asks for more than the envelope allows is never satisfiable
        regardless of sessions.
        """
        for sid, window in self.windows.items():
            due = self.energy_due[sid]
            if due <= _TOL:
                continue
            available = sum(self.max_power[sid] * float(self.dt.loc[t]) for t in window)
            if available < due - _TOL:
                raise Infeasible(
                    f"src.sched: deadline infeasible for session {sid!r} at site "
                    f"{self.site_id!r}: needs {due:.4f} kWh but can draw at most "
                    f"{available:.4f} kWh in its own arrival-departure window, even at "
                    f"full max_power_kw and with the whole site's envelope to itself."
                )
        for c in self.commitments:
            for t in self.times:
                if c.t_start <= t < c.t_end and c.reduction_kw > self.max_kw[t] + _TOL:
                    raise Infeasible(
                        f"src.sched: floor infeasible at site {self.site_id!r}, t={t}: "
                        f"committed reduction_kw={c.reduction_kw:.4f} exceeds the "
                        f"envelope's max_kw={self.max_kw[t]:.4f} -- physically impossible "
                        f"regardless of sessions."
                    )


# ---------------------------------------------------------------------------
# LP solver (private)
# ---------------------------------------------------------------------------


def _solve_lp(
    problem: _SiteProblem,
    *,
    include_envelope: bool = True,
    include_floor: bool = True,
    peak_price_eur_per_kw: float = 0.0,
) -> pd.DataFrame | None:
    """Returns a dense (session, t) power dataframe for this site, or None if
    infeasible/unbounded/numerically failed. Deterministic: fixed variable order,
    method='highs'."""
    var_index: dict[tuple[str, pd.Timestamp], int] = {}
    for sid in sorted(problem.windows):
        for t in problem.windows[sid]:
            var_index[(sid, t)] = len(var_index)
    n = len(var_index)
    if n == 0:
        # No session window intersects this site's envelope grid, so there is zero
        # power available to draw on -- that is only actually feasible if nothing here
        # requires positive load. A live floor commitment does (see issue #27 finding
        # 6 / the module docstring's empty-input note): with `include_floor=True` and
        # any interval demanding reduction_kw > 0, this is infeasible and must say so
        # by returning None, not by reporting success while holding none of the floor.
        # `include_floor=False` callers are explicitly asking "ignoring the floor,
        # would this be feasible?" -- with no variables at all the envelope can never
        # be exceeded either, so that question is trivially yes.
        if include_floor and any(problem.floor_at(t) > _TOL for t in problem.times):
            return None
        return pd.DataFrame(columns=["t", "session_id", "power_kw"])

    use_peak_term = peak_price_eur_per_kw > 0.0
    peak_index = n if use_peak_term else None
    n_variables = n + int(use_peak_term)
    c = np.zeros(n_variables)
    bounds = [(0.0, 0.0)] * n
    for (sid, t), i in var_index.items():
        dt = float(problem.dt.loc[t])
        c[i] = problem.price[t] * dt / 1000.0  # EUR per kW of this variable
        bounds[i] = (0.0, problem.max_power[sid])
    if use_peak_term:
        c[peak_index] = peak_price_eur_per_kw
        bounds.append((0.0, None))

    # equality: total delivered energy == energy_kwh_due, per session.
    # (Tightened from the contract's ">=" to "==": an EV cannot usefully absorb more
    # energy than it needs, so an inequality would let a negative price make
    # "overcharging" spuriously "profitable" in the LP with no physical meaning.
    # Documented design decision, not a contract violation -- see report.)
    session_order = sorted(problem.windows)
    A_eq = np.zeros((len(session_order), n_variables))
    b_eq = np.zeros(len(session_order))
    for r, sid in enumerate(session_order):
        b_eq[r] = problem.energy_due[sid]
        for t in problem.windows[sid]:
            A_eq[r, var_index[(sid, t)]] = float(problem.dt.loc[t])

    A_ub_rows: list[np.ndarray] = []
    b_ub: list[float] = []

    if include_envelope:
        for t in problem.times:
            row = np.zeros(n_variables)
            any_var = False
            for sid in problem.windows:
                key = (sid, t)
                if key in var_index:
                    row[var_index[key]] = 1.0
                    any_var = True
            if any_var:
                A_ub_rows.append(row)
                b_ub.append(problem.max_kw[t])

    if use_peak_term:
        for t in problem.times:
            row = np.zeros(n_variables)
            any_var = False
            for sid in problem.windows:
                key = (sid, t)
                if key in var_index:
                    row[var_index[key]] = 1.0
                    any_var = True
            if any_var:
                row[peak_index] = -1.0
                A_ub_rows.append(row)
                b_ub.append(0.0)

    if include_floor:
        for t in problem.times:
            req = problem.floor_at(t)
            if req <= 0:
                continue
            row = np.zeros(n_variables)
            for sid in problem.windows:
                key = (sid, t)
                if key in var_index:
                    row[var_index[key]] = -1.0
            A_ub_rows.append(row)
            b_ub.append(-req)

    A_ub = np.array(A_ub_rows) if A_ub_rows else None
    b_ub_arr = np.array(b_ub) if b_ub else None

    res = linprog(c, A_ub=A_ub, b_ub=b_ub_arr, A_eq=A_eq, b_eq=b_eq,
                   bounds=bounds, method="highs")
    if not res.success:
        return None

    records = []
    for (sid, t), i in var_index.items():
        raw = float(res.x[i])
        lo, hi = 0.0, problem.max_power[sid]
        if raw < lo - 1e-4 or raw > hi + 1e-4:
            # a real violation, not solver noise -- never coerced away (see the module
            # docstring's "no numerical-clip coercion metric" note / issue #27 finding 9).
            raise RuntimeError(
                f"src.sched: LP returned power_kw={raw:.6f} for session {sid!r} at t={t}, "
                f"outside its [0, {hi}] bound by more than solver noise -- solver bug or "
                f"numerical failure, not something to clip past."
            )
        records.append((t, sid, raw))
    return pd.DataFrame(records, columns=["t", "session_id", "power_kw"])


def _solve_site_lp(problem: _SiteProblem, *, peak_price_eur_per_kw: float = 0.0) -> pd.DataFrame:
    problem.check_deadline_feasibility()  # names "deadline" first, per priority order
    full = _solve_lp(problem, peak_price_eur_per_kw=peak_price_eur_per_kw)
    if full is not None:
        return full
    # diagnose: physics (envelope) ranks above the sold floor, so check whether
    # dropping the floor alone would fix it -- if so, the floor is what's binding.
    without_floor = _solve_lp(
        problem, include_floor=False, peak_price_eur_per_kw=peak_price_eur_per_kw
    )
    if without_floor is not None:
        raise Infeasible(
            f"src.sched: floor infeasible at site {problem.site_id!r}: dropping the "
            f"commitment floor alone makes the problem feasible again, so the sold "
            f"reduction floor is the binding constraint here."
        )
    raise Infeasible(
        f"src.sched: envelope infeasible at site {problem.site_id!r}: even with the "
        f"commitment floor dropped, the site's envelope cannot carry every session's "
        f"deadline-feasible charging demand."
    )


# ---------------------------------------------------------------------------
# Greedy solver (private) -- least-slack-first, with mandatory floor fill and a
# cheap-price preference for optional (non-critical) allocation.
# ---------------------------------------------------------------------------


def _solve_site_greedy(
    problem: _SiteProblem, *, peak_price_eur_per_kw: float = 0.0
) -> pd.DataFrame:
    """Three ordered full-horizon passes over `problem.times`, in the contract's own
    priority order (deadlines first... but the floor has to claim its energy BEFORE
    anything optional does, or a later floor window can find every session already
    drained by an eager earlier price-driven fill -- see the bug note below).

    Priority order note: although the contract ranks "deadlines first, physics
    second, floor third", a single interleaved chronological pass that fills
    optional (price-driven) demand *before* a later floor window is reached will
    starve the floor even when a feasible schedule exists -- greedy has no lookahead,
    so cheap-early-hours sessions can fully deliver and vanish from "active" long
    before a floor window later in the same horizon needs them. Fixed by giving the
    floor first claim on session capacity, in its own dedicated chronological pass,
    before deadline-criticality and optional pricing get to spend anything. This
    does not change priority order for what's ALLOWED to violate what (deadlines
    still can never be sacrificed for the floor -- phase 2 below still raises
    Infeasible naming envelope/deadline before phase 3 ever runs), only the order in
    which capacity is *reserved* so a real feasible answer isn't missed.
    Greedy is price/deadline only; the demand-charge peak term is an LP-only objective.
    """
    problem.check_deadline_feasibility()

    remaining_energy = dict(problem.energy_due)
    assigned: dict[tuple[str, pd.Timestamp], float] = {}
    cap_used: dict[pd.Timestamp, float] = {t: 0.0 for t in problem.times}
    window_pos = {
        sid: {t: i for i, t in enumerate(window)} for sid, window in problem.windows.items()
    }
    session_ids = sorted(problem.windows)

    def slack(sid: str, t: pd.Timestamp) -> float:
        window = problem.windows[sid]
        idx = window_pos[sid][t]
        remaining_slots = len(window) - idx
        e = remaining_energy[sid]
        mp = problem.max_power[sid]
        acc = 0.0
        used = 0
        for tt in window[idx:]:
            if acc >= e - _TOL:
                break
            acc += mp * float(problem.dt.loc[tt])
            used += 1
        return remaining_slots - used

    def room(sid: str, t: pd.Timestamp, dt: float) -> float:
        already = assigned.get((sid, t), 0.0)
        return min(problem.max_power[sid] - already, remaining_energy[sid] / dt)

    def take_for(sid: str, t: pd.Timestamp, dt: float, amount: float) -> None:
        assigned[(sid, t)] = assigned.get((sid, t), 0.0) + amount
        cap_used[t] += amount
        remaining_energy[sid] -= amount * dt

    def active_at(t: pd.Timestamp) -> list[str]:
        return [sid for sid in session_ids
                if t in window_pos[sid] and remaining_energy[sid] > _TOL]

    # ---- phase 1: floor gets first claim on session capacity, site-wide, before any
    # deadline-critical or optional-price allocation happens anywhere in the horizon.
    # Drawn from the sessions with the MOST slack first, so tight sessions keep their
    # scarce capacity free for their own deadlines in phase 2.
    for t in problem.times:
        floor_req = problem.floor_at(t)
        if floor_req <= 0:
            continue
        dt = float(problem.dt.loc[t])
        cap_left = problem.max_kw[t] - cap_used[t]
        by_slack_desc = sorted(active_at(t), key=lambda sid: (-slack(sid, t), sid))
        shortfall = floor_req
        for sid in by_slack_desc:
            if shortfall <= _TOL:
                break
            r = room(sid, t, dt)
            if r <= _TOL:
                continue
            take = min(r, shortfall, cap_left)
            if take <= 0:
                continue
            take_for(sid, t, dt, take)
            cap_left -= take
            shortfall -= take
        if shortfall > _TOL:
            raise Infeasible(
                f"src.sched: floor infeasible at site {problem.site_id!r}, t={t}: "
                f"active sessions cannot collectively draw the committed "
                f"reduction_kw even using all remaining envelope/session headroom "
                f"(short by {shortfall:.4f} kW)."
            )

    # ---- phase 2: a single chronological sweep doing, at each t, (a) deadline-critical
    # sessions (slack <= 0) mandatorily, then (b) optional price-aware top-up for
    # sessions that still have slack, IF this interval is cheap enough for them.
    # This has to be ONE combined per-t sweep, not two separate full-horizon passes:
    # if the optional pass ran as its own later full sweep, every session would
    # already have been fully drained by the mandatory pass alone (criticality is
    # re-evaluated live and always eventually forces full delivery by each session's
    # own deadline), leaving the optional pass nothing to do and greedy would stop
    # being price-aware at all. Interleaving per-t lets a session opportunistically
    # grab a cheap early interval *before* it would otherwise become critical.
    price_threshold: dict[str, float] = {}
    for sid, window in problem.windows.items():
        price_threshold[sid] = float(np.median([problem.price[t] for t in window])) if window \
            else float("inf")

    for t in problem.times:
        dt = float(problem.dt.loc[t])

        # (a) mandatory: zero-slack sessions must charge at their full remaining rate.
        for sid in sorted(active_at(t), key=lambda sid: (slack(sid, t), sid)):
            if slack(sid, t) > 0:
                continue
            need_kw = room(sid, t, dt)
            if need_kw <= _TOL:
                continue
            cap_left = problem.max_kw[t] - cap_used[t]
            if need_kw > cap_left + _TOL:
                raise Infeasible(
                    f"src.sched: envelope infeasible at site {problem.site_id!r}, t={t}: "
                    f"session {sid!r} has zero slack left and needs {need_kw:.4f} kW to "
                    f"still reach its deadline, but only {cap_left:.4f} kW of envelope "
                    f"headroom remains this interval."
                )
            take_for(sid, t, dt, need_kw)

        # (b) optional: sessions with slack left may top up now if the price is cheap
        # enough for them specifically (below their own window's median).
        for sid in sorted(active_at(t), key=lambda sid: (slack(sid, t), sid)):
            cap_left = problem.max_kw[t] - cap_used[t]
            if cap_left <= _TOL:
                continue
            if problem.price[t] > price_threshold[sid] + _TOL:
                continue  # defer -- this session has slack and this slot isn't cheap for it
            r = room(sid, t, dt)
            if r <= _TOL:
                continue
            take_for(sid, t, dt, min(r, cap_left))

    # feasibility of what's left is guaranteed by check_deadline_feasibility() plus
    # phase 1 and phase 2(a) above having already raised on any floor/envelope
    # conflict; anything still unmet here would mean remaining_energy > 0 with no
    # more window left, which cannot happen once phase 2(a) has cleared every t up to
    # each session's own deadline.

    # dense output: every (session, t) in-window gets a row, 0 where unassigned.
    dense = []
    for sid, window in problem.windows.items():
        for t in window:
            dense.append((t, sid, assigned.get((sid, t), 0.0)))
    return pd.DataFrame(dense, columns=["t", "session_id", "power_kw"])


# ---------------------------------------------------------------------------
# Shared multi-site driver
# ---------------------------------------------------------------------------


def _solve_all_sites(
    sessions: pd.DataFrame,
    envelope: pd.DataFrame,
    prices: pd.DataFrame,
    commitments: Sequence[Commitment],
    solver: Literal["lp", "greedy"],
    peak_price_eur_per_kw: float = 0.0,
) -> pd.DataFrame:
    # issue #27 finding 7: `Literal["lp", "greedy"]` is a type-checker hint, not a
    # runtime check -- an unrecognised string (a capitalisation typo, say) must not
    # silently fall through to greedy and produce a valid-looking schedule from the
    # wrong solver.
    if solver not in ("lp", "greedy"):
        raise ValueError(
            f"src.sched: unknown solver {solver!r}; expected 'lp' or 'greedy'"
        )
    solve_fn = _solve_site_lp if solver == "lp" else _solve_site_greedy
    frames = []
    for site_id in sorted(sessions["site_id"].unique()):
        site_sessions = sessions[sessions["site_id"] == site_id]
        site_envelope = envelope[envelope["site_id"] == site_id]
        if site_envelope.empty:
            raise ValueError(f"src.sched: no envelope rows for site_id={site_id!r}")
        problem = _SiteProblem(site_id, site_sessions, site_envelope, prices, commitments)
        site_df = solve_fn(problem, peak_price_eur_per_kw=peak_price_eur_per_kw)
        site_df.insert(1, "site_id", site_id)
        frames.append(site_df)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=_SCHEDULE_COLUMNS)
    return out[_SCHEDULE_COLUMNS].sort_values(["site_id", "t", "session_id"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def schedule(
    sessions: pd.DataFrame,
    envelope: pd.DataFrame,
    prices: pd.DataFrame,
    *,
    commitments: Sequence[Commitment] = (),
    solver: Literal["lp", "greedy"] = "lp",
    peak_price_eur_per_kw: float = 0.0,
) -> pd.DataFrame:
    """t, site_id, session_id, power_kw. LP (or greedy) over 15-min intervals, decomposed
    per site (see contracts/src/sched.md). Dense per session: one row for every interval
    in that session's [t_arrive, t_depart) that also appears in its site's envelope
    grid, whether or not power was assigned there (power_kw may be 0).

    Raises `Infeasible` naming the binding constraint (deadline / envelope / floor)
    rather than ever returning a partial/best-effort schedule. See module docstring
    for the NaN policy and the site_id/prices/Commitment shape assumptions.
    """
    _require_columns(sessions, _SESSION_COLUMNS, "sessions")
    _require_columns(envelope, _ENVELOPE_COLUMNS, "envelope")
    _require_columns(prices, _PRICE_COLUMNS, "prices")
    _reject_nan(sessions, ["energy_kwh", "max_power_kw"], "sessions")
    _reject_nan(envelope, ["max_kw"], "envelope")
    _reject_nan(prices, ["price_eur_mwh"], "prices")
    _reject_nan_commitments(commitments)
    if not np.isfinite(peak_price_eur_per_kw) or peak_price_eur_per_kw < 0:
        raise ValueError(
            f"{LANE}: peak_price_eur_per_kw must be finite and >= 0, "
            f"got {peak_price_eur_per_kw!r}"
        )
    if solver not in ("lp", "greedy"):
        raise ValueError(
            f"src.sched: unknown solver {solver!r}; expected 'lp' or 'greedy'"
        )
    if sessions.empty:
        out = pd.DataFrame(columns=_SCHEDULE_COLUMNS)
        out.attrs.update(sessions=sessions, envelope=envelope, prices=prices,
                         commitments=tuple(commitments), solver=solver,
                         peak_price_eur_per_kw=float(peak_price_eur_per_kw))
        return out

    out = _solve_all_sites(
        sessions,
        envelope,
        prices,
        commitments,
        solver,
        peak_price_eur_per_kw=peak_price_eur_per_kw,
    )
    out.attrs["sessions"] = sessions
    out.attrs["envelope"] = envelope
    out.attrs["prices"] = prices
    out.attrs["commitments"] = tuple(commitments)
    out.attrs["solver"] = solver
    out.attrs["peak_price_eur_per_kw"] = float(peak_price_eur_per_kw)
    return out


def _floor_shortfall_kw_min(
    envelope: pd.DataFrame, commitments: Sequence[Commitment], totals: pd.Series
) -> float:
    """kW*min of floor shortfall, evaluated over every (site_id, t) the envelope
    defines -- not just the ones present in the schedule (issue #27 finding 2). A
    (site, t) with a live commitment but no matching schedule row delivered zero, not
    "no obligation": `totals` (a Series indexed by (site_id, t) -> total_kw, built by
    the caller from whatever the schedule actually has, possibly empty) is looked up
    with a 0.0 default rather than skipped.
    """
    if not commitments or envelope.empty:
        return 0.0
    env = envelope.copy()
    env["t"] = _as_utc(env["t"])
    site_dt: dict[str, pd.Series] = {}
    for site_id, grp in env.groupby("site_id"):
        site_dt[site_id] = _infer_interval_hours(grp["t"])
    shortfall = 0.0
    for row in env.itertuples(index=False):
        applicable = [c.reduction_kw for c in commitments if c.t_start <= row.t < c.t_end]
        if not applicable:
            continue
        req = max(applicable)
        total_kw = float(totals.get((row.site_id, row.t), 0.0))
        dt_h = float(site_dt[row.site_id].loc[row.t])
        shortfall += max(0.0, req - total_kw) * dt_h * 60.0
    return shortfall


def evaluate(
    schedule: pd.DataFrame,
    sessions: pd.DataFrame,
    envelope: pd.DataFrame,
    prices: pd.DataFrame,
    commitments: Sequence[Commitment],
) -> dict:
    """{energy_cost_eur, unmet_kwh, deadline_misses, envelope_violation_kwh,
    floor_shortfall_kw_min, peak_kw}. Recomputes everything directly from the raw
    `schedule`/`sessions`/`envelope`/`prices`/`commitments` arguments -- it never reads
    `schedule.attrs` -- so it audits honestly even a hand-built or deliberately-broken
    schedule, and a passing test here is not vacuous just because `schedule()` also
    enforces the same constraints internally.
    """
    _require_columns(schedule, _SCHEDULE_COLUMNS, "schedule")
    _require_columns(sessions, _SESSION_COLUMNS, "sessions")
    _require_columns(envelope, _ENVELOPE_COLUMNS, "envelope")
    _require_columns(prices, _PRICE_COLUMNS, "prices")
    _reject_nan(schedule, ["power_kw"], "schedule")
    _reject_nan(sessions, ["energy_kwh", "max_power_kw"], "sessions")
    _reject_nan(envelope, ["max_kw"], "envelope")
    _reject_nan(prices, ["price_eur_mwh"], "prices")
    _reject_nan_commitments(commitments)

    sched = schedule.copy()
    sched["t"] = _as_utc(sched["t"])

    if sched.empty:
        due_total = float(sessions["energy_kwh"].sum())
        # issue #27 finding 2: an empty schedule delivers zero everywhere, which is a
        # full floor shortfall wherever a commitment is live -- not the "nothing to
        # audit" that a hard-coded 0.0 here used to report.
        empty_totals = pd.Series(dtype=float)
        return {
            "energy_cost_eur": 0.0,
            "unmet_kwh": due_total,
            "deadline_misses": int((sessions["energy_kwh"] > _TOL).sum()),
            "envelope_violation_kwh": 0.0,
            "floor_shortfall_kw_min": _floor_shortfall_kw_min(envelope, commitments, empty_totals),
            "peak_kw": 0.0,
        }

    site_dt: dict[str, pd.Series] = {}
    for site_id, grp in envelope.groupby("site_id"):
        site_dt[site_id] = _infer_interval_hours(grp["t"])

    def dt_lookup(site_id: str, t: pd.Timestamp):
        s = site_dt.get(site_id)
        if s is None or t not in s.index:
            return None
        return float(s.loc[t])

    dt_vals = [dt_lookup(sid, t) for sid, t in zip(sched["site_id"], sched["t"])]
    missing_mask = [v is None for v in dt_vals]
    if any(missing_mask):
        missing = sched.loc[missing_mask, ["site_id", "t"]].drop_duplicates()
        raise ValueError(
            f"src.sched.evaluate: schedule has (site_id, t) rows absent from `envelope`, "
            f"so their interval length is unknown: {missing.head(5).to_dict('records')}"
        )
    sched["dt_h"] = dt_vals
    sched["row_energy_kwh"] = sched["power_kw"] * sched["dt_h"]

    # issue #27 finding 1: only energy delivered at the right site, within the
    # session's own [t_arrive, deadline_t) window, counts toward meeting its deadline.
    # `delivered = sched.groupby("session_id")[...]` used to join on session_id alone,
    # so a hand-built schedule crediting a session's energy at the wrong site, before
    # it arrived, or after its deadline read as a met deadline. Invalid rows are
    # excluded here (surfaced as unmet energy / a deadline miss), never silently
    # banked -- this is the check that would have caught findings 3-5 of #27 on its
    # own, which is why `evaluate()` is fixed before any of dispatch()'s reporting.
    sess = sessions.set_index("session_id")
    sess_site = sess["site_id"]
    sess_arrive = _as_utc(sess["t_arrive"])
    sess_deadline = _as_utc(sess["deadline_t"])
    row_site = sched["session_id"].map(sess_site)
    row_arrive = sched["session_id"].map(sess_arrive)
    row_deadline = sched["session_id"].map(sess_deadline)
    valid_row = (
        sched["session_id"].isin(sess.index)
        & (sched["site_id"] == row_site)
        & (sched["t"] >= row_arrive)
        & (sched["t"] < row_deadline)
    )
    sched["valid_row_energy_kwh"] = np.where(valid_row, sched["row_energy_kwh"], 0.0)

    delivered = sched.groupby("session_id")["valid_row_energy_kwh"].sum()
    due = sessions.set_index("session_id")["energy_kwh"]
    unmet_per_session = (due - delivered.reindex(due.index).fillna(0.0)).clip(lower=0.0)
    unmet_kwh = float(unmet_per_session.sum())
    deadline_misses = int((unmet_per_session > _TOL).sum())

    site_totals = (
        sched.groupby(["site_id", "t"])["power_kw"].sum().rename("total_kw").reset_index()
    )
    env = envelope.copy()
    env["t"] = _as_utc(env["t"])
    merged = site_totals.merge(env[["site_id", "t", "max_kw"]], on=["site_id", "t"], how="left")
    if merged["max_kw"].isna().any():
        missing = merged.loc[merged["max_kw"].isna(), ["site_id", "t"]]
        raise ValueError(
            f"src.sched.evaluate: schedule uses (site_id, t) not present in `envelope`: "
            f"{missing.head(5).to_dict('records')}"
        )
    merged["dt_h"] = [dt_lookup(sid, t) for sid, t in zip(merged["site_id"], merged["t"])]
    over = (merged["total_kw"] - merged["max_kw"]).clip(lower=0.0)
    envelope_violation_kwh = float((over * merged["dt_h"]).sum())

    # issue #27 finding 2: build the audit grid from the envelope (every site x
    # committed timestamp), not from `merged` (only (site, t) pairs the schedule
    # actually used) -- a site/interval the schedule is silent on delivered zero, which
    # is a real shortfall against a live floor, not "no obligation".
    totals_by_site_t = site_totals.set_index(["site_id", "t"])["total_kw"]
    floor_shortfall_kw_min = _floor_shortfall_kw_min(envelope, commitments, totals_by_site_t)

    pr = prices.copy()
    pr["t"] = _as_utc(pr["t"])
    priced = sched.merge(pr[["t", "price_eur_mwh"]], on="t", how="left")
    if priced["price_eur_mwh"].isna().any():
        missing_t = priced.loc[priced["price_eur_mwh"].isna(), "t"].drop_duplicates()
        raise ValueError(f"src.sched.evaluate: no price for t in {list(missing_t)[:5]}")
    energy_cost_eur = float(
        (priced["power_kw"] * priced["dt_h"] * priced["price_eur_mwh"] / 1000.0).sum()
    )

    peak_kw = float(site_totals["total_kw"].max()) if len(site_totals) else 0.0

    return {
        "energy_cost_eur": energy_cost_eur,
        "unmet_kwh": unmet_kwh,
        "deadline_misses": deadline_misses,
        "envelope_violation_kwh": envelope_violation_kwh,
        "floor_shortfall_kw_min": floor_shortfall_kw_min,
        "peak_kw": peak_kw,
    }


def baseline(sessions: pd.DataFrame, *, policy: Literal["asap", "even"] = "asap") -> pd.DataFrame:
    """Uncontrolled charging, same shape as `schedule` -- every claim in this lane's
    tests is stated against this. `policy="asap"`: max power from arrival until either
    the session is full or it departs. `policy="even"`: constant rate = energy/dwell,
    capped at max_power_kw.

    GUESS: this function's contract signature takes only `sessions` -- no envelope or
    time-grid input -- so there is no external interval index to align to. Each
    session gets its own synthetic 15-min grid (CONVENTIONS.md's native resolution)
    from `t_arrive` to `t_depart`. If real per-site envelope/grid alignment is needed
    downstream, resample; the energy totals are what every claim is checked against.

    Deliberately does NOT repeat this project's clip-and-hide anti-pattern
    (src/fleet clips 39% of sessions unrecorded): whenever a session cannot receive its
    full `energy_kwh` in its own window at `policy`'s rate, the shortfall is recorded
    per-session in `result.attrs['unmet_kwh_by_session']` and totalled in
    `result.attrs['unmet_kwh_total']` / `result.attrs['unmet_session_fraction']`,
    never silently dropped.
    """
    _require_columns(sessions, _SESSION_COLUMNS, "sessions")
    _reject_nan(sessions, ["energy_kwh", "max_power_kw"], "sessions")

    rows: list[tuple] = []
    unmet: dict[str, float] = {}

    for row in sessions.itertuples(index=False):
        arrive = pd.Timestamp(row.t_arrive)
        depart = pd.Timestamp(row.t_depart)
        due = float(row.energy_kwh)
        mp = float(row.max_power_kw)
        dt = 0.25  # the resolution of the synthetic grid this function itself builds
        times = pd.date_range(arrive, depart, freq="15min", inclusive="left")
        if len(times) == 0:
            if due > _TOL:
                unmet[row.session_id] = due
            continue
        if policy == "asap":
            remaining = due
            for t in times:
                p = min(mp, remaining / dt) if remaining > _TOL else 0.0
                rows.append((t, row.site_id, row.session_id, p))
                remaining -= p * dt
            if remaining > _TOL:
                unmet[row.session_id] = remaining
        elif policy == "even":
            dwell_h = (depart - arrive).total_seconds() / 3600.0
            rate = min(mp, due / dwell_h) if dwell_h > 0 else 0.0
            delivered = 0.0
            for t in times:
                rows.append((t, row.site_id, row.session_id, rate))
                delivered += rate * dt
            shortfall = due - delivered
            if shortfall > _TOL:
                unmet[row.session_id] = shortfall
        else:
            raise ValueError(f"src.sched.baseline: unknown policy {policy!r}")

    df = pd.DataFrame(rows, columns=_SCHEDULE_COLUMNS)
    df.attrs["policy"] = policy
    df.attrs["unmet_kwh_by_session"] = unmet
    df.attrs["unmet_kwh_total"] = float(sum(unmet.values()))
    df.attrs["unmet_session_fraction"] = (len(unmet) / len(sessions)) if len(sessions) else 0.0
    return df


def _release_floor_over_window(
    commitments: Sequence[Commitment], start: pd.Timestamp, end: pd.Timestamp
) -> tuple[Commitment, ...]:
    """`commitments` with `[start, end)` carved out of every one of them (issue #27
    finding 1). The floor exists to guarantee load is *present* so it can be dropped
    when the grid operator calls; once the call arrives, the promise is being
    *delivered* over that window, not held, so enforcing it there as well as the
    temporarily tightened envelope just makes the two constraints fight -- and the
    floor always wins, since `dispatch()` never sacrifices a hard constraint. A
    commitment that straddles a boundary becomes up to two pieces with the same
    `reduction_kw`; one that falls entirely inside `[start, end)` disappears; one
    entirely outside it is untouched.
    """
    released: list[Commitment] = []
    for c in commitments:
        if c.t_end <= start or c.t_start >= end:
            released.append(c)
            continue
        if c.t_start < start:
            released.append(Commitment(c.t_start, start, c.reduction_kw))
        if c.t_end > end:
            released.append(Commitment(end, c.t_end, c.reduction_kw))
    return tuple(released)


def _measure_window_shed_kw(
    before_totals: pd.Series, after_df: pd.DataFrame, envelope: pd.DataFrame,
    window_start: pd.Timestamp, window_end: pd.Timestamp,
) -> float:
    """Actual kW shed over `[window_start, window_end)`, measured directly by diffing
    the committed schedule's totals against the amended one (issue #27 finding 4) --
    never inferred from the envelope-tightening fraction used to search for it, which
    keeps reporting "success" even once the site has no more load left to shed and so
    silently overstates delivery. Returns the minimum per-(site, t) reduction across
    the window, since a per-interval firm promise is only as good as its worst
    interval; 0.0 if the window contains no envelope timestamps.
    """
    after_totals = (
        after_df.groupby(["site_id", "t"])["power_kw"].sum() if len(after_df) else pd.Series(dtype=float)
    )
    env = envelope.copy()
    env["t"] = _as_utc(env["t"])
    reductions = []
    for row in env[["site_id", "t"]].drop_duplicates().itertuples(index=False):
        if not (window_start <= row.t < window_end):
            continue
        before = float(before_totals.get((row.site_id, row.t), 0.0))
        after = float(after_totals.get((row.site_id, row.t), 0.0))
        reductions.append(before - after)
    return min(reductions) if reductions else 0.0


def dispatch(schedule: pd.DataFrame, event: ReductionEvent) -> pd.DataFrame:
    """Grid operator calls mid-window: curtail to deliver `event.reduction_kw` within
    the notice period, then recover the deferred energy before every deadline. Returns
    the amended schedule (same columns as `schedule()`).

    Never both accepts the call and misses a deadline: re-solves the site(s) from
    `event.call_t` forward (time before it is frozen to the committed schedule's
    realised power -- see issue #27 finding 5 and `_release_floor_over_window`'s note
    on finding 1) with the compliance window's envelope temporarily tightened by the
    requested reduction and the sold floor released over that same window, and if that
    is not fully feasible without a deadline miss, bisects down to the largest
    reduction that is. The achieved amount and shortfall recorded on the returned
    frame's `.attrs['reduction_kw_achieved']` / `.attrs['reduction_shortfall_kw']` are
    *measured* directly from the amended schedule (issue #27 finding 4), never the
    envelope-tightening fraction used to search for them: that fraction keeps
    "succeeding" once a site has no more load left to shed, which used to report full
    delivery for a call that shed nothing.

    `schedule` must be the object returned by `schedule()` in this same process --
    see the module docstring's ".attrs side-channel" note for why.
    """
    sessions = schedule.attrs.get("sessions")
    envelope = schedule.attrs.get("envelope")
    prices = schedule.attrs.get("prices")
    commitments = schedule.attrs.get("commitments", ())
    solver = schedule.attrs.get("solver", "lp")
    peak_price_eur_per_kw = schedule.attrs.get("peak_price_eur_per_kw", 0.0)
    if sessions is None or envelope is None or prices is None:
        raise ValueError(
            "src.sched.dispatch: `schedule` must be a DataFrame returned by "
            "src.sched.api.schedule() -- it carries the original sessions/envelope/"
            "prices in .attrs, which dispatch() needs to compute a feasible recovery. "
            "See the src.sched.api module docstring."
        )

    # issue #27 finding 8: a window whose end does not follow its start caps nothing
    # (the `compliance_start <= t < compliance_end` guard below is simply never true),
    # so the untouched problem re-solves as if no call had been made and the full
    # request silently invoices as delivered. Reject rather than coerce.
    if not np.isfinite(event.notice_min) or event.notice_min < 0:
        raise ValueError(
            f"src.sched.dispatch: event.notice_min must be finite and >= 0 minutes, "
            f"got {event.notice_min!r}"
        )
    if not np.isfinite(event.duration_min) or event.duration_min <= 0:
        raise ValueError(
            f"src.sched.dispatch: event.duration_min must be finite and > 0 minutes "
            f"(an end that does not follow the start is not a compliance window), "
            f"got {event.duration_min!r}"
        )

    call_t = pd.Timestamp(event.call_t)
    call_t = call_t.tz_localize("UTC") if call_t.tzinfo is None else call_t.tz_convert("UTC")
    compliance_start = call_t + timedelta(minutes=event.notice_min)
    compliance_end = compliance_start + timedelta(minutes=event.duration_min)

    sched = schedule.copy()
    sched["t"] = _as_utc(sched["t"])
    site_totals_before = sched.groupby(["site_id", "t"])["power_kw"].sum()

    # ---- issue #27 finding 5: freeze what has already elapsed. `t < call_t` was
    # physically drawn under the committed schedule; a live dispatch can defer and
    # recover *future* energy, but it cannot go back and redraw power that was not
    # drawn. Only the residual problem -- each session's remaining energy over its
    # remaining window, from call_t forward -- is handed to the solver; everything
    # before call_t is carried through from `schedule` untouched.
    past = sched[sched["t"] < call_t].copy()
    if len(past):
        site_dt: dict[str, pd.Series] = {}
        for site_id, grp in envelope.groupby("site_id"):
            site_dt[site_id] = _infer_interval_hours(grp["t"])
        dt_vals = [
            float(site_dt[sid].loc[t]) if sid in site_dt and t in site_dt[sid].index else 0.0
            for sid, t in zip(past["site_id"], past["t"])
        ]
        delivered_past = (
            (past["power_kw"] * pd.Series(dt_vals, index=past.index))
            .groupby(past["session_id"]).sum()
        )
    else:
        delivered_past = pd.Series(dtype=float)

    future_rows = []
    for row in sessions.itertuples(index=False):
        t_arrive = pd.Timestamp(row.t_arrive)
        t_depart = pd.Timestamp(row.t_depart)
        new_arrive = max(t_arrive, call_t)
        if new_arrive >= t_depart:
            continue  # this session's whole window has already elapsed
        remaining = max(0.0, float(row.energy_kwh) - float(delivered_past.get(row.session_id, 0.0)))
        d = row._asdict()
        d["t_arrive"] = new_arrive
        d["energy_kwh"] = remaining
        future_rows.append(d)
    future_sessions = (
        pd.DataFrame(future_rows, columns=sessions.columns) if future_rows else sessions.iloc[0:0].copy()
    )
    future_envelope = envelope[_as_utc(envelope["t"]) >= call_t]

    # ---- issue #27 finding 1: release the sold floor over the compliance window
    # (see `_release_floor_over_window`'s docstring) so the tightened envelope cap is
    # the only thing limiting load there, instead of fighting a floor that is being
    # delivered, not held.
    released_commitments = _release_floor_over_window(commitments, compliance_start, compliance_end)

    def envelope_at_alpha(alpha: float) -> pd.DataFrame:
        env = future_envelope.copy()
        env["t"] = _as_utc(env["t"])
        capped = []
        for row in env.itertuples(index=False):
            cap = row.max_kw
            if compliance_start <= row.t < compliance_end:
                original_total = site_totals_before.get((row.site_id, row.t), 0.0)
                target = max(0.0, original_total - alpha * event.reduction_kw)
                cap = min(cap, target)
            capped.append((row.site_id, row.t, cap))
        return pd.DataFrame(capped, columns=["site_id", "t", "max_kw"])

    def try_alpha(alpha: float):
        env_a = envelope_at_alpha(alpha)
        try:
            return _solve_all_sites(
                future_sessions,
                env_a,
                prices,
                released_commitments,
                solver,
                peak_price_eur_per_kw=peak_price_eur_per_kw,
            )
        except Infeasible:
            return None

    if event.reduction_kw <= 0:
        future_best = _solve_all_sites(
            future_sessions,
            future_envelope,
            prices,
            commitments,
            solver,
            peak_price_eur_per_kw=peak_price_eur_per_kw,
        )
        best_alpha = 0.0
    else:
        future_best = try_alpha(1.0)
        if future_best is not None:
            best_alpha = 1.0
        else:
            lo, hi = 0.0, 1.0
            future_best = _solve_all_sites(
                future_sessions,
                future_envelope,
                prices,
                commitments,
                solver,
                peak_price_eur_per_kw=peak_price_eur_per_kw,
            )
            best_alpha = 0.0
            for _ in range(16):  # ~1.5e-5 resolution on alpha
                mid = (lo + hi) / 2.0
                candidate = try_alpha(mid)
                if candidate is not None:
                    future_best, best_alpha, lo = candidate, mid, mid
                else:
                    hi = mid

    best_df = (
        pd.concat([past[_SCHEDULE_COLUMNS], future_best[_SCHEDULE_COLUMNS]], ignore_index=True)
        if len(past) else future_best[_SCHEDULE_COLUMNS].copy()
    )
    best_df = best_df.sort_values(["site_id", "t", "session_id"]).reset_index(drop=True)

    # issue #27 finding 4: report what was actually shed, not what the alpha search
    # merely proved solvable.
    achieved = _measure_window_shed_kw(
        site_totals_before, best_df, envelope, compliance_start, compliance_end
    )
    shortfall = max(0.0, float(event.reduction_kw) - achieved)

    best_df.attrs["sessions"] = sessions
    best_df.attrs["envelope"] = envelope
    best_df.attrs["prices"] = prices
    best_df.attrs["commitments"] = commitments
    best_df.attrs["solver"] = solver
    best_df.attrs["peak_price_eur_per_kw"] = float(peak_price_eur_per_kw)
    best_df.attrs["reduction_kw_requested"] = float(event.reduction_kw)
    best_df.attrs["reduction_kw_achieved"] = float(achieved)
    best_df.attrs["reduction_shortfall_kw"] = float(shortfall)
    return best_df
