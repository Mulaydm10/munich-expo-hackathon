"""`default_reduction_event()` and `reduction_event_valid()` (issue #40 findings 1 & 2).

Finding 1: nothing in production ever supplied `reduction_event`, so the call screen's
dispatch button was permanently disabled -- the only supplier anywhere in the repo was
`fixtures/reduction_event.json`. `default_reduction_event()` builds one from the day's
own timeseries so the button can go live without this lane inventing a number.

Finding 2: `event_ok` (before this PR) checked only that four keys existed and were
non-None, so a string, a bool, a naive timestamp or a negative duration all enabled the
button and were POSTed verbatim. `reduction_event_valid()` checks the real wire shape.

Every wire-shape rule asserted here mirrors the design owner's read of `src/service`'s
actual dispatch validator (`_event_from_body`) and of `src.sched.dispatch`'s own guards
-- see `src/ui/api.py::reduction_event_valid`'s docstring. This lane does not import
`src/service` (it is unmerged), so these are recorded rules, not an integration test.
"""

from __future__ import annotations

import copy
import json
import math

import pytest

from src.ui import api

from .conftest import FIXTURE_DIR

TIMESERIES = json.loads((FIXTURE_DIR / "timeseries.json").read_text())


# --- default_reduction_event(): construction ----------------------------------------


def test_default_event_is_built_entirely_from_the_days_own_timeseries() -> None:
    """Every field traces to `timeseries.json`, not an invented constant:

    - call_t is the 22:00Z row's own timestamp -- the highest `price_eur_mwh` (41.2)
      among all rows except the last (which has no room for a window after it).
    - notice_min is the native 15-minute resolution constant.
    - duration_min is exactly the gap from call_t to the day's last recorded interval
      (03:00Z), so the compliance window reaches the end of the day and no further.
    - reduction_kw is the MINIMUM firm_kw across every row after the call (96.0, not
      120.0 -- an average or a later-favourable value would overstate what this site can
      actually promise for the whole window).
    """
    event = api.default_reduction_event(TIMESERIES)
    assert event == {
        "call_t": "2026-10-24T22:00:00+00:00",
        "notice_min": 15.0,
        "duration_min": 300.0,
        "reduction_kw": 96.0,
    }


def test_default_event_is_itself_valid() -> None:
    """The cross-lane check: what this lane constructs must survive its own validity
    gate (and, per the design owner's read of `_event_from_body`, the service's)."""
    event = api.default_reduction_event(TIMESERIES)
    assert api.reduction_event_valid(event) is True


@pytest.mark.parametrize("bad_intervals", [None, [], "not a list", {"t": "x"}, [1, 2, 3]])
def test_default_event_is_none_for_unusable_input(bad_intervals) -> None:
    assert api.default_reduction_event(bad_intervals) is None


def test_default_event_is_none_with_only_one_usable_row() -> None:
    """A duration needs two distinct, valid timestamps; one row leaves no interval to
    build a compliance window against."""
    assert api.default_reduction_event(TIMESERIES[:1]) is None


def test_default_event_skips_a_row_with_a_naive_timestamp() -> None:
    """Coercion pinned at the end that AVOIDS it (a full valid fixture) and the end that
    FORCES it (one row's `t` loses its UTC offset): a naive timestamp is a service bug
    (contracts/CONVENTIONS.md), not a UTC time the wire happened to omit the offset on,
    and must not silently participate in choosing call_t or the window."""
    naive = copy.deepcopy(TIMESERIES)
    naive[0]["t"] = "2026-10-24T22:00:00"  # was the highest-price row; now naive
    event = api.default_reduction_event(naive)
    assert event is not None
    # With the highest-price row dropped, the next-highest valid row (22:15Z, 39.75)
    # among the remaining candidates becomes call_t.
    assert event["call_t"] == "2026-10-24T22:15:00+00:00"


def test_default_event_skips_a_row_with_a_negative_firm_kw() -> None:
    """A negative capacity is physically meaningless and must not win the min()."""
    mutated = copy.deepcopy(TIMESERIES)
    mutated[-1]["firm_kw"] = -50.0
    event = api.default_reduction_event(mutated)
    assert event is not None
    # The mutated last row is dropped entirely (not just its firm_kw), so the new last
    # usable row is 02:00Z and the window/reduction are recomputed against it.
    assert event["call_t"] == "2026-10-24T22:00:00+00:00"
    assert event["duration_min"] == pytest.approx(240.0)
    assert event["reduction_kw"] == 96.0


def test_default_event_skips_a_row_with_a_non_finite_price() -> None:
    mutated = copy.deepcopy(TIMESERIES)
    mutated[0]["price_eur_mwh"] = float("nan")
    event = api.default_reduction_event(mutated)
    assert event is not None
    assert event["call_t"] == "2026-10-24T22:15:00+00:00"


