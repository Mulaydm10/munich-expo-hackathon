"""The `src.market.pool()` / `src.market.diversification_curve()` seam.

Both functions are named in `contracts/src/service.md` (`pool_firm_mw`, `peakers_displaced`,
the `/pooling` route) but are not implemented in this checkout -- `src/market` issue #33 is
still open on its own branch. `src/service`'s `_capability()` guard makes that safe *today*:
`_pipeline._pooling()` treats a missing `pool`/`diversification_curve` as `upstream_unavailable`
and nulls the dependent figures (`pool_firm_mw`, `peakers_displaced`, `capacity_revenue_eur`,
the per-site `revenue_eur` on `/map`) rather than reporting a zero.

The moment #33 lands, `market.pool` and `market.diversification_curve` stop being absent and
`_pipeline.py:888-973` (`pool_firm_mw`, `peakers_displaced`, `_bid_revenue`, the map's revenue
allocation) stops being dead code -- and per-lane CI never exercises the composed path, so
nothing would have tested that arithmetic before it started shipping numbers to a judge's
screen. This module makes that path live *now*, without depending on #33 merging or checking
out its branch: `src.market.pool` and `src.market.diversification_curve` are monkeypatched
with fakes returning a known frame, so it is `src/service`'s own arithmetic under test --
the kW->MW conversion, `peakers_displaced` forwarding, the revenue allocation rule, and the
worst-interval (not mean) rule for `pool_firm_mw` -- while the existing
`upstream_unavailable` -> `null` behaviour (never `0.0`) is re-confirmed to still hold once the
stub is removed.

The fakes' shapes were checked against the real functions before being relied on:
`git show claim/33:src/market/api.py | grep -n -A30 "^def pool"` /
`"^def diversification_curve"`. `pool()`'s signature
(`pool(firm, *, method, sites=None, seed=0, correlation=None, n_draws=None) -> t, pool_firm_kw`)
matches the pipeline's call (`pool_fn(firm, method=spec.pool_method, seed=spec.seed)`) exactly,
so the happy-path fake below is a faithful stand-in for it.

`diversification_curve()` does **not** match cleanly, and this file does not pretend otherwise:
its real signature is `diversification_curve(firm, *, sizes, seed, realised=None,
correlation=None, method="gaussian_copula")`, and its body raises `MarketError` unconditionally
when `realised is None` -- "`realised` has no default" is in its own docstring, because a
shortfall rate derived from the same distribution that produced the promise would be
tautological. `_pipeline._pooling()` called it as `curve_fn(firm, sizes=sizes, seed=spec.seed)`
-- it never passed `realised`. That call was accepted by the signature (keyword-compatible) but
raised on every invocation of the *real* function -- and since `_pooling()` runs unconditionally
inside `build()` for every scenario, the instant #33 merged (`origin/main` 5e2d87a), every
`POST /api/scenario` request started failing with a 500, because nothing caught the
`MarketError`. This was confirmed empirically, not just read off the source: a disposable
worktree combining #33's merged `src/market` with this lane's (then-unfixed) code failed
58 of 105 tests, every one with `500 {"error":"scenario_failed","detail":"MarketError: cann..."}`.

`pool()` has the same twin failure mode for a different reason -- it raises `MarketError` when
a site's firm-capacity series has zero residual variation, which is exactly the error the
empirical run surfaced (`"cannot measure correlation: ... zero residual variation"`,
`src/market/api.py:451` on `origin/main`).

`_pipeline._pooling()` now catches `market.MarketError` around both the `pool_fn(...)` and
`curve_fn(...)` calls, exactly the way it already treated (and still treats)
`pool_fn is None` / `curve_fn is None`: a displayable, attributed warning, the dependent
figure(s) left null (never `0.0`, never fabricated), and the scenario still succeeds.
`test_a_documented_diversification_curve_failure_degrades_to_null_not_a_500` and
`test_pool_failure_degrades_to_null_not_a_500` below pin that fixed contract, using fakes
that faithfully reproduce each function's real failure mode rather than a generic exception.
The other tests in this file use a deliberately looser fake for `diversification_curve`
(never requires `realised`) purely to exercise `_pooling()`'s row-shape arithmetic on a
*successful* call, in isolation from the failure path; that divergence from the real contract
is called out here so nobody mistakes it for a rebuttal of the finding above.
"""

