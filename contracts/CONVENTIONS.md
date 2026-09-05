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

## Tests
- A lane's tests live only in `tests/<lane>/`; fixtures too (small, checked in, < 200 kB).
- No test may hit the network. Ingest tests run against checked-in sample payloads.
- A lane's verify command (`docs/verify.txt`) must pass on a machine that has never downloaded a
  dataset. If your lane needs real data to be meaningful, ship a 3-site fixture.

## Provenance rule
Every figure that reaches the UI or the pitch traces to a canonical table (with its `.meta.json`
source URL) or to a named constant in `src/market/api.py::ASSUMPTIONS` with a comment saying
where it came from. No invented statistics — the same rule `COMPETITION.md` applies to event facts.
