"""The `src/service` -> `src/market` seam, exercised through the REAL market lane.

## Why this test exists

`src/service/_pipeline._pooling()` calls `src.market.pool()` and
`src.market.diversification_curve()`. Every `tests/src/service/` test that runs the
pipeline uses the *real* `market.pool()` (only two tests in that lane's suite monkeypatch
it, both in `test_market_seam.py`, to pin `_pooling()`'s own row-conversion arithmetic in
isolation). But no lane-confined suite is *allowed* to prove that "the real
`src.market.pool`/`diversification_curve`, imported from the real, merged `src/market`
lane, produces a non-null `pool_firm_mw` through a real HTTP request" -- that is a
cross-lane claim by definition (contracts/CONVENTIONS.md's lane confinement rule keeps
`tests/src/service/` from asserting anything about `src/market`'s own correctness, only
about how `src/service` reacts to what `src/market` returns).

`docs/verify.txt` maps one merged PR to exactly one lane's suite (`resolve` -> `run`,
`.github/workflows/checks.yml`). That is precisely why issue #38's defect 1 was invisible
to CI: `src/service`'s 100 tests were green while `src.market.pool`/`diversification_curve`
did not exist (the block was dead code -- guarded on `_capability(...)` returning `None`),
and stayed green the moment `src/market` landed and the block went live, because no
single PR's CI run exercises both lanes' real code at once. This suite does, on every PR,
regardless of which lane's `verify.txt` command also ran (see `tests/integration/README.md`).

## Why `pool_method="sum"` for the success case

`market.pool()`'s `empirical`/`gaussian_copula` methods measure a dependence structure
from `firm`'s own residuals when no `correlation=` is passed. `firm` here only ever spans
ONE scenario day (`_pipeline._forecast()` predicts `day_rows`, not the history window),
so every "time of day" bucket has exactly one observation and the residual is identically
zero -- `pool()` raises `MarketError` ("... zero residual variation ...") for BOTH of
those methods on every scenario this project can build today. This is not a fixture
artefact: `tests/src/service/test_market_seam.py::test_pool_unavailable_still_nulls_rather_than_zeroes`
pins the same real, unstubbed null for the default `pool_method="empirical"`, and
`test_real_market_pool_failure_is_a_real_documented_condition` below reproduces that same
real failure directly (no monkeypatch) as the "upstream failure" half of this file.

`pool_method="sum"` is the one real method that does NOT need a measured dependence
structure (it is the contract's own naive lower bound, `contracts/src/market.md`), so it
is the one real, unstubbed configuration in which `_pooling()` can succeed -- which is
exactly the scenario the task asks for: "a scenario where market is available".

`test_scenario_runs_through_real_market_and_returns_200` is the test that would have
caught issue #38's defect 1: it asserts `pool_firm_mw` is not None using the real,
unstubbed `src.market`, which the falsification (see the PR's report) proves by literally
reintroducing the missing `except market.MarketError` handling defect 1 was.
"""

from __future__ import annotations

from .conftest import FEASIBLE_SPEC, warm

# The one pool_method that succeeds against a real, single-day, unstubbed `src.market`
# (see the module docstring). Using it is what makes this "a scenario where market is
# available" rather than another instance of the pool()-always-fails case.
SUM_POOL_SPEC = {**FEASIBLE_SPEC, "pool_method": "sum"}


