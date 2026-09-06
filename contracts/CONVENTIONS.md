# Cross-lane conventions

Design-owned. Every lane obeys these; a contract only spells out what it adds on top.

## Units — never carry a bare number across a lane boundary
| quantity | unit | column suffix |
|---|---|---|
| power | kW | `_kw` |
| energy | kWh | `_kwh` |
| price (electricity) | EUR/MWh | `_eur_mwh` |
| capacity price (reserve) | EUR/MW/h | `_eur_mw_h` |
| money | EUR | `_eur` |
| temperature | °C | `_c` |
| carbon intensity | g CO₂/kWh | `_g_kwh` |

Power is instantaneous mean over the interval that starts at `t`. Convert with the interval length,
never with a hard-coded `/4`.

## Time
- One column named `t`, `datetime64[ns, UTC]`, tz-aware, **interval-start** labelled.
- Native resolution is **15 minutes** (the German market's imbalance interval). Hourly sources are
  forward-filled on ingest and the fact is recorded in the table's `_meta` sidecar.
- Anything shown to a human is `Europe/Berlin`; anything stored or computed is UTC. The DST-shifted
  day (25 h / 23 h) is a real test case, not an edge case — `2026-10-25` must not crash a pipeline.

## On-disk layout
`DATA_ROOT` = env `FLEXGRID_DATA` or `./data`. Gitignored in full.

```
data/raw/<source>/<file as downloaded>         # never parsed by anything but src/data
data/canonical/<table>.parquet                 # the only cross-lane data interface
data/canonical/<table>.meta.json               # {source_url, retrieved_at, rows, resolution_min, license}
data/derived/<lane>/<artifact>                 # a lane's own outputs; other lanes read via that lane's API, not the path
data/models/<name>/                            # fitted models
```

A lane **reads** `data/canonical/` through `src/data`'s `load()`, and reads another lane's
derived artifacts only through that lane's Python API. No lane hard-codes another lane's paths.

## Python
- `python3.12`, standard library + `requirements-dev.txt` only. A new third-party dependency is a
  `agent:devin` issue, not a commit: `requirements-dev.txt` is canary-gated.
- Every lane exposes exactly one public module: `src/<lane>/api.py`. Everything else in the lane is
  private and may be refactored freely. Cross-lane imports name that module and nothing else:
  `from src.forecast import api as forecast`. Importing any other module of another lane is a
  contract violation a reviewer will reject, even though Python permits it.
- Lane directories are plain identifiers (`src/data`, not `src/10_data`) so they stay importable;
  the data-flow order lives in `docs/STATE.md`, not in the directory names. Run everything from the
  repo root (`python3 -m …`) — no `sys.path` surgery, no editable install.
- Type hints on every public function. Dataframes are `pandas.DataFrame`; document columns in the
  docstring and assert them at the boundary with `require_columns(df, [...])` from `src.data.api`.
- Determinism: any function that samples takes `seed: int` and returns identical output for
  identical inputs. A reviewer will run it twice.

## Any coercion is observable
When a function enforces a constraint by changing a value rather than by failing — clipping,
clamping, flooring at zero, sorting crossed quantiles, rounding a bid down to a whole block,
substituting a default for a missing input — the frequency of that change is part of the return
value, not an implementation detail.

- Return it as a column (`thermal_envelope`'s `clipped`) where it is per-row, or in `df.attrs` /
  an attribute on the returned object (`floor_clamp_rate`, `last_predict_crossing_rate`) where it
  is a rate.
- Name it for what happened, and make it a rate or a count — not a boolean "something was coerced".
- A test must pin it at both ends: a fixture that forces the coercion and one that avoids it, so
  the measurement is proven to move rather than merely to exist.

The reason is specific to this project: every coercion here moves the answer in a direction that
flatters us or endangers a commitment, and the difference between a defensible number and a made-up
one is whether we can say how often the constraint bound. A silent coercion also hides bugs — a NaN
ambient temperature made `thermal_envelope` return *maximum* headroom, and only an unrecorded clamp
kept that invisible.

Preferred over coercion, where the choice exists: reject the input (`_no_nan`) or remove the cause.
Missing data is a fact the operator needs, not something to paper over with a permissive default.

### Measure the physical quantity, not the derived one
A telemetry figure must be computed from the thing it claims to describe. Two ways this rule has
already been broken here, both of them past a green suite:

- `src/market`'s `penalty_bind_rate` counted how often the penalty *in euros* was non-zero. Under a
  negative price the penalty silently inverted sign and the rate kept reading correctly, because
  money is downstream of the bug. Counting the *kWh shortfall* — the physical event — catches it.
- `src/sched`'s `reduction_kw_achieved` reported the envelope-tightening fraction the solver proved
  feasible, not the kilowatts the amended schedule actually shed. It read full delivery for a call
  that shed nothing, and `settle()` invoiced off it.

So: derive a metric from measured state (a diff of two schedules, a shortfall in kWh, a count of
rows), never from the search parameter, feasibility flag or intermediate that *led to* the state.
If a metric cannot be made to move by any fixture you can write, it is not evidence — and if the
coercion behind it turns out to be physically impossible, delete the coercion rather than ship a
counter pinned at zero. A number that cannot vary reads as proof while proving nothing, which is
worse than no number at all.

## Precedence when documents disagree
`contracts/` wins over an issue body, over a docstring, over neighbouring code. An issue is a
request for work and may be written before the interface settles; the contract is the interface
other lanes are built against, so implementing the issue's version silently breaks a lane that
read the contract.

An issue body **does** define *scope* — how much of the contract to implement now. The contract
describing a function is not authority to implement it in whatever issue happens to be open.
Contract decides *what a thing is*; issue decides *whether it is in this PR*.

If the contract is what is wrong, say so in the PR and implement the contract anyway. Fixing it is
a design PR, and until that lands, one lane quietly right is worse than every lane consistently
wrong.

## Tests
- A lane's tests live only in `tests/<lane>/`; fixtures too (small, checked in, < 200 kB).
- No test may hit the network. Ingest tests run against checked-in sample payloads.
- A lane's verify command (`docs/verify.txt`) must pass on a machine that has never downloaded a
  dataset. If your lane needs real data to be meaningful, ship a 3-site fixture.

## Provenance rule
Every figure that reaches the UI or the pitch traces to a canonical table (with its `.meta.json`
source URL) or to a named constant in `src/market/api.py::ASSUMPTIONS` with a comment saying
where it came from. No invented statistics — the same rule `COMPETITION.md` applies to event facts.
