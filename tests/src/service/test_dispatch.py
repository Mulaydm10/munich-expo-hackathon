"""`POST /api/scenario/{id}/dispatch`: promised vs delivered, measured not asserted.

The route reports two numbers side by side and never merges them:

* `delivered_reduction_kw_worst_interval` -- measured here as a diff of the committed and
  amended portfolio schedules over the compliance window;
* `sched_reduction_kw_achieved` -- `src/sched`'s own figure, forwarded verbatim. Issue
  #28 is explicit that #27 is open and unfixed, so this lane must surface it next to the
  physical measurement rather than average it into a headline.

The most important test in this module is the one where the two disagree.
"""

from __future__ import annotations

import json
import pandas as pd
import pytest

from src.sched import api as sched
from src.service import _pipeline as pipeline
from src.service import api as service

from .conftest import DAY, FEASIBLE, WITH_INFEASIBLE_SITE, day_index, warm

from .test_errors import assert_error_shape


def an_event(call_t, *, notice_min=0, duration_min=60, reduction_kw=5.0) -> dict:
    return {
        "call_t": call_t,
        "notice_min": notice_min,
        "duration_min": duration_min,
        "reduction_kw": reduction_kw,
    }


def scheduled_load(scenario_id):
    """The portfolio load of the sites `src/sched` actually scheduled, per interval."""
    live = service._LIVE[scenario_id]
    return pipeline._by_t(
        pipeline._schedule_to_load(live.optimised), "load_kw", live.grid_index
    )


# ---------------------------------------------------------------------------
# the shape of the answer
# ---------------------------------------------------------------------------


def test_dispatch_returns_the_amended_timeseries_and_both_measurements(client):
    result = warm(client, FEASIBLE)
    call_t = day_index()[70].isoformat()
    body = client.post(
        f"/api/scenario/{result['id']}/dispatch", json=an_event(call_t)
    ).json()

    assert {
        "promised_reduction_kw",
        "delivered_reduction_kw_worst_interval",
        "delivered_reduction_kw_mean",
        "shortfall_kw",
        "sched_reduction_kw_achieved",
        "rows",
        "warnings",
        "compliance_window",
    } <= set(body)
    assert body["promised_reduction_kw"] == 5.0
    assert len(body["rows"]) == len(day_index())
    for row in body["rows"]:
        assert {"t", "load_kw_committed", "load_kw_amended", "reduction_kw",
                "in_compliance_window"} <= set(row)
        assert row["reduction_kw"] == pytest.approx(
            row["load_kw_committed"] - row["load_kw_amended"], abs=1e-9
        )
    # a 60-minute window on the native 15-minute grid is exactly four intervals
    assert body["compliance_window"]["intervals"] == 4
    assert sum(row["in_compliance_window"] for row in body["rows"]) == 4


def test_the_compliance_window_starts_after_the_notice_period(client):
    result = warm(client, FEASIBLE)
    call_t = day_index()[40]
    body = client.post(
        f"/api/scenario/{result['id']}/dispatch",
        json=an_event(call_t.isoformat(), notice_min=30, duration_min=30),
    ).json()
    window = [row["t"] for row in body["rows"] if row["in_compliance_window"]]
    assert window == [
        (call_t + pd.Timedelta(minutes=30)).isoformat(),
        (call_t + pd.Timedelta(minutes=45)).isoformat(),
    ]
    assert body["compliance_window"]["start"] == window[0]


# ---------------------------------------------------------------------------
# delivered is a diff of two schedules, not a forwarded flag
# ---------------------------------------------------------------------------


