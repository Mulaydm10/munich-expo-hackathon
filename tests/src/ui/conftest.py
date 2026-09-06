"""Recorded `src/service` payloads, and the per-screen contexts built from them.

No test in this lane touches a running service: `src/service` is unmerged (issue #28)
and `contracts/src/service.md` is frozen precisely so this lane can be written and
verified against the shapes rather than the implementation. Every fixture under
`fixtures/` is a hand-written recording of one route's response.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load(name: str) -> Any:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


@pytest.fixture
def sites() -> list[dict]:
    return load("sites")


@pytest.fixture
def intervals() -> list[dict]:
    return load("timeseries")


@pytest.fixture
def dispatch() -> dict:
    return load("dispatch")


@pytest.fixture
def amended() -> list[dict]:
    return load("amended")


@pytest.fixture
def reduction_event() -> dict:
    """The `ReductionEvent` the service tells the call screen to POST.

    Field names are `src/sched/api.py::ReductionEvent`'s: call_t, notice_min,
    duration_min, reduction_kw. Values line up with `dispatch.json`'s event window
    (16:00-20:00Z is 240 min) so the two fixtures describe one story.
    """
    return load("reduction_event")


@pytest.fixture
def curve() -> list[dict]:
    return load("pooling")


@pytest.fixture
def ledger() -> dict:
    return load("ledger")


@pytest.fixture
def warnings() -> list:
    return load("warnings")


@pytest.fixture
def error() -> dict:
    return load("error")


def contexts() -> dict[str, dict]:
    """One happy-path context per screen, keyed by screen name.

    A plain function rather than a fixture so it can also be used at collection time
    for `@pytest.mark.parametrize`.
    """
    ledger_payload = load("ledger")
    return {
        "map": {"sites": load("sites")},
        "day": {"intervals": load("timeseries"), "stream_url": "/api/scenario/abc/stream"},
        "call": {
            "dispatch": load("dispatch"),
            "amended_intervals": load("amended"),
            "dispatch_url": "/api/scenario/abc/dispatch",
            "reduction_event": load("reduction_event"),
        },
        "pooling": {"curve": load("pooling"), "pool_method": "empirical"},
        "ledger": dict(ledger_payload),
    }


@pytest.fixture
def screen_contexts() -> dict[str, dict]:
    return contexts()
