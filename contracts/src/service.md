# src/service — contract

The seam between the pipeline and everything a human sees. Runs a scenario, caches it, serves JSON.
No modelling lives here; no other lane imports this one.

Read `contracts/CONVENTIONS.md` first.

## Public API — `src/service/api.py`

```python
app: fastapi.FastAPI          # uvicorn src.service.api:app  (canonical run command, see CLAUDE.md)

def run_scenario(spec: ScenarioSpec) -> ScenarioResult      # the whole pipeline, once, cached on disk
```

`ScenarioSpec`: `date`, `site_ids | region | n_sites`, `seed`, `product`, `tau`, `policy`
(`baseline | optimised`), `pool_method`. Fully determines the result — the cache key is its hash,
stored under `data/derived/service/<hash>.json`.

## HTTP surface (frozen — `src/ui` and `src/voice` are written against exactly this)

| method | route | returns |
|---|---|---|
| GET | `/api/health` | `{status, git_sha, tables: {name: rows}}` |
| GET | `/api/sites?bbox=&limit=` | site geometry + profile + rated power, for the map |
| POST | `/api/scenario` | `ScenarioResult` (see below), from cache when warm |
| GET | `/api/scenario/{id}/timeseries` | per-interval `load_kw` baseline vs optimised, `envelope_kw`, `price_eur_mwh`, `firm_kw` |
| GET | `/api/scenario/{id}/pooling` | `diversification_curve` rows |
| GET | `/api/scenario/{id}/map` | per-site `firm_kw`, `revenue_eur`, `co2_kg`, lat/lon |
| POST | `/api/scenario/{id}/dispatch` | applies a `ReductionEvent`; returns amended timeseries + delivered vs promised |
| GET | `/api/scenario/{id}/stream` | SSE tick-by-tick playback of one day (the demo's "press play") |
| GET | `/api/assumptions` | `src/market.ASSUMPTIONS`, verbatim — the provenance rule, exposed |

`ScenarioResult` (top level): `id, spec, totals {energy_cost_eur, capacity_revenue_eur, penalty_eur,
net_eur, co2_kg_saved, peak_kw_baseline, peak_kw_optimised, pool_firm_mw, peakers_displaced},
scorecard (src/sched.evaluate output), calibration {tau: coverage}, warnings[]`.

## Guarantees
- Every route answers in < 500 ms warm. A cold scenario returns `202` with a job id and a
  `progress` field rather than blocking a request for minutes.
- `warnings[]` carries anything the pipeline had to assume or clip (missing table, infeasible site,
  uncalibrated forecast). The UI must be able to display them; silent degradation is forbidden.
- Errors are JSON `{error, detail, how_to_fix}` with the real HTTP status — never a 200 with an
  empty body.
- Additive changes only once `src/ui` exists: removing or renaming a field is a contract change
  (design PR + comments on open claims in `src/ui` and `src/voice`).
- No secrets in responses. `ELEVENLABS_API_KEY` and friends are read from the environment by
  `src/voice`, never proxied through here.

## Explicitly not this lane's job
Any model, any optimisation, any HTML (`src/ui` owns templates and static assets).