def test_scenario_runs_through_real_market_and_returns_200(client):
    """The seam test for issue #38's defect 1.

    Runs a full scenario end to end through the real, merged `src/service` -> `src/market`
    seam (`market.pool`, `market.diversification_curve` are imported for real -- nothing
    in this test or its fixtures monkeypatches `src.market`) and asserts:

    - the HTTP response is 200, never a 500 -- the exact symptom issue #38 recorded
      ("58 of 105 service tests failed, every one a 500 -- every `POST /api/scenario`");
    - `_pooling()` genuinely executed and produced a number: `pool_firm_mw` is not
      None. Before #33 merged `src.market.pool`, this branch was dead code and every
      lane's tests passed whether or not the call underneath it was correct -- this
      assertion is the one that fails if that regresses.
    - the figure is physically sane: a non-negative number of megawatts, well under 1 MW
      for this 2-site fixture portfolio (the kW->MW `/1000.0` conversion named in issue
      #38 is exercised, not just present), and `peakers_displaced` -- which is only
      computed *from* `pool_firm_mw` -- is present alongside it.
    """
    response = warm(client, SUM_POOL_SPEC)
    assert response.status_code == 200, f"{response.status_code}: {response.text}"
    body = response.json()

    totals = body["totals"]
    assert totals["pool_firm_mw"] is not None, (
        "pool_firm_mw is null: either src.market.pool()/diversification_curve() raised "
        "and _pipeline._pooling() did not catch it (issue #38 defect 1), or market "
        "genuinely has nothing to promise for this fixture -- either way the scenario "
        "must not silently drop the seam this test exists to exercise"
    )
    # Physically sane, not just non-null: a 2-site portfolio of a 44 kW AC site and a
    # 66 kW depot promises well under 1 MW of firm capacity either way.
    assert 0.0 <= totals["pool_firm_mw"] < 1.0, totals["pool_firm_mw"]

    assert totals["peakers_displaced"] is not None
    assert totals["peakers_displaced"]["count"] is not None

    warning_codes = {w["code"] for w in body["warnings"]}
    assert "upstream_unavailable" not in warning_codes, (
        "market.pool/diversification_curve reported as unimplemented; src/market has "
        "been merged since #33 and this fixture must exercise it for real"
    )
    assert "pool_failed" not in warning_codes


def test_pooling_route_serves_the_real_diversification_curve_shape(client):
    """`diversification_curve()` always raises `MarketError` in this repo today (it
    requires `realised` load, which nothing in `data/raw/` provides -- see
    `src/market/api.py::diversification_curve`'s own docstring), so `/pooling` must
    answer 200 with an empty (not fabricated) curve rather than a 500 or a silently
    wrong one. This is the other half of `_pooling()`'s two try/excepts, exercised
    through the real function -- not a stand-in for it.
    """
    result = warm(client, SUM_POOL_SPEC)
    assert result.status_code == 200, result.text
    scenario_id = result.json()["id"]

    response = client.get(f"/api/scenario/{scenario_id}/pooling")
    assert response.status_code == 200
    body = response.json()
    assert body["diversification_curve"] == []
    codes = {w["code"] for w in body["warnings"]}
    assert "diversification_curve_failed" in codes


def test_real_market_pool_failure_is_a_real_documented_condition_and_degrades_cleanly(client):
    """The other side of the same guarantee, using the REAL, unstubbed `market.pool()` --
    no monkeypatch anywhere in this test.

    `FEASIBLE_SPEC`'s default `pool_method="empirical"` measures a dependence structure
    from `firm`'s own residuals (see the module docstring: `firm` spans exactly one
    scenario day, so every residual is exactly zero), which is a real, always-triggering
    failure mode of `src.market.pool()` on this project's data -- not a synthetic stub
    shape. The scenario must still answer 200 with `pool_firm_mw: null` and a displayable,
    attributed warning naming the real cause, never a 500.
    """
    response = warm(client, FEASIBLE_SPEC)  # default pool_method="empirical"
    assert response.status_code == 200, f"{response.status_code}: {response.text}"
    body = response.json()

    assert body["totals"]["pool_firm_mw"] is None
    assert body["totals"]["peakers_displaced"] is None
    assert body["totals"]["capacity_revenue_eur"] is None

    warnings = body["warnings"]
    codes = {w["code"] for w in warnings}
    assert "pool_failed" in codes, (
        f"expected pool()'s real zero-residual-variation failure to degrade to a "
        f"'pool_failed' warning; got codes {sorted(codes)}"
    )
    failure = next(w for w in warnings if w["code"] == "pool_failed")
    assert failure["lane"] == "src/market"
    assert "residual variation" in failure["detail"]["detail_text"]

    map_body = client.get(f"/api/scenario/{body['id']}/map").json()
    assert all(row["revenue_eur"] is None for row in map_body["sites"])
