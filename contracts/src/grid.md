# src/grid — contract

The physics of one site: how much power the connection can actually carry, at each moment, given
what it has already been carrying and how warm it is outside. Produces the **feasible power
envelope** that the scheduler may never exceed.

This is the lane that makes the project defensible to anyone who has run a real depot: capacity is
thermal and per-phase, not a single number on a nameplate.

Read `contracts/CONVENTIONS.md` first.

## Public API — `src/grid/api.py`

```python
@dataclass(frozen=True)
class SiteElectrical:
    site_id: str
    transformer_kva: float
    phases: int                  # 1 or 3
    tau_oil_min: float           # oil time constant, minutes
    tau_winding_min: float
    delta_top_oil_rated_c: float # rated top-oil rise
    hotspot_limit_c: float       # sustained limit (default from IEC 60076-7 loading guide)
    hotspot_emergency_c: float
    point_phase: Mapping[str, int]   # charge point -> which phase (1|2|3) it is wired to
    provenance: str = ""         # how transformer_kva was derived; required, see infer_electrical

def infer_electrical(sites: pd.DataFrame, *, seed: int) -> dict[str, SiteElectrical]
    """Registry data has no transformer rating; derive it from installed capacity with a documented
    sizing rule, and record the rule in the returned object's provenance field. Deterministic."""

def hotspot_temperature(load_kw: pd.Series, ambient_c: pd.Series, site: SiteElectrical) -> pd.Series
    """Exponential top-oil + winding model (IEC 60076-7 clause 7, difference-equation form). Stateful in
    time: the answer at t depends on the whole preceding load path, which is the point."""

def thermal_envelope(site: SiteElectrical, ambient_c: pd.Series, *,
                     prior_load_kw: pd.Series | None = None) -> pd.DataFrame
    """t, max_kw, clipped — the largest constant-over-interval load that keeps hotspot <= limit
    given ambient and thermal history. Cold night => max_kw above nameplate; hot evening => below.
    `clipped` is True where the solved value hit the absolute ceiling on how far above nameplate
    this lane will ever go, per "any coercion is observable" in CONVENTIONS.md.
    A NaN in `ambient_c` raises: missing weather must never read as unlimited headroom."""

def phase_allocate(demand_kw: Mapping[str, float], site: SiteElectrical) -> dict[str, float]
    """Per-point setpoints respecting each phase's own limit. Returns the achievable allocation."""
def phase_imbalance(setpoints: Mapping[str, float], site: SiteElectrical) -> float   # kW, worst phase vs mean
def envelope_violations(schedule: pd.DataFrame, envelopes: pd.DataFrame) -> pd.DataFrame
    """The audit function the scheduler is tested against and the UI shows. Empty frame = clean."""
```

## Guarantees
- `thermal_envelope` is monotone in ambient temperature (colder ⇒ never less headroom) and never
  returns negative `max_kw`.
- With ambient at the rated reference and steady load, `max_kw` equals the nameplate rating to
  within 2 % — the model must reduce to the trivial answer in the trivial case.
- `phase_allocate` never returns an allocation that violates a phase limit; if demand cannot be met
  it returns the best feasible one and the caller sees the shortfall.
- Pure functions: no I/O, no data downloads. Inputs are frames the caller loaded.

## Assumption discipline
Every constant (thermal constants, sizing rule, phase-assignment default) is a named field with a
comment citing where the value came from. Where no public value exists, say so in the docstring and
expose it as a parameter — a hidden magic number here silently invalidates every euro downstream.

## Explicitly not this lane's job
Choosing what to charge when (`src/sched`), bidding (`src/market`), forecasting (`src/forecast`).
