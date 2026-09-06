# tests/integration/ — the cross-lane tier

Design-owned (like `tests/README.md`'s baseline, but this file is a normal, unlocked
file so it can be revised as the tier grows -- see `GOVERNANCE.md`). Not a lane: no
`lane:` row in `docs/STATE.md`, no entry in `docs/verify.txt`, and `claim/*` PRs may not
touch it (`.github/workflows/checks.yml`'s "files stay in lane" step only allows a
claim PR to write inside its own `<lane>/` and `tests/<lane>/`).

## Why this exists

`docs/verify.txt` + `.github/workflows/checks.yml`'s `resolve` job map one issue to
exactly ONE lane's test command. That is correct and load-bearing for lane confinement,
and it has a structural blind spot: a defect that only exists at the seam BETWEEN two
lanes' merged code is invisible to both lanes' suites individually, in any language,
because no single PR's CI run ever executes both lanes' real implementations together
(see issue #38). Two real instances already shipped past green CI:

1. `src/service/_pipeline._pooling()` called `src.market.diversification_curve()`
   without the `realised` argument that function requires. While `src.market.pool` did
   not exist, the call was dead code and `src/service`'s suite passed identically
   whether the bug was there or not. The moment `src/market` merged, it went live:
   `pool_firm_mw` -- the project's headline number -- started computing through code no
   test in either lane had ever executed.
2. `src/ui` gated the dispatch button on a `reduction_event` context key nothing in
   production supplied (issue #40), then shipped the identical pattern again for
   `reduction_event_input` (issue #43) -- a lane-confined suite cannot see "does
   anything outside my own fixtures ever produce this," because that question is
   cross-lane by construction.

## What belongs here vs. in `tests/<lane>/`

| | `tests/<lane>/` | `tests/integration/` |
|---|---|---|
| May import | only that lane's `src/<lane>/api.py` (contracts/CONVENTIONS.md) | any lane's `api.py`, freely |
| Runs on | that issue's PR only (`resolve` maps ONE lane to it) | every PR, regardless of lane |
| Tests | that lane's own contract guarantees | a claim that is only checkable with two or more lanes' REAL code running together |
| Owned by | the lane's claim | design (like `contracts/`) |

A test belongs here if, and only if, stubbing out the other lane would make it pass
vacuously. If a lane test can express the same guarantee against a fixture shaped like
the contract, it belongs in that lane instead — this tier is for the cases where the
contract's *shape* agreeing is not the same claim as the two REAL implementations
agreeing (`tests/test_pipeline_seam.py`'s docstring walks through exactly this for
`src.market.pool`/`diversification_curve`).

## The in-lane mitigation this tier does not replace

Landing a cross-lane function before its provider lane is fully wired (`src/service`
calling `src.market.pool` while `pool` did not yet exist) is normal and expected —
lanes are built against contracts, not against each other's finished code
(`docs/STATE.md`: "a downstream lane can be built against a contract before its upstream
lane produces real data"). The rule that keeps THAT gap from reproducing issue #38's
failure mode is narrower than this whole tier and belongs in the consuming lane itself:

> **A lane that guards a code path on another lane's not-yet-implemented function must
> pin that path with a stub in-lane**, so the guarded branch is exercised by that lane's
> own suite regardless of merge order, rather than staying dead code that only starts
> running (untested) the moment the other lane merges.

`src/service/_pipeline._pooling()` does exactly this today: it wraps both
`market.pool(...)` and `market.diversification_curve(...)` in `try/except
market.MarketError`, and `tests/src/service/test_market_seam.py` monkeypatches a
same-call-shape stand-in to pin `_pooling()`'s own row-conversion arithmetic before the
real functions existed. That stub proved the lane's OWN code was correct in isolation —
it did not and could not prove the real `src.market.pool`/`diversification_curve`,
merged for real, actually produce a value there. Both checks are required; neither
substitutes for the other. This tier's `test_scenario_runs_through_real_market_and_returns_200`
is the second half.

## What's here

- `test_pipeline_seam.py` — a scenario through the REAL `src/market` (not a stub, not
  monkeypatched), asserting `pool_firm_mw` is populated and an upstream `MarketError`
  degrades to a warning + null rather than a 500.
- `test_reduction_event_contract.py` — `src/ui`'s `ReductionEvent` construction and
  validity gate agree with `src/service`'s real wire validator on the same set of
  known-good/known-bad events, and a real scenario's own `ReductionEvent` survives the
  validator verbatim.
- `test_no_dead_context_keys.py` — every context key that gates a control in `src/ui`
  (not every display field — see that file's docstring for why the scope is pinned)
  has a real producer somewhere in `src/`, not only in a test fixture. Currently
  `xfail`s on `reduction_event_input` (issue #43) by design — do not remove the xfail
  without fixing #43, and do not weaken the assertion to make it green.

## Speed and data

Same rules as `tests/README.md`: no network, no downloaded dataset, small fixtures built
in Python at test time (`conftest.py`, deliberately NOT importing
`tests/src/service/conftest.py` — see that file's docstring). Session-scoped where the
fixture data is expensive to build and never mutated by a test.
