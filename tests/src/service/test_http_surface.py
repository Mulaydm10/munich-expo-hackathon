"""The frozen HTTP surface: every route in `contracts/src/service.md` answers, with the
documented keys.

`src/ui` (PR #36) and `src/voice` are written against exactly this table, so these tests
are deliberately about *field names and presence*, not about the values: a renamed or
dropped key is a contract change, and it should fail here rather than in another lane's
browser.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict

import pytest

from src.market import api as market
from src.service import _pipeline as pipeline

from .conftest import DAY, FEASIBLE, SECRET_VALUE, day_index, sites_frame, warm

RESULT_KEYS = {"id", "spec", "totals", "scorecard", "calibration", "warnings"}
TOTALS_KEYS = {
    "energy_cost_eur",
    "capacity_revenue_eur",
    "penalty_eur",
    "net_eur",
    "co2_kg_saved",
    "peak_kw_baseline",
    "peak_kw_optimised",
    "pool_firm_mw",
    "peakers_displaced",
}
TIMESERIES_ROW_KEYS = {
    "t",
    "load_kw_baseline",
    "load_kw_optimised",
    "envelope_kw",
    "price_eur_mwh",
    "firm_kw",
}


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


def test_health_names_every_canonical_table_and_counts_the_ones_on_disk(client, full_root):
    body = client.get("/api/health").json()
    assert {"status", "git_sha", "tables"} <= set(body)
    assert set(body["tables"]) == set(pipeline.CANONICAL_TABLES)
    # the row counts are the fixture's own, computed here rather than read back
    assert body["tables"]["sites"] == len(sites_frame(3))
    assert body["tables"]["prices"] == len(day_index()) * 11  # 10 history days + the day
    assert body["status"] == "ok"


def test_health_reports_an_unbuilt_table_as_null_not_zero(client_on, tmp_path):
    """"an absent key and a zero must never be the same thing on the wire" (issue #28)."""
    from .conftest import build_root

    root = build_root(tmp_path / "no-carbon", carbon=False)
    body = client_on(root).get("/api/health").json()
    assert body["tables"]["carbon"] is None
    assert body["tables"]["grid_load"] is None  # never built by any fixture
    assert body["tables"]["prices"] > 0
    assert body["status"] == "ok"  # carbon is not a required table


def test_health_is_degraded_when_a_required_table_is_missing(client_on, tmp_path):
    from .conftest import prices_frame, span_index, weather_frame, write_table

    root = tmp_path / "no-sites"
    root.mkdir()
    write_table(root, "prices", prices_frame(span_index()))
    write_table(root, "weather", weather_frame(span_index()))
    body = client_on(root).get("/api/health").json()
    assert body["tables"]["sites"] is None
    assert body["status"] == "degraded"


def test_health_git_sha_identifies_the_served_commit(client):
    sha = client.get("/api/health").json()["git_sha"]
    assert sha != "unknown", "the service could not tell a judge which commit it is running"
    assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)


# ---------------------------------------------------------------------------
# assumptions
# ---------------------------------------------------------------------------


def test_assumptions_are_verbatim(client):
    """"No reformatting, no rounding -- provenance is the point" (issue #28)."""
    body = client.get("/api/assumptions").json()
    assert body == {key: asdict(value) for key, value in market.ASSUMPTIONS.items()}
    # the note is the provenance, so it must survive whole rather than being trimmed
    peaker = market.ASSUMPTIONS["peaker_plant_capacity_mw"]
    assert body["peaker_plant_capacity_mw"]["note"] == peaker.note
    assert body["peaker_plant_capacity_mw"]["value"] == peaker.value


# ---------------------------------------------------------------------------
# sites
# ---------------------------------------------------------------------------


def test_sites_returns_geometry_profile_and_rated_power(client):
    body = client.get("/api/sites").json()
    assert body["total_sites"] == len(sites_frame(3))
    assert body["returned"] == body["matched"] == len(sites_frame(3))
    for row in body["sites"]:
        assert {"site_id", "lat", "lon", "rated_power_kw", "profile"} <= set(row)
        assert row["profile"] is not None
    assert body["warnings"] == []


def test_sites_bbox_filters_and_limit_is_reported(client):
    """A truncated map must never be mistaken for a complete one."""
    munich = client.get("/api/sites", params={"bbox": "11.0,47.9,12.0,48.4"}).json()
    # two of the three fixture sites are in Munich, the third is in Stuttgart
    in_bbox = [
        s
        for s in sites_frame(3).to_dict("records")
        if 11.0 <= s["lon"] <= 12.0 and 47.9 <= s["lat"] <= 48.4
    ]
    assert munich["matched"] == len(in_bbox) == 2
    assert {s["site_id"] for s in munich["sites"]} == {s["site_id"] for s in in_bbox}

    truncated = client.get("/api/sites", params={"limit": 1}).json()
    assert truncated["returned"] == 1
    assert truncated["matched"] == 3
    codes = [w["code"] for w in truncated["warnings"]]
    assert "sites_truncated" in codes


