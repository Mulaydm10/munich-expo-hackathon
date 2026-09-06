"""Private: the whole pipeline, run once per spec.

`data -> fleet -> forecast -> grid / market / sched`, composed here and nowhere else.
No modelling lives in this lane (contracts/src/service.md); every number below is
produced by another lane's public API and carried through unchanged, or is an
arithmetic aggregation whose rule is stated next to it.

Two rules from `contracts/CONVENTIONS.md` shape almost every branch:

* **Any coercion is observable.** Wherever this module clips, drops, substitutes or
  assumes, it appends a `warnings[]` entry carrying the *rate or count*, and the
  upstream lanes' own coercion telemetry (`df.attrs`) is forwarded the same way. A
  warning is emitted only when the coercion actually bound, so the tests can pin it at
  both ends.
* **Measure the physical quantity, not the derived one.** Nothing here is computed from
  a feasibility flag or a search parameter. In particular `src/sched`'s
  `reduction_kw_achieved` is a per-`(site, t)` worst-interval floor measurement, so it
  is forwarded verbatim and never summed or averaged across sites into a headline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from src.data import api as data
from src.fleet import api as fleet
from src.forecast import api as forecast
from src.grid import api as grid
from src.market import api as market
from src.sched import api as sched

from ._errors import BadSpec, MissingData, UpstreamUnavailable
from ._spec import ScenarioSpec

# Days of history synthesised before the scenario day so the forecast has its 168 h
# lag/rolling features. 10 leaves ~3 complete training days after the 7-day warm-up.
HISTORY_DAYS = 10

LOCAL_TZ = "Europe/Berlin"

# The canonical tables named in contracts/src/data.md. Listed here (rather than read
# from src/data, whose SOURCES map only covers wired sources) so /api/health can show a
# table that does not exist yet as absent rather than omitting it.
CANONICAL_TABLES = ("sites", "grid_load", "prices", "weather", "balancing", "carbon")


# ---------------------------------------------------------------------------
# warnings
# ---------------------------------------------------------------------------


class Warnings:
    """Ordered, structured `warnings[]`.

    Structured rather than free text because `src/ui` has to display them and
    `src/voice` has to read them aloud: `code` is stable, `message` is for a human,
    `detail` carries the rate or count that makes the warning checkable.
    """

    def __init__(self) -> None:
        self._items: list[dict] = []

    def add(self, code: str, lane: str, message: str, **detail) -> None:
        self._items.append(
            {"code": code, "lane": lane, "message": message, "detail": detail or {}}
        )

    def rate(self, code: str, lane: str, message: str, value, **detail) -> None:
        """Emit only when a coercion actually bound (`value` > 0).

        A warning that fires unconditionally is a caveat, not a measurement -- and one
        that can never fire is not evidence either, which is why every rate forwarded
        here has a test forcing it and a test avoiding it.
        """
        if value is None:
            return
        value = float(value)
        if value > 0.0:
            self.add(code, lane, message, rate=value, **detail)

    def codes(self) -> list[str]:
        return [w["code"] for w in self._items]

    def as_list(self) -> list[dict]:
        return list(self._items)


# ---------------------------------------------------------------------------
# live (in-process) scenario state
# ---------------------------------------------------------------------------


@dataclass
class LiveScenario:
    """What `/dispatch` needs and JSON cannot hold.

    `src.sched.dispatch` requires the exact frame `src.sched.schedule` returned in this
    process (it reads sessions/envelope/prices off `.attrs`), so the dispatch route
    replays the pipeline rather than reconstructing a schedule from the cached JSON.
    """

    spec: ScenarioSpec
    grid_index: pd.DatetimeIndex
    optimised: pd.DataFrame | None
    envelope: pd.DataFrame
    sessions_day: pd.DataFrame
    prices_day: pd.DataFrame
    baseline_by_t: pd.Series
    warnings: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _capability(module, name: str):
    fn = getattr(module, name, None)
    return fn if callable(fn) else None


def _day_bounds(day: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The Europe/Berlin local day, as UTC interval-start bounds.

    CONVENTIONS.md: humans see Berlin, computation is UTC. Doing the arithmetic in
    local time is what makes the 25-hour DST day a 100-interval grid instead of a
    crash.
    """
    start = pd.Timestamp(day, tz=LOCAL_TZ).tz_convert("UTC")
    end = pd.Timestamp(day + timedelta(days=1), tz=LOCAL_TZ).tz_convert("UTC")
    return start, end