def test_delivered_is_measured_from_the_schedules_even_when_sched_claims_otherwise(
    client, monkeypatch
):
    """The regression this project keeps hitting: a metric read off the solver's own
    success flag instead of the physical state.

    `src/sched.dispatch` is replaced by one that sheds a known 4 kW while *claiming*
    12 kW on `.attrs['reduction_kw_achieved']`. The route must report 4 (it diffs the two
    schedules) and still forward the 12 beside it, unmerged.
    """
    result = warm(client, FEASIBLE)
    scenario_id = result["id"]

    # The shed is deliberately uneven across the window: 4 kW in its first interval and
    # 8 kW in the other two. A firm promise is only as good as its worst interval, so the
    # delivered figure must be 4 -- the 6.67 kW mean would flatter the same call by 67%.
    worst_shed_kw, rest_shed_kw = 4.0, 8.0
    load = scheduled_load(scenario_id)
    runs = [
        i
        for i in range(len(load) - 2)
        if (load.iloc[i : i + 3] > rest_shed_kw + 1.0).all()
    ]
    assert runs, "the fixture has no run of scheduled load to shed from"
    call_t = load.index[runs[0]]
    window_end = call_t + pd.Timedelta(minutes=45)

    real_dispatch = sched.dispatch

    def fake_dispatch(schedule, event):
        amended = schedule.copy()
        amended.attrs = dict(schedule.attrs)
        first = amended["t"] == call_t
        rest = (amended["t"] > call_t) & (amended["t"] < window_end)
        amended.loc[first, "power_kw"] = amended.loc[first, "power_kw"] - worst_shed_kw
        amended.loc[rest, "power_kw"] = amended.loc[rest, "power_kw"] - rest_shed_kw
        amended.attrs["reduction_kw_achieved"] = 12.0  # a lie, of exactly the #27 shape
        amended.attrs["reduction_shortfall_kw"] = 0.0
        return amended

    monkeypatch.setattr(sched, "dispatch", fake_dispatch)
    body = client.post(
        f"/api/scenario/{scenario_id}/dispatch",
        json=an_event(call_t.isoformat(), duration_min=45, reduction_kw=12.0),
    ).json()
    monkeypatch.setattr(sched, "dispatch", real_dispatch)

    assert body["delivered_reduction_kw_worst_interval"] == pytest.approx(worst_shed_kw)
    assert body["delivered_reduction_kw_mean"] == pytest.approx(
        (worst_shed_kw + 2 * rest_shed_kw) / 3
    )
    # the shortfall is measured against the worst interval, never against the mean
    assert body["shortfall_kw"] == pytest.approx(12.0 - worst_shed_kw)
    # forwarded, beside the measurement -- never summed or averaged into it
    assert body["sched_reduction_kw_achieved"] == 12.0
    assert "dispatch_under_delivered" in [w["code"] for w in body["warnings"]]


def test_a_partial_call_reports_its_measured_shortfall_and_a_zero_call_is_not(client):
    """The real `src.sched.dispatch` measurement remains visible when a call is partial."""
    result = warm(client, FEASIBLE)
    call_t = day_index()[44].isoformat()

    called = client.post(
        f"/api/scenario/{result['id']}/dispatch", json=an_event(call_t, reduction_kw=5.0)
    ).json()
    delivered = called["delivered_reduction_kw_worst_interval"]
    assert delivered > 0.0
    assert called["shortfall_kw"] == pytest.approx(5.0 - delivered)
    # sched's figure is a per-site minimum, the service's a portfolio sum; they need not agree.
    assert called["sched_reduction_kw_achieved"] >= 0.0
    assert ("dispatch_under_delivered" in [w["code"] for w in called["warnings"]]) == (
        called["shortfall_kw"] > 1e-6
    )

    not_called = client.post(
        f"/api/scenario/{result['id']}/dispatch", json=an_event(call_t, reduction_kw=0.0)
    ).json()
    assert not_called["shortfall_kw"] == 0.0
    assert "dispatch_under_delivered" not in [w["code"] for w in not_called["warnings"]]