# ---------------------------------------------------------------------------
# the scenario routes
# ---------------------------------------------------------------------------


def test_scenario_result_carries_every_documented_key(client):
    result = warm(client, FEASIBLE)
    assert set(result) == RESULT_KEYS
    assert set(result["totals"]) == TOTALS_KEYS
    assert isinstance(result["calibration"], dict) and result["calibration"]
    assert isinstance(result["warnings"], list)
    assert result["spec"]["date"] == DAY
    assert result["scorecard"] is not None  # this portfolio schedules


def test_timeseries_has_one_row_per_interval_with_the_documented_columns(client):
    result = warm(client, FEASIBLE)
    body = client.get(f"/api/scenario/{result['id']}/timeseries").json()
    assert {"id", "rows", "warnings"} <= set(body)
    # 96 intervals in a 24-hour Berlin day at the native 15-minute resolution
    assert len(body["rows"]) == len(day_index()) == 96
    assert all(TIMESERIES_ROW_KEYS <= set(row) for row in body["rows"])
    assert [row["t"] for row in body["rows"]] == [t.isoformat() for t in day_index()]


def test_pooling_route_answers_with_a_curve_field(client):
    result = warm(client, FEASIBLE)
    body = client.get(f"/api/scenario/{result['id']}/pooling").json()
    assert "diversification_curve" in body
    assert isinstance(body["diversification_curve"], list)


def test_map_has_one_row_per_site_with_lat_lon_firm_revenue_co2(client):
    result = warm(client, FEASIBLE)
    body = client.get(f"/api/scenario/{result['id']}/map").json()
    rows = body["sites"]
    assert len(rows) == 2  # FEASIBLE is a two-site portfolio
    for row in rows:
        assert {"site_id", "lat", "lon", "firm_kw", "revenue_eur", "co2_kg"} <= set(row)
        assert row["lat"] is not None and row["lon"] is not None
    # the allocation rule is on the wire, not left for the reader to guess
    assert body["firm_kw_rule"] and body["revenue_allocation_rule"]


def test_stream_replays_the_day_tick_by_tick(client):
    result = warm(client, FEASIBLE)
    text = client.get(f"/api/scenario/{result['id']}/stream").text
    ticks = [line for line in text.splitlines() if line == "event: tick"]
    assert len(ticks) == len(day_index()) == 96
    assert "event: end" in text
    payloads = [
        json.loads(line[len("data: ") :])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]
    assert payloads[0]["index"] == 0 and payloads[0]["n"] == 96
    assert TIMESERIES_ROW_KEYS <= set(payloads[0])


# ---------------------------------------------------------------------------
# latency and secrets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "route",
    ["", "/timeseries", "/pooling", "/map"],
)
def test_warm_routes_answer_well_under_500ms(client, route):
    """The contract's latency claim, measured rather than asserted in a comment."""
    result = warm(client, FEASIBLE)
    started = time.perf_counter()
    response = client.get(f"/api/scenario/{result['id']}{route}")
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    assert response.status_code == 200
    assert elapsed_ms < 500.0, f"{route or '(result)'} took {elapsed_ms:.0f} ms warm"


def test_no_response_leaks_a_secret_from_the_environment(client, monkeypatch):
    """`ELEVENLABS_API_KEY` and friends are `src/voice`'s, never proxied through here.

    The secret is really in this process's environment (asserted first, so the probe
    cannot pass by looking for a value that was never there).
    """
    import os

    monkeypatch.setenv("FLEXGRID_SECRET_TOKEN", SECRET_VALUE)
    assert os.environ["ELEVENLABS_API_KEY"] == SECRET_VALUE

    result = warm(client, FEASIBLE)
    scenario_id = result["id"]
    bodies = [
        client.get("/api/health").text,
        client.get("/api/assumptions").text,
        client.get("/api/sites").text,
        client.post("/api/scenario", json=FEASIBLE).text,
        client.get(f"/api/scenario/{scenario_id}").text,
        client.get(f"/api/scenario/{scenario_id}/timeseries").text,
        client.get(f"/api/scenario/{scenario_id}/pooling").text,
        client.get(f"/api/scenario/{scenario_id}/map").text,
        client.get(f"/api/scenario/{scenario_id}/stream").text,
        client.post(
            f"/api/scenario/{scenario_id}/dispatch",
            json={
                "call_t": f"{DAY}T18:00:00+00:00",
                "notice_min": 5,
                "duration_min": 60,
                "reduction_kw": 1.0,
            },
        ).text,
    ]
    assert all(bodies), "a route answered with an empty body"
    for body in bodies:
        assert SECRET_VALUE not in body
        assert "ELEVENLABS" not in body.upper()