from __future__ import annotations

import time

import pandas as pd
import pytest

from src.market import api as market
from src.service import _pipeline as pipeline

from .conftest import DAY, FEASIBLE, day_index, warm

# `client`'s data root is session-scoped; every spec below must be one no other test in the
# whole suite has warmed, or a stale cache entry (from a run with no stub in effect) would be
# served straight back without the pipeline -- and this file's stub -- ever running.
POOLED_SPEC = {**FEASIBLE, "seed": 90210}
BROKEN_CURVE_SPEC = {**FEASIBLE, "seed": 90211}

# A deliberate dip at the very first interval of the day: it falls inside the one 4-hour
# billing block that straddles the day boundary (2026-03-03T20:00Z), which `_bid_revenue`
# always drops as "partial" regardless of its value, so the dip cannot contaminate the
# revenue figure. It exists purely so `pool_firm_mw` -- the day's *worst* interval -- reads
# differently from the day's mean, which is constant at BASE_KW otherwise.
DIP_KW = 500.0
BASE_KW = 3000.0

# Hand-computed and cross-checked against the real `market.bid()`/`peakers_displaced()`
# against this exact fixture (conftest.DAY, conftest.balancing_frame's constant 12.0
# EUR/MWh aFRR capacity price): the day has 5 complete 4-hour blocks and 2 partial ones
# (the two edges), so revenue is 5 * (3000/1000 rounded down to the 1 MW granularity) *
# 12.0 EUR/MWh * 4h = 720.0 EUR; pool_firm_mw is the day's min (the dip) over 1000; and
# peakers_displaced divides that by the 50 MW peaker-plant assumption.
EXPECTED_POOL_FIRM_MW = DIP_KW / 1000.0
EXPECTED_REVENUE_EUR = 720.0
EXPECTED_PEAKERS_COUNT = EXPECTED_POOL_FIRM_MW / 50.0


def _fake_pool(firm, *, method, sites=None, seed=0, correlation=None, n_draws=None):
    """Matches `pool()`'s real signature (verified against claim/33) exactly. Returns
    `t, pool_firm_kw` with one dip at the day's first interval and a constant elsewhere,
    so both the worst-interval rule and the billing-block arithmetic are exercised."""
    ts = sorted(pd.Series(firm["t"]).unique())
    kw = [DIP_KW if i == 0 else BASE_KW for i in range(len(ts))]
    return pd.DataFrame({"t": ts, "pool_firm_kw": kw})


def _fake_diversification_curve_loose(firm, *, sizes, seed, **_ignored):
    """A deliberately loose stand-in: matches the *call shape* `_pooling()` uses (no
    `realised`), NOT the real function's contract (which requires `realised` and raises
    without it -- see the module docstring and the dedicated test below). Used only to
    exercise `_pooling()`'s own row-conversion arithmetic in isolation."""
    sizes = list(sizes)
    return pd.DataFrame(
        {
            "n_sites": sizes,
            "firm_kw_per_site": [100.0 * n for n in sizes],
            "shortfall_rate": [0.05] * len(sizes),
        }
    )


def _fake_diversification_curve_faithful(
    firm, *, sizes, seed, realised=None, correlation=None, method="gaussian_copula"
):
    """Reproduces exactly one documented behaviour of the real (claim/33)
    `diversification_curve()`: it raises `MarketError` when `realised` is not supplied,
    because "`realised` has no default" (its own docstring). Everything else about this
    fake is irrelevant, because the pipeline's call never gets past this check."""
    if realised is None:
        raise market.MarketError(
            "diversification_curve() requires `realised` (t, site_id, realised_kw): "
            "shortfall_rate must be measured against realised load."
        )
    raise AssertionError("unreachable: _pipeline never passes `realised`")


def _post_and_wait(client, body, timeout_s: float = 60.0):
    """Like `conftest.warm`, but returns the final response without asserting it is a
    200 -- needed to observe a scenario that is *expected* to fail cold."""
    response = client.post("/api/scenario", json=body)
    deadline = time.monotonic() + timeout_s
    while response.status_code == 202 and time.monotonic() < deadline:
        time.sleep(0.02)
        response = client.post("/api/scenario", json=body)
    return response


# ---------------------------------------------------------------------------
# the four pins Task 3 asks for
# ---------------------------------------------------------------------------