def _grid(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    return pd.date_range(start, end, freq="15min", inclusive="left", tz="UTC")


def _require_full_grid(df: pd.DataFrame, index: pd.DatetimeIndex, table: str) -> None:
    have = pd.DatetimeIndex(pd.Series(df["t"]).unique())
    missing = index.difference(have)
    if len(missing):
        raise MissingData(
            f"table {table!r} covers {len(index) - len(missing)} of the {len(index)} "
            f"15-minute intervals this scenario needs; first gap at {missing[0].isoformat()}",
            f"build {table!r} over the scenario window, or pick a date the table covers. "
            "A gap is left as a gap on purpose: forward-filling it here would invent the "
            "missing hours (contracts/CONVENTIONS.md).",
        )


def _by_t(df: pd.DataFrame, column: str, index: pd.DatetimeIndex) -> pd.Series:
    """Portfolio total of `column` per interval, on `index`, zero where no row exists.

    Zero-filling is definitional, not an assumption: a (site, interval) with no
    schedule row carries no session and therefore no power. Aggregation rule stated
    here because every headline in `totals` is built on it.
    """
    if df is None or df.empty:
        return pd.Series(0.0, index=index)
    grouped = df.groupby("t")[column].sum()
    grouped.index = pd.DatetimeIndex(grouped.index)
    return grouped.reindex(index, fill_value=0.0).astype(float)


def _f(value) -> float | None:
    if value is None:
        return None
    value = float(value)
    return None if (np.isnan(value) or np.isinf(value)) else value


# ---------------------------------------------------------------------------
# stage 1: data
# ---------------------------------------------------------------------------


def _load_optional(table: str, root, warns: Warnings, *, start=None, end=None):
    try:
        return data.load(table, root=root, start=start, end=end)
    except data.MissingTable as exc:
        warns.add(
            "missing_table",
            "src/data",
            f"canonical table {table!r} is not built; every figure derived from it is "
            "reported as null rather than zero",
            table=table,
            how_to_get_it=exc.how_to_get_it,
        )
        return None


def _load_required(table: str, root, *, start=None, end=None) -> pd.DataFrame:
    try:
        return data.load(table, root=root, start=start, end=end)
    except data.MissingTable as exc:
        raise MissingData(
            f"canonical table {table!r} is required to run a scenario and is not built",
            exc.how_to_get_it,
        ) from exc


def _select_sites(sites: pd.DataFrame, spec: ScenarioSpec, warns: Warnings) -> pd.DataFrame:
    data.require_columns(sites, ["site_id", "lat", "lon", "rated_power_kw", "n_points"])
    sites = sites.sort_values("site_id").reset_index(drop=True)

    if spec.site_ids is not None:
        wanted = list(spec.site_ids)
        chosen = sites[sites["site_id"].isin(wanted)]
        unknown = sorted(set(wanted) - set(chosen["site_id"]))
        if unknown:
            raise BadSpec(
                f"{len(unknown)} requested site_id(s) are not in the `sites` table: "
                f"{unknown[:5]}",
                "check the ids against GET /api/sites; a silently smaller portfolio would "
                "understate every total in this scenario",
            )
    elif spec.region is not None:
        state = sites["state"].astype(str) if "state" in sites.columns else pd.Series("", index=sites.index)
        postcode = (
            sites["postcode"].astype(str) if "postcode" in sites.columns
            else pd.Series("", index=sites.index)
        )
        chosen = sites[(state == spec.region) | postcode.str.startswith(spec.region)]
        if chosen.empty:
            raise BadSpec(
                f"region={spec.region!r} matches no site's `state` and no `postcode` prefix",
                "regions are the sites table's `state` values, or a postcode prefix such "
                "as '80'",
            )
    else:
        chosen = sites.head(int(spec.n_sites))
        if len(chosen) < int(spec.n_sites):
            warns.add(
                "portfolio_smaller_than_requested",
                "src/service",
                f"n_sites={spec.n_sites} requested but the sites table holds only "
                f"{len(chosen)}; the scenario ran on what exists",
                requested=int(spec.n_sites),
                available=int(len(chosen)),
            )
    return chosen.reset_index(drop=True)


def _weather_area(weather: pd.DataFrame, warns: Warnings) -> pd.DataFrame:
    """One area-wide `t, temp_c` series.

    `src.data.nearest_weather_station` is not wired, so there is no per-site station
    mapping to use. Averaging the stations is a substitution, so it is counted: with a
    single station in the table nothing is substituted and no warning is emitted.
    """
    data.require_columns(weather, ["t", "temp_c"])
    n_stations = int(weather["station_id"].nunique()) if "station_id" in weather.columns else 1
    if n_stations > 1:
        warns.add(
            "weather_stations_averaged",
            "src/service",
            f"ambient temperature is the mean of {n_stations} stations applied to every "
            "site; src.data.nearest_weather_station() is not wired, so no per-site "
            "station could be chosen",
            n_stations=n_stations,
        )
    out = weather.groupby("t", as_index=False)["temp_c"].mean()
    out["t"] = pd.DatetimeIndex(out["t"])
    return out.sort_values("t").reset_index(drop=True)


# ---------------------------------------------------------------------------
# the pipeline
# ---------------------------------------------------------------------------


def build(spec: ScenarioSpec, *, root=None, progress=None) -> tuple[dict, LiveScenario]:
    """Run the pipeline once and return `(cache document, live in-process state)`."""

    def step(fraction: float, stage: str) -> None:
        if progress is not None:
            progress(fraction, stage)

    warns = Warnings()
    day = date.fromisoformat(spec.date)
    day_start, day_end = _day_bounds(day)
    hist_start = pd.Timestamp(day - timedelta(days=HISTORY_DAYS), tz=LOCAL_TZ).tz_convert("UTC")
    day_index = _grid(day_start, day_end)
    span_index = _grid(hist_start, day_end)

    # -- data ---------------------------------------------------------------
    step(0.05, "data:sites")
    sites = _select_sites(_load_required("sites", root), spec, warns)

    step(0.10, "data:tables")
    prices = _load_required("prices", root, start=hist_start, end=day_end)
    data.require_columns(prices, ["t", "price_eur_mwh"])
    _require_full_grid(prices, span_index, "prices")

    weather_raw = _load_required("weather", root, start=hist_start, end=day_end)
    weather = _weather_area(weather_raw, warns)
    _require_full_grid(weather, span_index, "weather")

    carbon = _load_optional("carbon", root, warns, start=day_start, end=day_end)
    balancing = _load_optional("balancing", root, warns, start=day_start, end=day_end)

    prices_day = prices[(prices["t"] >= day_start) & (prices["t"] < day_end)].reset_index(drop=True)

    # -- fleet --------------------------------------------------------------
    step(0.20, "fleet:sessions")
    synthesise = _capability(fleet, "synthesise_sessions")
    to_load = _capability(fleet, "to_load")
    if synthesise is None or to_load is None:
        raise UpstreamUnavailable(
            "src/fleet does not implement synthesise_sessions()/to_load(); there are no "
            "charging sessions to run a scenario on",
            "land the src/fleet lane (contracts/src/fleet.md, issue #19). This lane will "
            "not substitute a load curve of its own: an invented baseline would make "
            "every flexibility figure downstream unfalsifiable.",
        )
    classify = _capability(fleet, "classify_sites")
    if classify is None:
        warns.add(
            "upstream_unavailable",
            "src/fleet",
            "classify_sites() is not implemented; sites carry no `profile` and the map "
            "shows null for it",
            function="classify_sites",
        )
        sites_classified = sites
    else:
        sites_classified = classify(sites)

    days = [day - timedelta(days=n) for n in range(HISTORY_DAYS, -1, -1)]
    sessions = synthesise(sites_classified, weather, days, seed=spec.seed)
    _fleet_session_warnings(sessions, warns)

    base_load = to_load(sessions, freq="15min", policy="asap")
    data.require_columns(base_load, ["t", "site_id", "load_kw"])
    base_load = base_load.copy()
    base_load["t"] = pd.DatetimeIndex(base_load["t"])
    base_load = base_load[(base_load["t"] >= hist_start) & (base_load["t"] < day_end)]
    _fleet_density_warning(base_load, span_index, sites, warns)

    sessions_day, n_truncated = _sessions_in_day(sessions, day_start, day_end)
    if n_truncated:
        warns.add(
            "sessions_outside_day_grid",
            "src/service",
            f"{n_truncated} session(s) start before or end after the scenario day and are "
            "not scheduled; their energy is absent from the optimised totals",
            count=int(n_truncated),
            total_sessions=int(len(sessions)),
        )

    # -- grid ---------------------------------------------------------------
    step(0.35, "grid:envelope")
    envelope, envelope_clipped_rate = _build_envelope(
        sites, spec, weather, base_load, day_index, day_start, warns
    )
    warns.rate(
        "envelope_clipped",
        "src/grid",
        "thermal_envelope() truncated the physically-solved limit at "
        "MAX_ENVELOPE_MULTIPLE_OF_NAMEPLATE on some intervals; the envelope shown is the "
        "cap, not the thermal solution",
        envelope_clipped_rate,
    )

    # -- forecast -----------------------------------------------------------
    step(0.50, "forecast:fit")
    preds, calibration = _forecast(spec, base_load, weather, prices, day_start, day_end, warns)

    # -- market: firm capacity + pooling ------------------------------------
    step(0.70, "market:firm")
    firm = market.firm_capacity(preds, tau=spec.tau, floor_kw=0.0)
    _forward_attrs(
        firm,
        "src/market",
        warns,
        {
            "floor_clamp_rate": "firm_capacity() floored a negative promise at zero",
            "nan_quantile_rate": "firm_capacity() read a NaN forecast quantile as zero "
            "deliverable capacity",
            "negative_quantile_rate": "the forecast produced negative load quantiles",
            "lower_above_median_rate": "the tau quantile sits above the median on some rows "
            "(possible quantile mislabelling)",
        },
    )

    pool_day, pooling_rows = _pooling(spec, firm, day_index, warns)

    # -- sched --------------------------------------------------------------
    step(0.80, "sched:schedule")
    optimised, scorecard, sched_warning = _schedule(spec, sessions_day, envelope, prices_day, warns)

    # -- market: money and carbon -------------------------------------------
    step(0.90, "market:settle")
    baseline_day = base_load[(base_load["t"] >= day_start) & (base_load["t"] < day_end)]
    baseline_by_t = _by_t(baseline_day, "load_kw", day_index)
    optimised_load = _schedule_to_load(optimised)
    optimised_by_t = _by_t(optimised_load, "load_kw", day_index) if optimised is not None else None

    totals = _totals(
        spec=spec,
        warns=warns,
        prices_day=prices_day,
        carbon=carbon,
        balancing=balancing,
        baseline_day=baseline_day,
        optimised_load=optimised_load,
        baseline_by_t=baseline_by_t,
        optimised_by_t=optimised_by_t,
        pool_day=pool_day,
        day_index=day_index,
    )

    # -- assemble -----------------------------------------------------------
    step(0.97, "assemble")
    result = {
        "id": spec.id,
        "spec": spec.to_dict(),
        "totals": totals,
        "scorecard": scorecard,
        "calibration": calibration,
        "warnings": warns.as_list(),
    }
    doc = {
        "result": result,
        "timeseries": _timeseries(
            day_index, baseline_by_t, optimised_by_t, envelope, prices_day, firm, pool_day
        ),
        "pooling": pooling_rows,
        "map": _map_rows(
            sites_classified, firm, optimised_load, baseline_day, carbon, totals, spec, warns
        ),
    }
    # warnings are collected across every stage, so the copy inside `result` is refreshed
    # once at the end rather than being a snapshot of whenever `_totals` happened to run.
    doc["result"]["warnings"] = warns.as_list()

    live = LiveScenario(
        spec=spec,
        grid_index=day_index,
        optimised=optimised,
        envelope=envelope,
        sessions_day=sessions_day,
        prices_day=prices_day,
        baseline_by_t=baseline_by_t,
        warnings=warns.as_list(),
    )
    step(1.0, "done")
    return doc, live


# ---------------------------------------------------------------------------
# stage helpers
# ---------------------------------------------------------------------------


def _forward_attrs(frame: pd.DataFrame, lane: str, warns: Warnings, messages: dict) -> None:
    """Forward another lane's `df.attrs` coercion telemetry into `warnings[]` verbatim."""
    for key, message in messages.items():
        warns.rate(key, lane, message, frame.attrs.get(key))


def _fleet_session_warnings(sessions: pd.DataFrame, warns: Warnings) -> None:
    data.require_columns(sessions, ["session_id", "site_id", "t_arrive", "t_depart",
                                    "energy_kwh", "max_power_kw", "deadline_t"])
    if "energy_clipped" in sessions.columns and len(sessions):
        rate = float(pd.Series(sessions["energy_clipped"]).astype(bool).mean())
        warns.rate(
            "sessions_energy_clipped",
            "src/fleet",
            "synthesise_sessions() capped some sessions' energy draw to fit their dwell; a "
            "clipped session carries no scheduling slack",
            rate,
            count=int(pd.Series(sessions["energy_clipped"]).astype(bool).sum()),
        )
    occupancy = sessions.attrs.get("occupancy")
    if occupancy is not None:
        dropped = _occupancy_total(occupancy, "dropped")
        arrivals = _occupancy_total(occupancy, "arrivals")
        if dropped and arrivals:
            warns.rate(
                "sessions_dropped_no_free_point",
                "src/fleet",
                "arrivals were turned away because every charge point at the site was busy",
                dropped / arrivals,
                dropped=int(dropped),
                arrivals=int(arrivals),
            )


def _occupancy_total(occupancy, key: str) -> float:
    if isinstance(occupancy, pd.DataFrame):
        return float(occupancy[key].sum()) if key in occupancy.columns else 0.0
    if isinstance(occupancy, dict):
        if key in occupancy and not isinstance(occupancy[key], dict):
            return float(occupancy[key])
        return float(sum(float(v.get(key, 0.0)) for v in occupancy.values() if isinstance(v, dict)))
    return 0.0


def _fleet_density_warning(base_load, span_index, sites, warns) -> None:
    """`to_load` is contractually dense (contracts/src/fleet.md).

    A sparse frame is reported, never repaired: design's note on #34 is explicit that a
    reindex-and-fill workaround in a consuming lane hides the defect. Everything
    downstream then sees the frame as it really is.
    """
    expected = len(span_index) * len(sites)
    actual = int(len(base_load))
    if actual < expected:
        warns.add(
            "fleet_load_sparse",
            "src/fleet",
            f"to_load() returned {actual} rows where the dense (interval x site) grid has "
            f"{expected}; missing rows are left missing, not filled",
            rows=actual,
            expected_rows=int(expected),
            missing_rate=float((expected - actual) / expected) if expected else 0.0,
        )


def _sessions_in_day(sessions: pd.DataFrame, day_start, day_end) -> tuple[pd.DataFrame, int]:
    s = sessions.copy()
    s["t_arrive"] = pd.DatetimeIndex(s["t_arrive"])
    s["t_depart"] = pd.DatetimeIndex(s["t_depart"])
    inside = (s["t_arrive"] >= day_start) & (s["t_depart"] <= day_end)
    touching = (s["t_depart"] > day_start) & (s["t_arrive"] < day_end)
    n_truncated = int((touching & ~inside).sum())
    return s[inside].reset_index(drop=True), n_truncated


def _build_envelope(sites, spec, weather, base_load, day_index, day_start, warns):
    electrical = grid.infer_electrical(sites, seed=spec.seed)
    ambient = (
        weather.set_index("t")["temp_c"].reindex(day_index).astype(float)
    )
    if ambient.isna().any():  # pragma: no cover - _require_full_grid already guarantees this
        raise MissingData(
            "ambient temperature has gaps over the scenario day",
            "build the `weather` table over the scenario day",
        )

    prior_window_start = day_start - pd.Timedelta(days=1)
    frames, clipped_num, clipped_den, n_no_prior = [], 0.0, 0, 0
    for site_id, site in electrical.items():
        prior = base_load[
            (base_load["site_id"] == site_id)
            & (base_load["t"] >= prior_window_start)
            & (base_load["t"] < day_start)
        ]
        prior_series = None
        if len(prior) and not prior["load_kw"].isna().any():
            prior_series = pd.Series(
                prior["load_kw"].to_numpy(dtype=float), index=pd.DatetimeIndex(prior["t"])
            ).sort_index()
        else:
            n_no_prior += 1
        env = grid.thermal_envelope(site, ambient, prior_load_kw=prior_series)
        env["site_id"] = site_id
        clipped_num += float(env["clipped"].sum())
        clipped_den += len(env)
        frames.append(env[["site_id", "t", "max_kw"]])

    if n_no_prior:
        warns.add(
            "envelope_cold_start",
            "src/service",
            f"{n_no_prior} site(s) had no prior-day load to warm the transformer thermal "
            "state, so their envelope starts from ambient",
            sites=int(n_no_prior),
        )
    envelope = pd.concat(frames, ignore_index=True)
    rate = (clipped_num / clipped_den) if clipped_den else 0.0
    return envelope, rate


def _forecast(spec, base_load, weather, prices, day_start, day_end, warns):
    features = forecast.make_features(base_load, weather, prices, horizon_h=36)
    train = features[features["t"] < day_start]
    day_rows = features[(features["t"] >= day_start) & (features["t"] < day_end)]
    if train.empty or day_rows.empty:
        raise MissingData(
            "not enough history to fit a forecast for this scenario day",
            f"the pipeline synthesises {HISTORY_DAYS} days of history; a shorter "
            "`weather`/`prices` window than that cannot support the 168 h lag features",
        )
    model = forecast.fit(train, base_load, quantiles=forecast.QUANTILES, seed=spec.seed)
    preds = model.predict(day_rows)
    warns.rate(
        "forecast_quantile_crossing",
        "src/forecast",
        "the independently-fitted quantile heads crossed on some rows and predict() "
        "re-sorted them",
        model.last_predict_crossing_rate,
    )
    warns.rate(
        "forecast_fallback_rows",
        "src/forecast",
        "some rows had no usable feature vector (or an unseen site) and fell back to the "
        "climatological quantiles",
        model.last_predict_fallback_rate,
    )

    truth = day_rows[["t", "site_id"]].merge(
        base_load[["t", "site_id", "load_kw"]], on=["t", "site_id"], how="left"
    )
    y = truth["load_kw"]
    usable = ~y.isna()
    if not usable.all():
        warns.rate(
            "calibration_rows_dropped",
            "src/service",
            "some forecast rows have no realised load to score against and were left out "
            "of the calibration figures",
            float((~usable).mean()),
            dropped=int((~usable).sum()),
        )
    if not usable.any():
        return preds, {}
    curve = forecast.reliability_curve(
        y[usable].to_numpy(dtype=float), preds.loc[usable.to_numpy()].reset_index(drop=True)
    )
    calibration = {
        f"{row.tau_nominal:g}": _f(row.coverage_empirical) for row in curve.itertuples(index=False)
    }
    return preds, calibration


def _pooling(spec, firm, day_index, warns):
    pool_fn = _capability(market, "pool")
    curve_fn = _capability(market, "diversification_curve")
    pool_day = None
    if pool_fn is None:
        warns.add(
            "upstream_unavailable",
            "src/market",
            "pool() is not implemented, so the pooled firm promise (and every total "
            "derived from it) is null, not zero",
            function="pool",
            how_to_get_it="land src/market issue #33 (pool / correlation_structure / "
                          "diversification_curve)",
        )
    else:
        pooled = pool_fn(firm, method=spec.pool_method, seed=spec.seed)
        data.require_columns(pooled, ["t", "pool_firm_kw"])
        pooled = pooled.copy()
        pooled["t"] = pd.DatetimeIndex(pooled["t"])
        pool_day = pooled[pooled["t"].isin(day_index)].sort_values("t").reset_index(drop=True)

    rows: list[dict] = []
    if curve_fn is None:
        warns.add(
            "upstream_unavailable",
            "src/market",
            "diversification_curve() is not implemented; /pooling returns an empty curve "
            "rather than a fabricated one",
            function="diversification_curve",
            how_to_get_it="land src/market issue #33",
        )
    else:
        n_sites = int(firm["site_id"].nunique())
        sizes = list(range(1, n_sites + 1))
        curve = curve_fn(firm, sizes=sizes, seed=spec.seed)
        rows = [
            {k: _f(v) if isinstance(v, (int, float, np.generic)) else v for k, v in row.items()}
            for row in curve.to_dict("records")
        ]
    return pool_day, rows


def _schedule(spec, sessions_day, envelope, prices_day, warns):
    """The optimised schedule and `src/sched`'s own scorecard.

    `commitments=()` deliberately: `Commitment` carries no `site_id` and
    `src/sched.schedule()` applies every commitment to every site in the call, so a
    portfolio floor cannot be expressed without one solver call per site. Selling a
    floor is therefore out of this scenario; firm capacity is reported as *sellable*,
    and the sold floor is exercised through `/dispatch`.
    """
    warns.add(
        "commitments_not_applied",
        "src/service",
        "the optimised schedule holds no sold reduction floor: src.sched.Commitment has "
        "no site_id and applies to every site in a call, so a portfolio floor cannot be "
        "posed without one solver call per site. `firm_kw` is what could be sold, not "
        "what was sold, and scorecard.floor_shortfall_kw_min is measured against no "
        "commitment.",
        commitments=0,
    )
    try:
        optimised = sched.schedule(sessions_day, envelope, prices_day, commitments=(), solver="lp")
    except sched.Infeasible as exc:
        warns.add(
            "schedule_infeasible",
            "src/sched",
            f"no schedule satisfies every hard constraint: {exc}",
            binding=str(exc),
        )
        return None, None, str(exc)
    scorecard = sched.evaluate(optimised, sessions_day, envelope, prices_day, ())
    scorecard = {k: _f(v) if isinstance(v, (int, float, np.generic)) else v
                 for k, v in scorecard.items()}
    for key, message in (
        ("unmet_kwh", "the optimised schedule leaves energy undelivered"),
        ("deadline_misses", "the optimised schedule misses session deadlines"),
        ("envelope_violation_kwh", "the optimised schedule exceeds the thermal envelope"),
        ("floor_shortfall_kw_min", "the optimised schedule breaks a sold reduction floor"),
    ):
        warns.rate("scorecard_" + key, "src/sched", message + f" ({key})", scorecard.get(key))
    return optimised, scorecard, None


def _schedule_to_load(optimised) -> pd.DataFrame | None:
    if optimised is None:
        return None
    if optimised.empty:
        return pd.DataFrame(columns=["t", "site_id", "load_kw"])
    out = optimised.groupby(["site_id", "t"], as_index=False)["power_kw"].sum()
    return out.rename(columns={"power_kw": "load_kw"})[["t", "site_id", "load_kw"]]


def _totals(*, spec, warns, prices_day, carbon, balancing, baseline_day, optimised_load,
            baseline_by_t, optimised_by_t, pool_day, day_index):
    active = optimised_load if spec.policy == "optimised" else baseline_day
    energy_cost = _f(market.energy_cost(active, prices_day)) if active is not None else None

    co2_saved = None
    if carbon is not None and optimised_load is not None:
        co2_base = market.co2(baseline_day, carbon)
        co2_active = market.co2(active, carbon)
        co2_saved = _f(co2_base - co2_active)

    pool_firm_mw = None
    peakers = None
    if pool_day is not None and len(pool_day):
        # Worst interval, not the mean: a firm promise is only as good as its weakest
        # 15 minutes (the same rule src/market.bid sizes a block by).
        pool_firm_mw = _f(float(pool_day["pool_firm_kw"].min()) / 1000.0)
        if pool_firm_mw is not None:
            count, assumption = market.peakers_displaced(pool_firm_mw)
            peakers = {"count": _f(count), "assumption": assumption}

    capacity_revenue = _bid_revenue(spec, pool_day, balancing, warns)

    warns.add(
        "not_settled",
        "src/service",
        "penalty_eur and net_eur are null: src.market.settle() needs a delivery record "
        "per block, and this scenario dispatches no reduction event. Delivered-vs-promised "
        "is measured on POST /api/scenario/{id}/dispatch instead of being guessed here.",
    )

    return {
        "energy_cost_eur": energy_cost,
        "capacity_revenue_eur": capacity_revenue,
        "penalty_eur": None,
        "net_eur": None,
        "co2_kg_saved": co2_saved,
        "peak_kw_baseline": _f(float(baseline_by_t.max())) if len(baseline_by_t) else None,
        "peak_kw_optimised": _f(float(optimised_by_t.max())) if optimised_by_t is not None else None,
        "pool_firm_mw": pool_firm_mw,
        "peakers_displaced": peakers,
    }


def _bid_revenue(spec, pool_day, balancing, warns):
    if pool_day is None or not len(pool_day):
        return None
    if balancing is None:
        return None
    prices = _balancing_for_product(balancing, spec, warns)
    if prices is None:
        return None
    spec_product = market.PRODUCTS[spec.product]
    block = f"{int(spec_product.block_length_min)}min"
    per_block = int(round(spec_product.block_length_min / 15.0))

    pool_day = pool_day.copy()
    pool_day["block_start"] = pool_day["t"].dt.floor(block)
    counts = pool_day.groupby("block_start")["t"].nunique()
    complete = counts[counts == per_block].index
    partial = int((counts != per_block).sum())
    if partial:
        warns.add(
            "bid_blocks_partial_dropped",
            "src/service",
            f"{partial} product block(s) are only partly covered by this scenario day and "
            "were not bid; capacity revenue counts whole blocks only",
            dropped_blocks=partial,
            complete_blocks=int(len(complete)),
        )
    if not len(complete):
        return None
    pool_complete = pool_day[pool_day["block_start"].isin(complete)].drop(columns=["block_start"])
    try:
        bids = market.bid(pool_complete, spec.product, prices)
    except market.MarketError as exc:
        warns.add(
            "bid_failed",
            "src/market",
            f"bid() refused to size blocks for this scenario: {exc}",
            detail_text=str(exc),
        )
        return None
    warns.rate(
        "bid_blocks_dropped_below_minimum",
        "src/market",
        f"blocks were dropped for falling under the {spec.product} minimum bid size",
        bids.attrs.get("blocks_dropped_rate"),
        blocks_considered=bids.attrs.get("blocks_considered"),
    )
    warns.rate(
        "bid_rounddown_bind",
        "src/market",
        "bid() rounded a block's capacity down to the product granularity",
        bids.attrs.get("blocks_rounddown_bind_rate"),
    )
    return _f(float(bids["expected_revenue_eur"].sum())) if len(bids) else 0.0


def _balancing_for_product(balancing, spec, warns):
    if "capacity_price_eur_mw_h" not in balancing.columns:
        warns.add(
            "balancing_columns_missing",
            "src/data",
            "the `balancing` table carries no capacity_price_eur_mw_h column, so no "
            "capacity revenue can be priced",
            columns=list(balancing.columns),
        )
        return None
    sub = balancing
    if "product" in sub.columns:
        sub = sub[sub["product"].astype(str) == spec.product]
        if sub.empty:
            warns.add(
                "balancing_product_missing",
                "src/data",
                f"the `balancing` table holds no rows for product {spec.product!r}",
                product=spec.product,
            )
            return None
    if "direction" in sub.columns:
        # A load reduction is upward (positive) reserve from the grid's point of view.
        positive = sub[sub["direction"].astype(str).str.upper().str.startswith("POS")]
        if len(positive):
            sub = positive
        else:
            warns.add(
                "balancing_direction_unfiltered",
                "src/data",
                "the `balancing` table has a `direction` column but no POS rows; capacity "
                "prices were taken across every direction present",
                directions=sorted(sub["direction"].astype(str).unique().tolist()),
            )
    duplicates = int(len(sub) - sub["t"].nunique())
    if duplicates:
        warns.add(
            "balancing_rows_averaged",
            "src/service",
            f"{duplicates} duplicate balancing row(s) per timestamp were averaged into one "
            "capacity price series",
            duplicate_rows=duplicates,
        )
        sub = sub.groupby("t", as_index=False).mean(numeric_only=True)
    return sub.sort_values("t").reset_index(drop=True)


def _timeseries(day_index, baseline_by_t, optimised_by_t, envelope, prices_day, firm, pool_day):
    env_by_t = _by_t(envelope, "max_kw", day_index)
    firm_by_t = _by_t(firm, "firm_kw", day_index)
    price_by_t = prices_day.set_index("t")["price_eur_mwh"].reindex(day_index)
    pool_by_t = (
        pool_day.set_index("t")["pool_firm_kw"].reindex(day_index) if pool_day is not None else None
    )
    rows = []
    for i, t in enumerate(day_index):
        rows.append(
            {
                "t": t.isoformat(),
                "load_kw_baseline": _f(baseline_by_t.iloc[i]),
                "load_kw_optimised": _f(optimised_by_t.iloc[i]) if optimised_by_t is not None else None,
                "envelope_kw": _f(env_by_t.iloc[i]),
                "price_eur_mwh": _f(price_by_t.iloc[i]),
                # naive per-site sum; the pooled promise is the separate column beside it
                "firm_kw": _f(firm_by_t.iloc[i]),
                "pool_firm_kw": _f(pool_by_t.iloc[i]) if pool_by_t is not None else None,
            }
        )
    return rows


def _map_rows(sites, firm, optimised_load, baseline_day, carbon, totals, spec, warns):
    """Per-site `firm_kw`, `revenue_eur`, `co2_kg`, lat/lon.

    `firm_kw` is the site's **worst interval** over the day (the promise it could keep
    all day), not its mean. `revenue_eur` is the scenario's capacity revenue split
    pro-rata by that figure -- an allocation rule, stated in the payload rather than
    left for the reader to guess.
    """
    firm_by_site = firm.groupby("site_id")["firm_kw"].min()
    total_firm = float(firm_by_site.sum())
    revenue = totals.get("capacity_revenue_eur")
    load = optimised_load if (spec.policy == "optimised" and optimised_load is not None) else baseline_day

    co2_by_site = {}
    if carbon is not None and load is not None and len(load):
        for site_id, grp in load.groupby("site_id"):
            co2_by_site[site_id] = _f(market.co2(grp, carbon))

    rows = []
    for row in sites.itertuples(index=False):
        site_id = str(row.site_id)
        site_firm = _f(firm_by_site.get(site_id))
        share = (site_firm / total_firm) if (site_firm is not None and total_firm > 0) else None
        rows.append(
            {
                "site_id": site_id,
                "lat": _f(getattr(row, "lat", None)),
                "lon": _f(getattr(row, "lon", None)),
                "profile": getattr(row, "profile", None),
                "rated_power_kw": _f(getattr(row, "rated_power_kw", None)),
                "firm_kw": site_firm,
                "revenue_eur": _f(revenue * share) if (revenue is not None and share is not None) else None,
                "co2_kg": co2_by_site.get(site_id),
            }
        )
    return {
        "sites": rows,
        "firm_kw_rule": "per-site minimum firm_kw over the scenario day (worst interval)",
        "revenue_allocation_rule": (
            "totals.capacity_revenue_eur split pro-rata by each site's share of the summed "
            "worst-interval firm_kw; null when no capacity revenue could be priced"
        ),
        "co2_kg_rule": f"src.market.co2 of the site's own load under policy={spec.policy!r}",
    }
