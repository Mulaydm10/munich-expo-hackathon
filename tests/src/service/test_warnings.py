"""`warnings[]` carries what the pipeline actually had to assume, clip or skip.

"silent degradation is forbidden" (`contracts/src/service.md`) is the guarantee this
whole module exists for, so nothing here is satisfied by a `warnings` key that is merely
present. Every warning is pinned **at both ends**: a fixture that forces it and a fixture
that avoids it, so the measurement is proven to move rather than merely to exist
(`contracts/CONVENTIONS.md`, "Any coercion is observable").
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.fleet import api as fleet
from src.forecast import api as forecast
from src.service import _pipeline as pipeline

from .conftest import (
    DAY,
    FEASIBLE,
    WITH_INFEASIBLE_SITE,
    build_root,
    day_index,
    prices_frame,
    warm,
    weather_frame,
    span_index,
    write_table,
)

LANES = {
    "src/data",
    "src/fleet",
    "src/forecast",
    "src/grid",
    "src/market",
    "src/sched",
    "src/service",
}


def codes(result) -> list[str]:
    return [w["code"] for w in result["warnings"]]


def detail(result, code: str) -> dict:
    matches = [w["detail"] for w in result["warnings"] if w["code"] == code]
    assert matches, f"no {code!r} warning in {codes(result)}"
    return matches[0]


# ---------------------------------------------------------------------------
# structure
# ---------------------------------------------------------------------------


def test_every_warning_is_structured_and_attributed(client):
    """`src/ui` renders these and `src/voice` reads them aloud, so `code` is stable,
    `message` is for a human, and `detail` carries the number that makes it checkable."""
    result = warm(client, WITH_INFEASIBLE_SITE)
    assert result["warnings"], "a scenario with a known-degraded site reported nothing"
    for warning in result["warnings"]:
        assert set(warning) == {"code", "lane", "message", "detail"}
        assert warning["code"] and warning["message"]
        assert warning["lane"] in LANES, warning["lane"]
        assert isinstance(warning["detail"], dict)


def test_warnings_reach_every_route_that_shows_a_number(client):
    """A judge reading /timeseries must see the same caveats as one reading the result."""
    result = warm(client, WITH_INFEASIBLE_SITE)
    scenario_id = result["id"]
    for route in ("/timeseries", "/pooling", "/map"):
        body = client.get(f"/api/scenario/{scenario_id}{route}").json()
        assert body["warnings"] == result["warnings"], route


# ---------------------------------------------------------------------------
# a missing table: the degraded figure is null, and the reason is on the wire
# ---------------------------------------------------------------------------


def test_a_missing_optional_table_nulls_its_figure_and_says_so(client_on, tmp_path):
    """The headline degradation test: same spec, same day, one table removed.

    Both ends in one test -- with `carbon` the CO2 figure is a real number and no
    warning is raised; without it the figure is `null` (never `0.0`, which would read as
    "we saved nothing") and a `missing_table` warning names the table and how to build it.
    """
    with_carbon = warm(
        client_on(build_root(tmp_path / "with-carbon", carbon=True)), FEASIBLE
    )
    without_carbon = warm(
        client_on(build_root(tmp_path / "without-carbon", carbon=False)), FEASIBLE
    )

    assert isinstance(with_carbon["totals"]["co2_kg_saved"], float)
    assert with_carbon["totals"]["co2_kg_saved"] != 0.0
    assert "missing_table" not in codes(with_carbon)

    assert without_carbon["totals"]["co2_kg_saved"] is None
    assert "missing_table" in codes(without_carbon)
    assert detail(without_carbon, "missing_table")["table"] == "carbon"
    assert detail(without_carbon, "missing_table")["how_to_get_it"]

    # the two runs differ *only* in that table, so nothing else may have moved
    assert with_carbon["totals"]["peak_kw_baseline"] == without_carbon["totals"]["peak_kw_baseline"]


def test_history_truncation_is_reported(client_on, tmp_path):
    root = build_root(tmp_path / "short-history")
    short_index = span_index(history_days=12)
    write_table(root, "prices", prices_frame(short_index))
    write_table(root, "weather", weather_frame(short_index))
    result = warm(client_on(root), FEASIBLE)
    warning = detail(result, "history_truncated")
    assert warning == {"requested": 28, "used": 12}


def test_a_missing_balancing_table_nulls_the_capacity_revenue(client_on, tmp_path):
    result = warm(client_on(build_root(tmp_path / "no-balancing", balancing=False)), FEASIBLE)
    assert result["totals"]["capacity_revenue_eur"] is None
    assert "missing_table" in codes(result)
    assert {d["table"] for d in [w["detail"] for w in result["warnings"]
                                 if w["code"] == "missing_table"]} == {"balancing"}


# ---------------------------------------------------------------------------
# an infeasible site
# ---------------------------------------------------------------------------


def test_short_dwell_sessions_are_clamped_before_scheduling(client):
    """Fractional overlap makes short dwells representable on the scheduler grid."""
    feasible = warm(client, FEASIBLE)
    degraded = warm(client, WITH_INFEASIBLE_SITE)

    assert "session_energy_clamped_to_grid" not in codes(feasible)
    assert feasible["scorecard"] is not None

    assert "session_energy_clamped_to_grid" not in codes(degraded)
    assert degraded["scorecard"] is not None


def test_the_optimised_curve_preserves_fractional_grid_energy(client):
    """Fractional overlap avoids creating an artificial grid remnant."""
    degraded = warm(client, WITH_INFEASIBLE_SITE)
    rows = client.get(f"/api/scenario/{degraded['id']}/timeseries").json()["rows"]
    interval_h = 0.25  # the native 15-minute grid, asserted below
    assert len(rows) == len(day_index())
    baseline_kwh = sum(r["load_kw_baseline"] for r in rows) * interval_h
    optimised_kwh = sum(r["load_kw_optimised"] for r in rows) * interval_h
    assert baseline_kwh > 0
    assert optimised_kwh <= baseline_kwh + 1e-9
    assert "session_energy_clamped_to_grid" not in codes(degraded)
    assert "sessions_outside_day_grid" in codes(degraded)


# ---------------------------------------------------------------------------
# substitutions this lane itself makes
# ---------------------------------------------------------------------------


def test_averaging_weather_stations_is_reported_only_when_it_happens(client_on, tmp_path):
    one = warm(client_on(build_root(tmp_path / "one-station", stations=1)), FEASIBLE)
    two = warm(client_on(build_root(tmp_path / "two-stations", stations=2)), FEASIBLE)

    assert "weather_stations_averaged" not in codes(one)
    assert "weather_stations_averaged" in codes(two)
    assert detail(two, "weather_stations_averaged")["n_stations"] == 2


def test_a_portfolio_smaller_than_requested_is_reported(client):
    exact = warm(client, WITH_INFEASIBLE_SITE)
    too_big = warm(client, {**WITH_INFEASIBLE_SITE, "n_sites": 99})

    assert "portfolio_smaller_than_requested" not in codes(exact)
    assert "portfolio_smaller_than_requested" in codes(too_big)
    reported = detail(too_big, "portfolio_smaller_than_requested")
    assert reported == {"requested": 99, "available": 3}
    # and the scenario still ran on what exists rather than failing or padding
    assert too_big["totals"]["peak_kw_baseline"] == exact["totals"]["peak_kw_baseline"]


def test_truncating_the_site_list_is_reported_only_when_it_truncates(client):
    full = client.get("/api/sites").json()
    cut = client.get("/api/sites", params={"limit": 2}).json()
    assert [w["code"] for w in full["warnings"]] == []
    assert "sites_truncated" in [w["code"] for w in cut["warnings"]]


# ---------------------------------------------------------------------------
# upstream telemetry, forwarded rather than invented
# ---------------------------------------------------------------------------


def test_the_envelope_clip_rate_is_measured_from_ambient_not_asserted(client_on, tmp_path):
    """`src/grid`'s clip counter must move with the physics it describes.

    A cold day leaves the transformer more thermal headroom, so the
    `MAX_ENVELOPE_MULTIPLE_OF_NAMEPLATE` cap binds more often than on a warm one. If the
    forwarded rate did not move between these two runs it would not be a measurement.
    """
    rates = {}
    for label, temp_c in (("warm", 28.0), ("cold", -20.0)):
        root = build_root(tmp_path / f"ambient-{label}")
        frame = weather_frame(span_index())
        frame["temp_c"] = temp_c
        write_table(root, "weather", frame)
        result = warm(client_on(root), FEASIBLE)
        rates[label] = detail(result, "envelope_clipped")["rate"]
    assert rates["cold"] > rates["warm"] > 0.0, rates


def test_a_sparse_fleet_load_frame_is_reported_not_repaired(client_on, tmp_path, monkeypatch):
    """`to_load()` is contractually dense. A hole in it makes "0 kW" and "no data"
    indistinguishable downstream, so it is reported and left alone -- never back-filled.
    """
    root = build_root(tmp_path / "sparse")
    client = client_on(root)
    healthy = warm(client, FEASIBLE)
    assert "fleet_load_sparse" not in codes(healthy)

    real_to_load = fleet.to_load
    hole_at = day_index()[40]

    def holed(*args, **kwargs):
        frame = real_to_load(*args, **kwargs)
        site = sorted(frame["site_id"].unique())[0]
        return frame[~((frame["t"] == hole_at) & (frame["site_id"] == site))].reset_index(
            drop=True
        )

    monkeypatch.setattr(fleet, "to_load", holed)
    result = warm(client, {**FEASIBLE, "seed": 21})
    assert "fleet_load_sparse" in codes(result)
    reported = detail(result, "fleet_load_sparse")
    assert reported["expected_rows"] - reported["rows"] == 1
    assert 0.0 < reported["missing_rate"] < 1.0


def test_forecast_and_fleet_telemetry_is_forwarded_with_its_rate(client):
    """Rates forwarded from another lane's `df.attrs` arrive as numbers, not adjectives."""
    result = warm(client, WITH_INFEASIBLE_SITE)
    forwarded = {
        w["code"]: w for w in result["warnings"] if "rate" in w["detail"]
    }
    assert forwarded, "no upstream coercion telemetry was forwarded at all"
    for code, warning in forwarded.items():
        assert 0.0 < float(warning["detail"]["rate"]) <= 1.0, code
    assert "forecast_quantile_crossing" in forwarded
    assert "sessions_dropped_no_free_point" in forwarded
    assert forwarded["sessions_dropped_no_free_point"]["detail"]["dropped"] > 0