def test_pool_seam_pins_kw_to_mw_scale_peakers_and_revenue_allocation(client, monkeypatch):
    monkeypatch.setattr(market, "pool", _fake_pool, raising=False)
    monkeypatch.setattr(
        market, "diversification_curve", _fake_diversification_curve_loose, raising=False
    )

    result = warm(client, POOLED_SPEC)
    totals = result["totals"]

    # the /1000.0 kW->MW conversion, measured on the day's WORST interval (the dip), not
    # the mean (which sits at BASE_KW the rest of the day)
    assert totals["pool_firm_mw"] == pytest.approx(EXPECTED_POOL_FIRM_MW)

    # peakers_displaced is forwarded with the assumption it was divided by
    displaced = totals["peakers_displaced"]
    assert displaced is not None
    assert displaced["count"] == pytest.approx(EXPECTED_PEAKERS_COUNT)
    assert "peaker_plant_capacity_mw" in displaced["assumption"]

    # the revenue rule: 5 complete 4-hour blocks, the 2 day-boundary blocks dropped as
    # partial (never silently billed against a half-covered window)
    assert totals["capacity_revenue_eur"] == pytest.approx(EXPECTED_REVENUE_EUR)
    codes = [w["code"] for w in result["warnings"]]
    assert "bid_blocks_partial_dropped" in codes

    # the map's per-site allocation rule: pro-rata by worst-interval firm_kw, summing back
    # to the scenario total. With this fixture's real (non-stubbed) forecast, every site's
    # own day-minimum `firm_kw` is exactly 0.0 -- session-based EV charging load has at
    # least one fully-idle 15-minute interval per site per day, so the 5th-percentile
    # forecast at that interval is 0 -- which makes the summed worst-interval firm_kw this
    # allocation splits by legitimately zero. That is a real, verified property of this
    # fixture (checked directly against `_pipeline.build()` for several seeds), not a
    # coercion bug: `capacity_revenue_eur` stays a real number while the per-site SPLIT of
    # it is correctly undefined (never a fabricated even split, never a fabricated 0.0)
    # when there is no basis to allocate by. Both branches are asserted so this test would
    # catch either direction breaking.
    body = client.get(f"/api/scenario/{result['id']}/map").json()
    firms = [row["firm_kw"] for row in body["sites"]]
    site_revenues = [row["revenue_eur"] for row in body["sites"]]
    if sum(f for f in firms if f) > 0:
        assert all(r is not None for r in site_revenues)
        assert sum(site_revenues) == pytest.approx(EXPECTED_REVENUE_EUR)
    else:
        assert all(f == 0.0 for f in firms), firms
        assert all(r is None for r in site_revenues), (
            "capacity_revenue_eur is real but every site's worst-interval firm_kw is 0; "
            "the allocation has nothing to split by and must stay null, not become a "
            "fabricated 0.0 or an invented even split"
        )

    # the /pooling route: the pipeline's own conversion of the curve's rows (ints become
    # JSON floats via `_f`, never left as numpy scalars)
    pooling = client.get(f"/api/scenario/{result['id']}/pooling").json()
    assert pooling["diversification_curve"] == [
        {"n_sites": 1.0, "firm_kw_per_site": 100.0, "shortfall_rate": 0.05},
        {"n_sites": 2.0, "firm_kw_per_site": 200.0, "shortfall_rate": 0.05},
    ]


def test_pool_unavailable_still_nulls_rather_than_zeroes(client):
    """The other end of the pin, with NO stub active: this is the existing
    `test_an_unavailable_upstream_function_nulls_its_figure_rather_than_zeroing_it`
    invariant, re-asserted here beside the stubbed test above so the two ends of "zero and
    unknown must never look the same" sit next to each other in the same module."""
    result = warm(client, FEASIBLE)
    assert result["totals"]["pool_firm_mw"] is None
    assert result["totals"]["peakers_displaced"] is None
    assert result["totals"]["capacity_revenue_eur"] is None
    body = client.get(f"/api/scenario/{result['id']}/map").json()
    assert all(row["revenue_eur"] is None for row in body["sites"])


# ---------------------------------------------------------------------------
# the seam bug found while signature-checking, now fixed: a MarketError from
# diversification_curve() (or pool()) degrades to a null figure, never a 500
# ---------------------------------------------------------------------------