def test_default_event_dedupes_a_repeated_timestamp() -> None:
    """Two rows sharing one `t` must not be treated as two distinct intervals -- the
    first occurrence wins and the duplicate is dropped."""
    dup = copy.deepcopy(TIMESERIES) + [dict(TIMESERIES[-1])]
    event = api.default_reduction_event(dup)
    assert event == api.default_reduction_event(TIMESERIES)


def test_default_event_call_t_is_never_the_last_interval() -> None:
    """A call at the day's last interval would leave no room for a compliance window
    after it -- excluded by construction, checked here against a fixture engineered so
    the LAST row has the highest price."""
    engineered = copy.deepcopy(TIMESERIES)
    engineered[-1]["price_eur_mwh"] = 999.0
    event = api.default_reduction_event(engineered)
    assert event is not None
    assert event["call_t"] != engineered[-1]["t"]
    # The next-highest price among the non-last rows (22:00Z, 41.2) is used instead.
    assert event["call_t"] == "2026-10-24T22:00:00+00:00"


def test_default_event_handles_the_dst_fallback_day() -> None:
    """`timeseries.json` already spans 2026-10-25's DST fallback (CONVENTIONS.md names
    this date explicitly); the duration arithmetic must use real elapsed UTC minutes,
    not a naive wall-clock subtraction that would be off by 60 on this day in Berlin."""
    event = api.default_reduction_event(TIMESERIES)
    # 22:00Z -> 03:00Z next day is unambiguously 5 hours of UTC time regardless of the
    # Berlin wall clock repeating 02:00-03:00 local that night.
    assert event["duration_min"] == 300.0


# --- reduction_event_valid(): the validity gate -------------------------------------

VALID_EVENT = {
    "call_t": "2026-10-25T16:00:00+00:00",
    "notice_min": 15.0,
    "duration_min": 240.0,
    "reduction_kw": 1000.0,
}


def test_a_well_formed_event_is_valid() -> None:
    assert api.reduction_event_valid(VALID_EVENT) is True


@pytest.mark.parametrize(
    "mutation, description",
    [
        ({"call_t": "2026-10-25T16:00:00"}, "naive call_t"),
        ({"call_t": 1234567890}, "call_t not a string"),
        ({"call_t": "not a timestamp"}, "unparseable call_t"),
        ({"notice_min": True}, "notice_min is a bool"),
        ({"notice_min": "15"}, "notice_min is a numeric string"),
        ({"notice_min": -1.0}, "negative notice_min"),
        ({"notice_min": float("nan")}, "NaN notice_min"),
        ({"notice_min": float("inf")}, "infinite notice_min"),
        ({"duration_min": 0.0}, "zero duration_min"),
        ({"duration_min": -5.0}, "negative duration_min"),
        ({"duration_min": float("nan")}, "NaN duration_min"),
        ({"reduction_kw": -1.0}, "negative reduction_kw"),
        ({"reduction_kw": True}, "reduction_kw is a bool"),
        ({"reduction_kw": "1000"}, "reduction_kw is a numeric string"),
    ],
)
def test_each_wire_shape_violation_is_rejected(mutation: dict, description: str) -> None:
    event = {**VALID_EVENT, **mutation}
    assert api.reduction_event_valid(event) is False, description


def test_an_extra_field_is_rejected() -> None:
    """The service 400s on an unknown field ("unknown ReductionEvent field(s)"); the
    button must not be enabled for a payload that is certain to fail that check."""
    event = {**VALID_EVENT, "site_id": "DE-MUC-0001"}
    assert api.reduction_event_valid(event) is False


def test_a_missing_field_is_rejected() -> None:
    for missing in api.REDUCTION_EVENT_KEYS:
        event = {k: v for k, v in VALID_EVENT.items() if k != missing}
        assert api.reduction_event_valid(event) is False, missing


def test_non_mapping_input_is_rejected() -> None:
    assert api.reduction_event_valid(None) is False
    assert api.reduction_event_valid("not a mapping") is False
    assert api.reduction_event_valid([1, 2, 3, 4]) is False


def test_reduction_event_keys_match_what_default_reduction_event_produces() -> None:
    """The two halves of this fix must agree on the wire shape, or a default this lane
    builds could fail its own validity gate."""
    event = api.default_reduction_event(TIMESERIES)
    assert set(event.keys()) == api.REDUCTION_EVENT_KEYS


# --- build_reduction_event_from_input(): the operator-adjust affordance ------------
#
# Issue #40 follow-up: "let the operator adjust it" is part of the requirement, not
# colour. `build_reduction_event_from_input()` turns raw form-field strings into a
# candidate event; `reduction_event_valid()` (already tested above) is the ONLY thing
# that decides whether the result may be POSTed -- this section tests construction, not
# a second copy of the validity rules.

RAW_FORM_INPUT = {
    "call_t": "2026-10-25T16:00:00+00:00",
    "notice_min": "15",
    "duration_min": "240",
    "reduction_kw": "1000",
}