def test_forecast_fallback_rows_fires_only_when_the_real_rate_is_positive(client, monkeypatch):
    """`forecast_fallback_rows`'s own docstring: "some rows had no usable feature vector
    (or an unseen site) and fell back to the climatological quantiles". No fixture in this
    suite ever forces `model.last_predict_fallback_rate` above 0 (every existing test uses
    the real forecast model, whose fallback rate is exactly 0.0 on this fixture's data --
    confirmed directly against `_forecast()`), which meant nothing pinned this warning's
    "it does fire" end: `warns.rate("forecast_fallback_rows", ..., model.last_predict_fallback_rate)`
    could be permanently replaced with `warns.rate("forecast_fallback_rows", ..., None)` --
    silently and forever suppressing it -- without a single test in the suite noticing.

    Both ends are pinned here, in one test: the real (unpatched) model, on the same fixture
    every other test uses, never reports it; a model whose `last_predict_fallback_rate` is
    forced positive (the only lever available without touching `src/forecast`, whose
    internals this lane may not reach into) does, with the forwarded rate intact.
    """
    baseline = warm(client, {**FEASIBLE, "seed": 60101})
    assert "forecast_fallback_rows" not in codes(baseline)

    real_fit = forecast.fit

    class _ForcedFallbackModel:
        """Delegates to a real, correctly-fitted model for everything except the one
        telemetry field this test needs to force -- `predict()`'s actual output (and
        therefore every downstream figure) is untouched and still real."""

        def __init__(self, real_model, forced_rate: float) -> None:
            self._real = real_model
            self._forced_rate = forced_rate

        def predict(self, *args, **kwargs):
            return self._real.predict(*args, **kwargs)

        @property
        def last_predict_crossing_rate(self):
            return self._real.last_predict_crossing_rate

        @property
        def last_predict_fallback_rate(self):
            return self._forced_rate

    def fake_fit(*args, **kwargs):
        return _ForcedFallbackModel(real_fit(*args, **kwargs), forced_rate=0.25)

    monkeypatch.setattr(forecast, "fit", fake_fit)
    forced = warm(client, {**FEASIBLE, "seed": 60102})
    assert "forecast_fallback_rows" in codes(forced)
    assert detail(forced, "forecast_fallback_rows")["rate"] == pytest.approx(0.25)