def test_a_documented_diversification_curve_failure_degrades_to_null_not_a_500(
    client, monkeypatch
):
    """`market.diversification_curve()` (merged in #33, `origin/main`) unconditionally
    raises `MarketError` when `realised` is not supplied, and has no default that avoids
    it -- "`realised` has no default" is in its own docstring, because a shortfall rate
    derived from the same distribution that produced the promise would be tautological.
    This repo has no measured realised load anywhere (`data/raw/` is empty), so the real
    function always refuses on this project's data, on every scenario, forever.

    `_pipeline._pooling()` previously did not catch that: it called
    `curve_fn(firm, sizes=sizes, seed=spec.seed)` uncaught, so the instant #33 merged,
    every `POST /api/scenario` failed with a `scenario_failed` 500 -- confirmed empirically
    against the real, merged `src/market` (58/105 tests failing with `MarketError: cann...`
    before this fix; a disposable worktree combining `origin/main`'s `src/market` with this
    lane's code is required to see it, because `claim/28` alone branches from before #33 and
    has no real `pool`/`diversification_curve` to fail against).

    `_pipeline._pooling()` now catches `market.MarketError` around the `curve_fn(...)` call
    exactly the way it already treats `curve_fn is None` a few lines above: a displayable,
    attributed warning naming `diversification_curve`, an EMPTY curve (never fabricated,
    never zero-filled), and the scenario still succeeds. This test pins the fixed contract.

    It deliberately does NOT fix this by having `_pipeline.py` fabricate a `realised` frame
    to satisfy the call -- `_fake_diversification_curve_faithful` still raises
    `AssertionError("unreachable")` if it is ever called with a non-`None` `realised`, so if
    a future change starts passing a manufactured one instead of degrading gracefully, this
    test fails loudly (a 500 from that `AssertionError`) rather than silently starting to
    assert a fabricated number.
    """
    monkeypatch.setattr(
        market, "diversification_curve", _fake_diversification_curve_faithful, raising=False
    )

    result = warm(client, BROKEN_CURVE_SPEC)  # 200, not 500 -- the whole point of the fix

    codes = [w["code"] for w in result["warnings"]]
    assert "diversification_curve_failed" in codes
    failure = next(w for w in result["warnings"] if w["code"] == "diversification_curve_failed")
    assert failure["lane"] == "src/market"
    assert "diversification_curve" in failure["message"]
    assert "realised" in failure["detail"]["detail_text"]

    # the empty-curve half of "zero and unknown must never look the same": /pooling shows
    # an empty list, not a zero-filled or fabricated one
    pooling = client.get(f"/api/scenario/{result['id']}/pooling").json()
    assert pooling["diversification_curve"] == []


def test_pool_failure_degrades_to_null_not_a_500(client, monkeypatch):
    """`pool()`'s own twin failure mode: it raises `MarketError` (not `curve_fn`'s) when a
    site has zero residual variation in its firm-capacity time series -- the real error
    the coordinator's empirical run against the merged market actually saw
    (`"cannot measure correlation: site(s) ... have zero residual variation"`,
    `src/market/api.py:451` on `origin/main`). `_pipeline._pooling()` must not fix only the
    `diversification_curve` twin and leave this one to take the whole scenario down; both
    are caught the same way.
    """

    def raising_pool(firm, *, method, sites=None, seed=0, correlation=None, n_draws=None):
        raise market.MarketError(
            "cannot measure correlation: site(s) ['BY-80331-aaaa0001'] have zero residual "
            "variation, so no dependence structure could be measured"
        )

    monkeypatch.setattr(market, "pool", raising_pool, raising=False)

    result = warm(client, {**FEASIBLE, "seed": 90212})  # 200, not a 500

    assert result["totals"]["pool_firm_mw"] is None
    assert result["totals"]["peakers_displaced"] is None
    assert result["totals"]["capacity_revenue_eur"] is None
    codes = [w["code"] for w in result["warnings"]]
    assert "pool_failed" in codes
    failure = next(w for w in result["warnings"] if w["code"] == "pool_failed")
    assert failure["lane"] == "src/market"
    assert "residual variation" in failure["detail"]["detail_text"]
    body = client.get(f"/api/scenario/{result['id']}/map").json()
    assert all(row["revenue_eur"] is None for row in body["sites"])
