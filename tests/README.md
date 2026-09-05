> **LOCKED governing file.** Do not edit in place. See `GOVERNANCE.md`.

# tests/ — conventions

Baseline exists as of 2026-09-01 (`ADR-0002` accepted): `pytest` on Python 3.12, one green smoke
test per lane. `python3 -m pytest tests -q` → 10 passed on a clean checkout with no data downloaded.

## Layout
```
tests/canary/                 the bus's own canary lane (do not repurpose)
tests/src/<lane>/             a lane's tests; ONLY that lane's claim may touch them
tests/src/<lane>/conftest.py  lane-local fixtures — the ROOT conftest.py is shared state, so lanes
                              do not add one; put shared helpers in a design PR instead
```
Every directory carries an `__init__.py`: test modules are imported as packages so two lanes may
have same-named test files without colliding.

## Rules
1. **A lane's verify command must pass on a machine that has never downloaded a dataset.** If real
   data would make the test meaningful, check in a small fixture (< 200 kB) instead of skipping.
2. **No network in tests.** Ingest code is tested against checked-in sample payloads. A test that
   reaches the internet is a broken test the moment the venue's wifi is.
3. **Don't delete `test_lane_surface.py`.** It is what keeps an unimplemented lane from reporting a
   green suite of zero tests (`pytest` exits 5 on no-collection, which reads as a failure, not a
   pass — that is deliberate).
4. **Assert the contract, not the implementation.** The guarantees listed in `contracts/src/<lane>.md`
   ("energy is conserved", "quantiles are monotone", "no deadline is missed", "pooling ≥ naive sum")
   are the tests that matter. Each of those sentences should be findable as a test name.
5. **Determinism.** Anything that samples takes a `seed` and is asserted to reproduce. A reviewer
   runs your test twice.
6. **Speed.** A lane's suite stays under ~30 s; CI runs it on every push and a slow suite stops
   being run. Mark anything longer and keep it out of the default path.

## Test fixtures and .gitignore
Fixture files under `tests/` must stay trackable in git. The top-level `.gitignore` carries a
`!tests/**` negation guard specifically so the `data/` and build-artifact patterns can never
silently swallow a fixture in here — verify with `git check-ignore -v <fixture-path>` after adding
one, not by assuming the guard works.
