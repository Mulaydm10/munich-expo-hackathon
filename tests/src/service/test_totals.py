"""What the headline numbers are allowed to be made of.

`contracts/CONVENTIONS.md`: "derive a metric from measured state ... never from the search
parameter, feasibility flag or intermediate that led to the state". Issue #28 adds a
specific instruction for this lane: `src/sched`'s `reduction_kw_achieved` is a
per-`(site, t)` worst-interval floor and must never be summed or averaged into a headline
in `ScenarioResult`.
"""

from __future__ import annotations

import json

import pytest

from src.forecast import api as forecast
from src.market import api as market

from .conftest import FEASIBLE, WITH_INFEASIBLE_SITE, day_index, sites_frame, warm

INTERVAL_H = 0.25  # the native 15-minute grid (contracts/CONVENTIONS.md)


def rows_of(client, scenario_id):
    return client.get(f"/api/scenario/{scenario_id}/timeseries").json()["rows"]


def test_scheduling_moves_energy_it_never_destroys_it(client):
    """The physical invariant behind every comparison in `totals`.

    Both curves must cover the same charging sessions, so their energy over the day must
    match to the last watt-hour. If a session were quietly dropped from the optimised
    side, every peak and cost figure would improve for free -- which is exactly how this
    scenario used to read before the two curves were made like-for-like.
    """
    for body in (FEASIBLE, WITH_INFEASIBLE_SITE):
        result = warm(client, body)
        rows = rows_of(client, result["id"])
        baseline_kwh = sum(row["load_kw_baseline"] for row in rows) * INTERVAL_H
        optimised_kwh = sum(row["load_kw_optimised"] for row in rows) * INTERVAL_H
        assert baseline_kwh > 0
        assert optimised_kwh == pytest.approx(baseline_kwh, rel=1e-9)


def test_the_share_of_energy_the_optimiser_never_placed_is_on_the_wire(client):
    """The number that says how much of the "optimised" curve was optimised at all.

    It is a genuine measurement rather than a caveat: it moves with the portfolio (the
    depot site's overnight sessions straddle the day boundary and are never offered to
    the solver, so adding sites moves the share), and it is arithmetically the two
    energies it reports.
    """
    shares = {}
    for label, body in (("two-site", FEASIBLE), ("three-site", WITH_INFEASIBLE_SITE)):
        result = warm(client, body)
        detail = [
            w["detail"] for w in result["warnings"] if w["code"] == "energy_not_optimised"
        ]
        assert detail, f"{label}: the unoptimised share was not reported"
        reported = detail[0]
        assert reported["rate"] == pytest.approx(
            reported["residual_kwh"] / reported["baseline_kwh"]
        )
        rows = rows_of(client, result["id"])
        assert reported["baseline_kwh"] == pytest.approx(
            sum(row["load_kw_baseline"] for row in rows) * INTERVAL_H
        )
        shares[label] = reported["rate"]
    assert shares["two-site"] != shares["three-site"]
    assert all(0.0 < share < 1.0 for share in shares.values())


def test_no_headline_is_derived_from_sched_s_per_site_floor(client):
    """`reduction_kw_achieved` is a per-(site, t) worst-interval floor, not a portfolio
    total (issue #28, and design flagged it for this lane specifically). It may appear on
    the dispatch route, beside the physical measurement -- never inside a
    `ScenarioResult`."""
    result = warm(client, WITH_INFEASIBLE_SITE)
    serialised = json.dumps(result)
    assert "reduction_kw_achieved" not in serialised
    assert "reduction_shortfall_kw" not in serialised
    for route in ("/timeseries", "/pooling", "/map"):
        body = client.get(f"/api/scenario/{result['id']}{route}").text
        assert "reduction_kw_achieved" not in body


def test_optimising_never_costs_more_than_the_baseline_it_replaces(client):
    """`src/sched` minimises energy cost, so the optimised policy must not price above
    the baseline policy on the same day and the same portfolio."""
    baseline = warm(client, {**FEASIBLE, "policy": "baseline"})
    optimised = warm(client, {**FEASIBLE, "policy": "optimised"})
    assert baseline["id"] != optimised["id"]
    assert optimised["totals"]["energy_cost_eur"] <= baseline["totals"]["energy_cost_eur"]
    # the baseline policy cannot save carbon against itself, and says so with a 0.0 it
    # actually computed rather than a null
    assert baseline["totals"]["co2_kg_saved"] == 0.0
    assert optimised["totals"]["co2_kg_saved"] != 0.0