def test_the_unscheduled_sites_appear_in_the_rows_but_not_in_the_measurement(client):
    """The service rows still expose the complete portfolio after grid-window clamping."""
    degraded = warm(client, WITH_INFEASIBLE_SITE)
    call_t = day_index()[70]
    body = client.post(
        f"/api/scenario/{degraded['id']}/dispatch", json=an_event(call_t.isoformat())
    ).json()
    committed = {row["t"]: row["load_kw_committed"] for row in body["rows"]}
    scheduled = scheduled_load(degraded["id"])
    assert set(committed) == {t.isoformat() for t in scheduled.index}
    window_start = pd.Timestamp(body["compliance_window"]["start"])
    window_end = window_start + pd.Timedelta(minutes=body["event"]["duration_min"])
    for row in body["rows"]:
        row_t = pd.Timestamp(row["t"])
        if window_start <= row_t < window_end:
            assert row["reduction_kw"] >= -1e-9


# ---------------------------------------------------------------------------
# rejected calls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body,because",
    [
        ({"notice_min": 0, "duration_min": 60, "reduction_kw": 1.0}, "call_t is missing"),
        (an_event(f"{DAY}T12:00:00"), "call_t is naive"),
        (an_event("not-a-time"), "call_t is not a timestamp"),
        (an_event(f"{DAY}T12:00:00+00:00", duration_min=0), "a zero-length window caps nothing"),
        (an_event(f"{DAY}T12:00:00+00:00", notice_min=-5), "negative notice"),
        (an_event(f"{DAY}T12:00:00+00:00", reduction_kw=-1.0), "a negative reduction"),
        ({**an_event(f"{DAY}T12:00:00+00:00"), "site_id": "x"}, "an unknown field"),
        (an_event("2026-06-01T12:00:00+00:00"), "a window outside the scenario day"),
    ],
)
def test_a_malformed_reduction_event_is_a_400(client, body, because):
    result = warm(client, FEASIBLE)
    response = client.post(f"/api/scenario/{result['id']}/dispatch", json=body)
    payload = assert_error_shape(response, status=400)
    assert payload["error"] == "bad_spec", because


def test_dispatching_a_short_dwell_site_remains_a_valid_scenario(client):
    """Fractional grid overlap leaves a short-dwell site dispatchable."""
    body = {"date": DAY, "site_ids": ["BY-80339-bbbb0002"], "seed": 7}
    result = warm(client, body)
    assert result["scorecard"] is not None
    assert "session_energy_clamped_to_grid" not in [w["code"] for w in result["warnings"]]

    response = client.post(
        f"/api/scenario/{result['id']}/dispatch",
        json=an_event(day_index()[70].isoformat()),
    )
    assert response.status_code == 200


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("field", ["notice_min", "duration_min", "reduction_kw"])
def test_a_non_finite_number_is_rejected_rather_than_authorising_a_dispatch(
    client, field, literal
):
    """NaN and Infinity must be refused by name, not by luck.

    `json.loads` accepts these non-standard literals, so they arrive from a real request
    body. Every comparison against NaN is False, so the range guards below the type check
    (`reduction_kw < 0`, `duration_min <= 0`) all *pass* for NaN: the guard reads as
    satisfied because nothing it tests is true, and the value is authorised. That is the
    same failure that once let src/grid's thermal envelope take maximum permission.

    Pinned at both ends: a finite value in the same field still dispatches (below), so
    this cannot be satisfied by rejecting everything.
    """
    result = warm(client, FEASIBLE)
    body = an_event(day_index()[70].isoformat())
    raw = json.dumps(body).replace(f'"{field}": {json.dumps(body[field])}',
                                   f'"{field}": {literal}')
    assert literal in raw, "the literal never reached the payload -- test is vacuous"

    response = client.post(
        f"/api/scenario/{result['id']}/dispatch",
        content=raw,
        headers={"content-type": "application/json"},
    )
    assert_error_shape(response, status=400)
    assert field in response.json()["detail"]


def test_a_finite_value_in_the_same_field_still_dispatches(client):
    """The other end of the coercion: the non-finite guard must not reject real numbers."""
    result = warm(client, FEASIBLE)
    response = client.post(
        f"/api/scenario/{result['id']}/dispatch", json=an_event(day_index()[70].isoformat())
    )
    assert response.status_code == 200
