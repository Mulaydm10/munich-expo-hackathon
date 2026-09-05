# src/sched — contract

The optimiser. Charges every vehicle by its deadline, inside the site's physical envelope, while
holding the reduction floor that was sold to the grid, at the lowest energy cost.

Priority order is not negotiable and is asserted in tests: **deadlines first, physics second,
promise third, price fourth.** A schedule that saves money and leaves a van empty is a failure.

Read `contracts/CONVENTIONS.md` first.

## Public API — `src/sched/api.py`

```python
@dataclass(frozen=True)
class Commitment:
    t_start: datetime; t_end: datetime; reduction_kw: float   # sold to the grid; requires load >= reduction_kw

def schedule(sessions: pd.DataFrame, envelope: pd.DataFrame, prices: pd.DataFrame,
             *, commitments: Sequence[Commitment] = (), solver: Literal["lp", "greedy"] = "lp",
             ) -> pd.DataFrame
    """t, site_id, session_id, power_kw. LP over 15-min intervals:
       min  sum(price_t * power) ;  s.t.
         sum_t power * dt >= energy_kwh_due      per session      (deadline feasibility)
         power == 0 outside [t_arrive, t_depart]
         power <= max_power_kw                    per session
         sum_sessions power <= envelope.max_kw    per site-t      (thermal)
         sum_sessions power >= reduction_kw       during each commitment window   (the floor)
    Infeasible => raises Infeasible with the binding constraint named, never a silent relaxation."""

def dispatch(schedule: pd.DataFrame, event: ReductionEvent) -> pd.DataFrame
    """Grid operator calls mid-window: pause/curtail points to deliver `event.reduction_kw` within
    the notice period, then recover the deferred energy before every deadline. Returns the amended
    schedule. This is the live demo action."""

def evaluate(schedule: pd.DataFrame, sessions: pd.DataFrame, envelope: pd.DataFrame,
             prices: pd.DataFrame, commitments: Sequence[Commitment]) -> dict
    """{energy_cost_eur, unmet_kwh, deadline_misses, envelope_violation_kwh, floor_shortfall_kw_min,
    peak_kw}. The scorecard every comparison in DEMO.md and the UI is built from."""

def baseline(sessions: pd.DataFrame, *, policy: Literal["asap", "even"] = "asap") -> pd.DataFrame
    """Uncontrolled charging, same shape as `schedule` — every claim is stated against this."""
```

## Guarantees
- `evaluate(schedule(...))` has `unmet_kwh == 0`, `deadline_misses == 0` and
  `envelope_violation_kwh == 0` for every feasible instance. These are hard assertions, not metrics.
- `floor_shortfall_kw_min == 0` whenever the commitment was sized from `src/market.firm_capacity`
  on the same forecast — the two lanes must agree, and a joint test proves it.
- `dispatch` never causes a deadline miss; if the event cannot be served without one, it delivers
  the largest feasible reduction and reports the shortfall (an honest partial beats a broken van).
- Deterministic and solver-agnostic: `greedy` is a fallback with the same signature, used when the
  LP is too slow for the national run. Both are tested against the same assertions.
- Scales by decomposition: sites are independent given their envelopes and commitments, so the
  national run is per-site LPs, not one giant program.

## Explicitly not this lane's job
Deciding *how much* to sell (`src/market`), computing the envelope (`src/grid`), forecasting.
