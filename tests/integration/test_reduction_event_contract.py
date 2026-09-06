"""`src/ui`'s `ReductionEvent` construction vs `src/service`'s wire validator.

`contracts/src/sched.md` (ratified 2026-09-06, #52) now names the four `ReductionEvent`
fields and the exact rules a caller must satisfy. Three lanes read that shape
independently: `src/sched` defines the dataclass, `src/service._event_from_body` is the
wire gate that decides whether a POST body may reach `sched.dispatch`, and `src/ui`
constructs a candidate event (`default_reduction_event`, `build_reduction_event_from_input`)
and decides -- separately, in Python it owns -- whether to enable the dispatch button
(`reduction_event_valid`). `contracts/src/ui.md`'s own docstring says the design owner
"read src/service's actual wire validator ... and confirmed" the two agree; that reading
was correct as of when it was written, but nothing re-checks it on every change to either
lane, and lane confinement (`tests/src/ui/` may not import `src.service`;
`tests/src/service/` has no reason to import `src.ui`) means no lane-confined suite can.
That is exactly this tier's job.

Two lanes drifting on "is this event valid" is silent and asymmetric: `src/ui` enabling
the dispatch button for an event `src/service` then rejects reads to a judge as the demo
button firing a 400 on stage; `src/ui` disabling it for an event the service would
actually accept reads as a stricter product than the one built.
"""

from __future__ import annotations

import pytest

from src.service._errors import BadSpec
from src.sched.api import ReductionEvent
from src.ui import api as ui

from .conftest import FEASIBLE_SPEC, warm

# `_event_from_body` is imported lazily inside fixtures/tests below via `src.service.api`
# so that importing this module never requires the FastAPI app to be constructed at
# collection time -- it is still exactly the function `src/service`'s dispatch route
# calls, not a re-implementation.

BASE_VALID = {
    "call_t": "2026-03-04T17:00:00+00:00",
    "notice_min": 15.0,
    "duration_min": 60.0,
    "reduction_kw": 100.0,
}

# name -> (event, expected validity). Every non-valid case is a known-bad shape named in
# the task: a naive call_t, an extra field, a missing field, a bool where a number is
# required, a numeric string, duration_min == 0, and a negative notice_min.
CASES: dict[str, tuple[dict, bool]] = {
    "valid_default": (dict(BASE_VALID), True),
    "naive_call_t": ({**BASE_VALID, "call_t": "2026-03-04T17:00:00"}, False),
    "extra_field": ({**BASE_VALID, "extra_field": 1.0}, False),
    "missing_field": (
        {k: v for k, v in BASE_VALID.items() if k != "duration_min"},
        False,
    ),
    "bool_for_number": ({**BASE_VALID, "notice_min": True}, False),
    "numeric_string": ({**BASE_VALID, "reduction_kw": "100"}, False),
    "duration_zero": ({**BASE_VALID, "duration_min": 0}, False),
    "negative_notice": ({**BASE_VALID, "notice_min": -5.0}, False),
}


def _service_says_valid(event_from_body, body: dict) -> bool:
    """True iff `src.service.api._event_from_body` accepts `body` -- the real wire
    validator, not a re-implementation of its rules."""
    try:
        event_from_body(body)
    except BadSpec:
        return False
    return True