def test_a_full_valid_submission_becomes_a_valid_event() -> None:
    event = api.build_reduction_event_from_input(RAW_FORM_INPUT)
    assert event == {
        "call_t": "2026-10-25T16:00:00+00:00",
        "notice_min": 15.0,
        "duration_min": 240.0,
        "reduction_kw": 1000.0,
    }
    assert api.reduction_event_valid(event) is True


@pytest.mark.parametrize("cleared_key", sorted(api.REDUCTION_EVENT_KEYS))
def test_an_empty_field_is_absent_never_zero(cleared_key: str) -> None:
    """The core of this follow-up's third requirement: a blank box must not become 0
    -- `reduction_kw: 0.0` is a real, valid event that promises nothing, and building
    one because the operator cleared a field would be exactly the silent-coercion
    failure mode this project's own history warns against."""
    raw = {**RAW_FORM_INPUT, cleared_key: ""}
    event = api.build_reduction_event_from_input(raw)
    assert cleared_key not in event
    assert 0.0 not in event.values()
    assert 0 not in event.values()
    # A cleared field makes the whole event incomplete, which reduction_event_valid()
    # must reject for missing-key reasons -- not accept with a fabricated 0.
    assert api.reduction_event_valid(event) is False


def test_a_whitespace_only_field_is_also_treated_as_empty() -> None:
    raw = {**RAW_FORM_INPUT, "reduction_kw": "   "}
    event = api.build_reduction_event_from_input(raw)
    assert "reduction_kw" not in event


def test_a_field_absent_from_the_submission_entirely_is_also_absent_from_the_event() -> None:
    raw = {k: v for k, v in RAW_FORM_INPUT.items() if k != "duration_min"}
    event = api.build_reduction_event_from_input(raw)
    assert "duration_min" not in event


@pytest.mark.parametrize("key", ["notice_min", "duration_min", "reduction_kw"])
def test_a_numeric_string_is_coerced_to_a_real_number(key: str) -> None:
    """Coercion pinned at the end that FORCES it: a numeric string must become a float,
    or reduction_event_valid() (which requires a real int/float) would reject every
    edited field an operator ever types, since a form field is always a string."""
    event = api.build_reduction_event_from_input({**RAW_FORM_INPUT, key: "42.5"})
    assert event[key] == 42.5
    assert isinstance(event[key], float)


@pytest.mark.parametrize("key", ["notice_min", "duration_min", "reduction_kw"])
def test_a_non_numeric_string_is_left_alone_for_the_validator_to_reject(key: str) -> None:
    """Coercion pinned at the end that AVOIDS it: a string that does not parse as a
    number must NOT be silently dropped, defaulted, or forced to a number -- it is kept
    exactly as typed, so `reduction_event_valid()` rejects it for what it is (a string
    where a number is required), rather than this function guessing or discarding it."""
    event = api.build_reduction_event_from_input({**RAW_FORM_INPUT, key: "fifteen"})
    assert event[key] == "fifteen"
    assert api.reduction_event_valid(event) is False


def test_call_t_is_never_parsed_or_reformatted_by_construction() -> None:
    """call_t travels through as the operator's own string, wire-shaped or not --
    this function does not decide whether it is a valid timestamp; the validator does."""
    naive = {**RAW_FORM_INPUT, "call_t": "2026-10-25T16:00:00"}  # no UTC offset
    event = api.build_reduction_event_from_input(naive)
    assert event["call_t"] == "2026-10-25T16:00:00"
    assert api.reduction_event_valid(event) is False  # the validator catches it, not this function


def test_an_unknown_field_in_the_submission_is_dropped() -> None:
    """No stray query parameter, submit-button name, or anything else riding along
    with a form submission may reach the POST body -- only the four ReductionEvent
    keys, or fewer, ever come out of this function."""
    raw = {**RAW_FORM_INPUT, "site_id": "DE-MUC-0001", "submit": "Apply"}
    event = api.build_reduction_event_from_input(raw)
    assert set(event.keys()) <= api.REDUCTION_EVENT_KEYS
    assert "site_id" not in event
    assert "submit" not in event


def test_a_none_value_for_a_field_is_treated_as_absent() -> None:
    event = api.build_reduction_event_from_input({**RAW_FORM_INPUT, "notice_min": None})
    assert "notice_min" not in event


def test_non_mapping_input_produces_an_empty_event() -> None:
    assert api.build_reduction_event_from_input(None) == {}
    assert api.build_reduction_event_from_input("not a mapping") == {}
    assert api.build_reduction_event_from_input([1, 2, 3, 4]) == {}


def test_an_all_empty_submission_produces_an_empty_event_not_a_zeroed_one() -> None:
    event = api.build_reduction_event_from_input({k: "" for k in api.REDUCTION_EVENT_KEYS})
    assert event == {}
    assert api.reduction_event_valid(event) is False