def test_the_caveats_that_always_apply_say_which_figure_is_null(client):
    """Two warnings fire unconditionally, and they are caveats rather than measurements:
    each one has to name the field it explains, or it is noise."""
    result = warm(client, FEASIBLE)
    assert "not_settled" in codes(result)
    assert result["totals"]["penalty_eur"] is None and result["totals"]["net_eur"] is None
    message = [w["message"] for w in result["warnings"] if w["code"] == "not_settled"][0]
    assert "penalty_eur" in message and "net_eur" in message

    assert "commitments_not_applied" in codes(result)
    assert detail(result, "commitments_not_applied")["commitments"] == 0
    assert "demand_charge_applied" in codes(result)
    assert detail(result, "demand_charge_applied")["eur_per_kw_day"] > 0.0


def test_portfolio_coordination_reports_its_iterates(client):
    result = warm(client, FEASIBLE)
    reported = detail(result, "portfolio_coordination")

    assert reported["sweeps"] == 3
    assert len(reported["peak_kw_by_sweep"]) == 3
    assert reported["chosen_sweep"] in range(3)
    assert reported["shadow_exponent"] == 3


def test_portfolio_coordination_keeps_the_lowest_peak_iterate(client):
    result = warm(client, FEASIBLE)
    reported = detail(result, "portfolio_coordination")
    peaks = reported["peak_kw_by_sweep"]

    assert min(peaks) == peaks[reported["chosen_sweep"]]