@pytest.mark.parametrize("name", sorted(CASES))
def test_ui_gate_agrees_with_service_validator(name):
    """For every case, `src/ui.reduction_event_valid()` and
    `src/service.api._event_from_body()` must agree on whether the event is usable.

    Each case is asserted against its OWN documented expectation first (so a bug in this
    test's table, not just a bug in the two lanes, would be caught), and only then
    cross-checked between the two lanes -- agreeing on the wrong answer is still a
    disagreement with the contract, so both directions are checked independently.
    """
    from src.service.api import _event_from_body

    body, expected_valid = CASES[name]

    ui_valid = ui.reduction_event_valid(body)
    assert ui_valid is expected_valid, (
        f"{name}: src.ui.reduction_event_valid() returned {ui_valid}, expected "
        f"{expected_valid} per contracts/src/sched.md's ReductionEvent rules"
    )

    if expected_valid:
        event = _event_from_body(body)
        assert isinstance(event, ReductionEvent)
        service_valid = True
    else:
        with pytest.raises(BadSpec):
            _event_from_body(body)
        service_valid = False

    assert ui_valid == service_valid, (
        f"{name}: src.ui says valid={ui_valid}, src.service's real wire validator says "
        f"valid={service_valid} -- the dispatch button and the route it POSTs to disagree"
    )


def test_gate_agreement_is_not_vacuous():
    """Guard against a probe that tests nothing: if a bug (in either validator, or in
    this test) made every case read as valid, `test_ui_gate_agrees_with_service_validator`
    would pass on every parameter without ever exercising a rejection path. This test
    fails loudly if that happens, by asserting the CASES table itself contains both
    outcomes and that both real validators are capable of returning both outcomes across
    the table -- i.e. the validator was actually entered and its rejection branches
    actually ran, not skipped by an exception unrelated to validity or a body that never
    reached the checks.
    """
    from src.service.api import _event_from_body

    assert any(expected for _, expected in CASES.values()), "table has no valid case"
    assert any(not expected for _, expected in CASES.values()), "table has no invalid case"

    ui_results = {name: ui.reduction_event_valid(body) for name, (body, _) in CASES.items()}
    assert any(ui_results.values()), "src.ui.reduction_event_valid() rejected every case"
    assert not all(ui_results.values()), "src.ui.reduction_event_valid() accepted every case"

    def service_result(body: dict) -> bool:
        try:
            _event_from_body(body)
            return True
        except BadSpec:
            return False

    service_results = {name: service_result(body) for name, (body, _) in CASES.items()}
    assert any(service_results.values()), "_event_from_body() rejected every case"
    assert not all(service_results.values()), "_event_from_body() accepted every case"


def test_ui_default_event_survives_service_validator_verbatim(client):
    """The end-to-end version of the same claim, using a REAL scenario's own timeseries
    (not a hand-built fixture row): `src.ui.default_reduction_event()` is fed the actual
    `/timeseries` rows a running `src/service` produced, and the event it builds must
    survive `src.service.api._event_from_body` byte-for-byte -- same `call_t` (once
    parsed to the same instant), same `notice_min`, `duration_min`, `reduction_kw`. A
    lane rewriting a field name or rounding a number on the way through would show up
    here as a mismatch, not as a validator disagreeing about validity.
    """
    from src.service.api import _event_from_body

    result = warm(client, FEASIBLE_SPEC)
    assert result.status_code == 200, result.text
    scenario_id = result.json()["id"]

    ts = client.get(f"/api/scenario/{scenario_id}/timeseries")
    assert ts.status_code == 200, ts.text
    rows = ts.json()["rows"]
    assert len(rows) >= 2, "need at least two intervals for default_reduction_event()"

    event_dict = ui.default_reduction_event(rows)
    assert event_dict is not None, (
        "default_reduction_event() returned None for a real scenario's own timeseries -- "
        "the day fixture must supply finite price_eur_mwh/firm_kw on at least two rows"
    )
    assert ui.reduction_event_valid(event_dict) is True

    parsed = _event_from_body(event_dict)
    assert isinstance(parsed, ReductionEvent)
    assert parsed.notice_min == pytest.approx(event_dict["notice_min"])
    assert parsed.duration_min == pytest.approx(event_dict["duration_min"])
    assert parsed.reduction_kw == pytest.approx(event_dict["reduction_kw"])

    import pandas as pd

    expected_call_t = pd.Timestamp(event_dict["call_t"]).tz_convert("UTC").to_pydatetime()
    assert parsed.call_t == expected_call_t