def test_peaks_agree_with_the_curve_the_ui_draws(client):
    result = warm(client, WITH_INFEASIBLE_SITE)
    rows = rows_of(client, result["id"])
    assert result["totals"]["peak_kw_baseline"] == pytest.approx(
        max(row["load_kw_baseline"] for row in rows)
    )
    assert result["totals"]["peak_kw_optimised"] == pytest.approx(
        max(row["load_kw_optimised"] for row in rows)
    )


def test_calibration_covers_exactly_the_fitted_quantiles(client):
    """A coverage figure for a quantile nobody fitted would be an invented number."""
    result = warm(client, FEASIBLE)
    assert set(result["calibration"]) == {f"{tau:g}" for tau in forecast.QUANTILES}
    assert all(0.0 <= value <= 1.0 for value in result["calibration"].values())


def test_the_scorecard_is_sched_s_own_output(client):
    result = warm(client, FEASIBLE)
    assert set(result["scorecard"]) == {
        "unmet_kwh",
        "deadline_misses",
        "envelope_violation_kwh",
        "floor_shortfall_kw_min",
        "peak_kw",
        "energy_cost_eur",
    }
    # this portfolio schedules, so the solver delivered every kWh it was given
    assert result["scorecard"]["unmet_kwh"] == 0.0
    assert result["scorecard"]["envelope_violation_kwh"] == 0.0


def test_the_map_allocates_revenue_by_a_rule_it_states(client):
    result = warm(client, WITH_INFEASIBLE_SITE)
    body = client.get(f"/api/scenario/{result['id']}/map").json()
    rated = {row["site_id"]: row["rated_power_kw"] for row in sites_frame(3).to_dict("records")}
    for row in body["sites"]:
        # a site cannot firmly promise more reduction than it can physically draw
        assert row["firm_kw"] <= rated[row["site_id"]] + 1e-9
        # no capacity revenue could be priced (market.pool is unimplemented), so the
        # per-site share is null rather than 0.0
        assert row["revenue_eur"] is None
    assert result["totals"]["capacity_revenue_eur"] is None


def test_nothing_on_the_wire_is_nan_or_infinity(client):
    """`NaN` is not valid JSON and a browser would choke on it; "we do not know" is
    `null`, and it must never be rendered as `0.0`."""
    result = warm(client, WITH_INFEASIBLE_SITE)
    for route in ("", "/timeseries", "/pooling", "/map"):
        text = client.get(f"/api/scenario/{result['id']}{route}").text
        assert "NaN" not in text and "Infinity" not in text
        json.loads(text)  # strict: json.loads accepts NaN, so the check above is the guard
    assert result["totals"]["penalty_eur"] is None
    assert result["totals"]["net_eur"] is None


def test_an_unknown_number_is_serialised_as_null_never_as_zero():
    """"we do not know" and "it is zero" must stay different things on the wire, and
    `NaN`/`Infinity` are not valid JSON for a browser to parse."""
    import numpy as np

    from src.service import _cache as cache

    assert cache.jsonable(float("nan")) is None
    assert cache.jsonable(float("inf")) is None
    assert cache.jsonable(np.float64("nan")) is None
    assert cache.jsonable({"unknown": float("nan"), "zero": 0.0}) == {
        "unknown": None,
        "zero": 0.0,
    }
    assert json.loads(cache.dumps({"x": [float("nan"), 1.5]}))["x"] == [None, 1.5]


def test_the_assumption_behind_the_loudest_claim_travels_with_it(client, monkeypatch):
    """`peakers_displaced` is null today (no pooled promise), but when it is not null it
    must carry the assumption it was divided by."""
    result = warm(client, FEASIBLE)
    displaced = result["totals"]["peakers_displaced"]
    if displaced is None:
        assert result["totals"]["pool_firm_mw"] is None
        return
    count, assumption = market.peakers_displaced(result["totals"]["pool_firm_mw"])
    assert displaced["count"] == pytest.approx(count)
    assert displaced["assumption"] == assumption
