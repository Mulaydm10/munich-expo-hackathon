# src/fleet — contract

Turns a set of real charge-point sites into plausible charging **sessions**, and sessions into the
uncontrolled load curve everything downstream predicts, constrains and reshapes.

This lane is why the project does not depend on a dataset the organizers hand out on the day (see
`COMPETITION.md`). Its output must be defensible as *plausible and parameterised*, never presented
as measured. Every parameter is named, checked in, and cited in `PARAMS` docstrings.

Read `contracts/CONVENTIONS.md` first.

## Public API — `src/fleet/api.py`

```python
@dataclass(frozen=True)
class FleetParams:
    vehicles_per_point: float        # utilisation assumption
    arrival_mode_h: float            # local-time hour of peak plug-in (depot: ~18)
    arrival_spread_h: float
    dwell_mean_h: float
    energy_mean_kwh: float
    energy_cv: float                 # coefficient of variation
    soc_topup_share: float           # fraction of sessions that are short top-ups
    temp_penalty_pct_per_c: float    # cold-weather energy penalty below 10 C
    weekday_factor: tuple[float, ...]  # Mon..Sun multipliers
    profile: Literal["depot", "workplace", "public_ac", "public_dc"]

PROFILES: dict[str, FleetParams]     # one calibrated-by-hand default per profile

def classify_sites(sites: pd.DataFrame) -> pd.DataFrame
    """+ column `profile`, derived from rated power / n_points / operator, rules documented inline."""

def synthesise_sessions(sites: pd.DataFrame, weather: pd.DataFrame, days: Sequence[date],
                        *, params: Mapping[str, FleetParams] = PROFILES, seed: int) -> pd.DataFrame
    """columns: session_id, site_id, t_arrive, t_depart, energy_kwh, max_power_kw, n_phases (1|3),
    deadline_t (== t_depart), profile, queued_h, energy_clipped. Deterministic for a given seed.
    `n_phases` is how many phases this session draws on, NOT which phase it is wired to — the
    wiring is `src/grid`'s `SiteElectrical.point_phase`, and the two are different quantities.
    `queued_h` is how long this arrival waited for a free charge point (0.0 if none); the
    per-site arrivals/served/queued/dropped counts live in `.attrs['occupancy']`.
    `energy_clipped` is the residual observability required below — True where the draw had to be
    capped to fit the dwell."""

def to_load(sessions: pd.DataFrame, *, freq: str = "15min",
            policy: Literal["asap", "even"] = "asap") -> pd.DataFrame
    """The uncontrolled baseline: t, site_id, load_kw. `asap` = charge at max power on arrival —
    this is the behaviour the project exists to improve on, so it is the comparison everywhere.
    Dense on `freq` over the span it is given: every (t, site_id) in that span gets a row, and an
    interval with no charging is `load_kw == 0.0` rather than a missing row (see Guarantees)."""

def flexible_energy(sessions: pd.DataFrame) -> pd.DataFrame
    """t, site_id, energy_kwh_due, latest_start_kw — the headroom a scheduler is allowed to move."""
```

## Guarantees
- `to_load(synthesise_sessions(...))` conserves energy: total kWh equals the sessions' total, to
  within 1e-6.
- No session ends after its `deadline_t`; `energy_kwh <= max_power_kw * dwell_hours` always (an
  infeasible draw is clipped and counted, never emitted). "Counted" is the rule in CONVENTIONS.md
  under "any coercion is observable": a clip rate the caller cannot read is a contract violation,
  because a clipped session has no scheduling slack and so silently removes the flexibility this
  project measures.
- At most `n_points` sessions overlap at a site: arrivals contend for a finite number of charge
  points, and what queueing or turning away did is counted. Consequently
  `to_load(...).load_kw` never exceeds the site's `rated_power_kw` — the uncontrolled baseline must
  be a load the connection could physically carry, or every flexibility figure derived from it is
  inflated.
- `to_load` returns a **dense** grid: one row per (interval, site) across the span, zeros filled
  explicitly, so `len(out) == n_intervals * n_sites`. An omitted interval makes "nobody charged"
  indistinguishable from "no data" — the distinction CONVENTIONS.md requires — and this is the one
  frame where zero is the common case, since the uncontrolled baseline is idle most of the night.
  It is also consumed directly: `src/market.settle` validates full delivery coverage and rejects an
  incomplete grid outright, and a sparse frame silently deflates every mean and peak computed off
  it. `flexible_energy` follows the same rule.
- Two calls with the same `seed` are byte-identical; different `seed` values are independent.
- Runs on the full national site table without materialising per-second data (target: 50 k sites ×
  1 day in under 60 s on a laptop; chunk by site if needed).

## Validation duty
If the organizers' sample charging-session file arrives, this lane owns the comparison: a
`validate_against(real_sessions)` function reporting distribution distances (arrival hour, dwell,
energy) between synthetic and real. The synthetic generator is then re-fitted, not replaced.

## Explicitly not this lane's job
Forecasting (`src/forecast`), transformer limits (`src/grid`), scheduling (`src/sched`).
