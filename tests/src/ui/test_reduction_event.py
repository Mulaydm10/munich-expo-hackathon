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
