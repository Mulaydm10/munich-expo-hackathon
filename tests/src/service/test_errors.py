"""Errors are JSON `{error, detail, how_to_fix}` at the real HTTP status.

"never a 200 with an empty body" is the guarantee, so every case here asserts three
things at once: the status is not 200, the body is not empty, and all three keys are
present and non-empty. A caller that only checked `response.ok` would be misled by any
of the three failing.
"""

from __future__ import annotations

import time

import pytest

from src.data import api as data
from src.service import _pipeline as pipeline

from .conftest import DAY, FEASIBLE, build_root, prices_frame, span_index, warm, weather_frame, write_table

ERROR_KEYS = {"error", "detail", "how_to_fix"}


def assert_error_shape(response, *, status: int | None = None) -> dict:
    assert response.status_code != 200, "a broken request answered 200"
    if status is not None:
        assert response.status_code == status, response.text
    assert response.content, "an error answered with an empty body"
    body = response.json()
    assert ERROR_KEYS <= set(body), body
    assert all(isinstance(body[k], str) and body[k].strip() for k in ERROR_KEYS), body
    return body


# ---------------------------------------------------------------------------
# a bad spec
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body,because",
    [
        ({"date": "not-a-date", "n_sites": 2}, "date is not an ISO calendar date"),
        ({"date": DAY}, "no portfolio selector at all"),
        ({"date": DAY, "n_sites": 2, "region": "BY"}, "two portfolio selectors"),
        ({"date": DAY, "n_sites": 0}, "n_sites is not a portfolio"),
        ({"date": DAY, "n_sites": 2, "tau": 0.123}, "tau has no fitted quantile behind it"),
        ({"date": DAY, "n_sites": 2, "product": "FCR"}, "unknown product"),
        ({"date": DAY, "n_sites": 2, "policy": "wishful"}, "unknown policy"),
        ({"date": DAY, "n_sites": 2, "pool_method": "vibes"}, "unknown pool_method"),
        ({"date": DAY, "n_sites": 2, "typo_field": 1}, "an unknown field would not enter the hash"),
        ({"date": DAY, "site_ids": "BY-80331-aaaa0001"}, "site_ids must be a list"),
        ([DAY, 2], "the body is not a JSON object"),
    ],
)
def test_a_bad_spec_is_a_400_with_all_three_error_fields(client, body, because):
    response = client.post("/api/scenario", json=body)
    payload = assert_error_shape(response, status=400)
    assert payload["error"] == "bad_spec", because


def test_a_body_that_is_not_json_is_a_400(client):
    response = client.post(
        "/api/scenario", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert_error_shape(response, status=400)


def test_an_unknown_site_id_is_rejected_rather_than_silently_shrinking_the_portfolio(client):
    """A silently smaller portfolio would understate every total in the scenario."""
    body = {"date": DAY, "site_ids": ["BY-80331-aaaa0001", "XX-00000-deadbeef"], "seed": 7}
    response = client.post("/api/scenario", json=body)
    deadline = time.monotonic() + 30
    while response.status_code == 202 and time.monotonic() < deadline:
        time.sleep(0.02)
        response = client.post("/api/scenario", json=body)
    payload = assert_error_shape(response, status=400)
    assert "XX-00000-deadbeef" in payload["detail"]


# ---------------------------------------------------------------------------
# missing data, missing scenario
# ---------------------------------------------------------------------------


def test_a_missing_canonical_table_is_a_503_naming_how_to_build_it(client_on, tmp_path):
    root = tmp_path / "no-sites"
    root.mkdir()
    write_table(root, "prices", prices_frame(span_index()))
    write_table(root, "weather", weather_frame(span_index()))
    client = client_on(root)

    sites = assert_error_shape(client.get("/api/sites"), status=503)
    assert sites["error"] == "missing_data"
    assert "canonicalise" in sites["how_to_fix"] or "fetch" in sites["how_to_fix"]

    response = client.post("/api/scenario", json=FEASIBLE)
    deadline = time.monotonic() + 30
    while response.status_code == 202 and time.monotonic() < deadline:
        time.sleep(0.02)
        response = client.post("/api/scenario", json=FEASIBLE)
    scenario = assert_error_shape(response, status=503)
    assert scenario["error"] == "missing_data"


@pytest.mark.parametrize("route", ["", "/timeseries", "/pooling", "/map", "/stream"])
def test_an_unknown_scenario_id_is_a_404_with_the_error_shape(client, route):
    body = assert_error_shape(client.get(f"/api/scenario/0000000000000000{route}"), status=404)
    assert body["error"] == "scenario_not_found"


def test_dispatching_against_an_unknown_scenario_is_a_404(client):
    response = client.post(
        "/api/scenario/0000000000000000/dispatch",
        json={"call_t": f"{DAY}T18:00:00+00:00", "notice_min": 5, "duration_min": 60,
              "reduction_kw": 1.0},
    )
    assert_error_shape(response, status=404)


# ---------------------------------------------------------------------------
# a bug still answers in the contract's shape
# ---------------------------------------------------------------------------


def test_an_unexpected_exception_answers_500_in_the_contract_shape(client_on, full_root,
                                                                   monkeypatch):
    """"Errors are JSON {error, detail, how_to_fix} with the real HTTP status" holds for
    a bug too -- a 500 with an empty body is what the UI cannot distinguish from a crash.
    """
    client = client_on(full_root, raise_server_exceptions=False)

    def explode(*args, **kwargs):
        raise RuntimeError("the sites table caught fire")

    monkeypatch.setattr(data, "load", explode)
    body = assert_error_shape(client.get("/api/sites"), status=500)
    assert body["error"] == "internal_error"
    assert "RuntimeError" in body["detail"]


def test_a_pipeline_bug_surfaces_as_a_500_error_document_not_a_202_forever(
    client_on, tmp_path, monkeypatch
):
    root = build_root(tmp_path / "bug")
    client = client_on(root)

    def explode(*args, **kwargs):
        raise RuntimeError("a lane this scenario composes is broken")

    monkeypatch.setattr(pipeline, "build", explode)
    body = {**FEASIBLE, "seed": 555}
    response = client.post("/api/scenario", json=body)
    deadline = time.monotonic() + 30
    while response.status_code == 202 and time.monotonic() < deadline:
        time.sleep(0.02)
        response = client.post("/api/scenario", json=body)
    payload = assert_error_shape(response, status=500)
    assert payload["error"] == "scenario_failed"
    assert "RuntimeError" in payload["detail"]


# ---------------------------------------------------------------------------
# the query parameters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [{"bbox": "11.0,48.0"}, {"bbox": "a,b,c,d"}, {"limit": 0}],
)
def test_a_malformed_sites_query_is_a_400(client, params):
    assert_error_shape(client.get("/api/sites", params=params), status=400)


def test_a_negative_stream_interval_is_a_400(client):
    result = warm(client, FEASIBLE)
    response = client.get(
        f"/api/scenario/{result['id']}/stream", params={"interval_ms": -1}
    )
    assert_error_shape(response, status=400)
