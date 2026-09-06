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

@dataclass(frozen=True)
class ReductionEvent:
    """The demo's key payload: the grid operator's call. Ratified 2026-09-06 (#52).

    Until then this type was named in three contracts but defined in none, and
    `src/sched` carried it with a docstring that said so: "GUESS ... its fields are
    not specified anywhere in contracts/". Three lanes were built on that guess.
    These four fields ARE the interface; they are not a suggestion.

    call_t:       when the operator places the call. TZ-AWARE, UTC.
    notice_min:   minutes before compliance is required. >= 0. The compliance
                  window opens at call_t + notice_min.
    duration_min: how long the reduction must be sustained. > 0 strictly -- an end
                  that does not follow its start caps nothing and would settle as
                  full delivery.
    reduction_kw: requested reduction in total site load vs the committed
                  schedule. >= 0.
    """
    call_t: datetime      # tz-aware, UTC
    notice_min: float     # >= 0
    duration_min: float   # > 0
    reduction_kw: float   # >= 0

**The wire form is exactly these four keys, and nothing else.** `src/service` rejects an unknown
field outright rather than ignoring it: a typo'd or invented field is a caller bug, and silently
dropping it would let a UI believe it had sent something it had not. This makes the payload
deliberately **non-additive**, which is a stated exception to `service.md`'s "additive changes only"
rule -- adding a field here is a contract change requiring a design PR and comments on every open
claim in `src/ui` and `src/voice`.

**Types are strict.** A JSON number is a number: `"60"` is rejected, `60` is accepted, and a bool is
rejected before anything else (`isinstance(True, int)` is True in Python, so an unguarded numeric
check would accept `true` as `1`). Non-finite values are rejected by name -- `json.loads` accepts the
literals `NaN`, `Infinity` and `-Infinity`, and because every comparison against NaN is False the
range guards above would all *pass* for NaN, authorising a dispatch that promises nothing.

**`call_t` must be tz-aware; a naive timestamp is rejected.** Any valid offset is accepted and
normalised to UTC, and the normalised instant is what settles. Rejecting non-UTC offsets outright
was considered (#52) and deliberately not adopted: `+02:00` names exactly one instant, so converting
it is unambiguous rather than a guess, and refusing it would make a correct request fail for a
cosmetic reason. The coercion is observable because it is specified here -- CONVENTIONS requires a
coercion to be visible and pinned, not that it be forbidden. Naive is different in kind and stays
rejected: it names no instant at all, and guessing a zone is where DST bugs come from.

**The compliance window must contain at least one interval of the scenario day.** `call_t +
notice_min` through `+ duration_min` is checked against the rendered day's grid, so a call placed so
late that its window runs past the last interval is a 400, not a silently empty settlement.

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