def test_sessions_that_exceed_the_day_grid_are_clamped_before_scheduling(
    client, monkeypatch
):
    real_synthesise = fleet.synthesise_sessions

    def oversized(*args, **kwargs):
        sessions = real_synthesise(*args, **kwargs).copy()
        t_arrive = day_index()[8]
        sessions.loc[sessions.index[0], "t_arrive"] = t_arrive
        sessions.loc[sessions.index[0], "t_depart"] = t_arrive + pd.Timedelta(minutes=15)
        sessions.loc[sessions.index[0], "energy_kwh"] = (
            sessions.loc[sessions.index[0], "max_power_kw"] * 4.0
        )
        return sessions

    monkeypatch.setattr(fleet, "synthesise_sessions", oversized)
    result = warm(client, {**FEASIBLE, "seed": 60103})
    reported = detail(result, "session_energy_clamped_to_grid")

    assert reported["count"] >= 1
    assert reported["clamped_kwh"] > 0.0
    assert reported["dropped_sessions"] >= 0


def _null_reason_present(warnings, function_name, failed_code):
    """True if `warnings` explains why `function_name`'s figure is null, whichever of
    its two failure modes is currently live: not implemented at all (`upstream_unavailable`,
    `detail.function == function_name`), or implemented but refusing on this repo's data
    (its own dedicated `*_failed` code, `failed_code`)."""
    return any(
        (w["code"] == "upstream_unavailable" and w["detail"].get("function") == function_name)
        or w["code"] == failed_code
        for w in warnings
    )


def test_an_unavailable_upstream_function_nulls_its_figure_rather_than_zeroing_it(client):
    """`src/market.pool()` / `diversification_curve()` are either not implemented yet
    (issue #33 open) or implemented but refusing on this repo's data -- `pool()` on a
    site with zero residual variation, `diversification_curve()` because there is no
    measured `realised` load anywhere in this repo (`data/raw/` is empty) and it has no
    default for that argument. Either way, the pooled promise and the curve must come
    back `null` with the reason on the wire -- never `0.0`, which would read as a real
    measurement of no capacity. This is checked against whichever failure mode is
    actually live in `src/market` right now, rather than assuming non-implementation, so
    it keeps holding once #33 lands (`test_market_seam.py` pins the post-#33 behaviour
    directly)."""
    result = warm(client, FEASIBLE)
    assert _null_reason_present(result["warnings"], "pool", "pool_failed")
    assert _null_reason_present(
        result["warnings"], "diversification_curve", "diversification_curve_failed"
    )
    assert result["totals"]["pool_firm_mw"] is None
    assert result["totals"]["peakers_displaced"] is None
    assert client.get(f"/api/scenario/{result['id']}/pooling").json()[
        "diversification_curve"
    ] == []
