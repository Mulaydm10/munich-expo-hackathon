"""src.service — public API.

FastAPI scenario service; the only surface the UI and voice layers read.

This module is the lane's ONLY cross-lane surface: other lanes import
`src.service.api` and nothing else. See `contracts/src/service.md` for the
interface this lane owes the rest of the project, and
`contracts/CONVENTIONS.md` for units, time handling and the data layout.

Run it with `uvicorn src.service.api:app` (no arguments needed).

What this lane is
-----------------
`run_scenario(spec)` runs `data -> fleet -> forecast -> grid / market / sched` **once**,
writes the whole answer to `data/derived/service/<spec hash>.json`, and every route
reads that file. No route re-runs a model. No modelling lives here.

Three guarantees the routes below exist to keep, all from the contract:

* a cold scenario answers `202` with a job id and a real `progress` fraction rather
  than blocking; a warm one answers from cache;
* `warnings[]` carries what the pipeline had to assume, clip or skip -- forwarded from
  the upstream lanes' own coercion telemetry, never invented here;
* every error is JSON `{error, detail, how_to_fix}` at the real status code -- never a
  200 with an empty body.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.data import api as data
from src.market import api as market
from src.sched import api as sched

from . import _cache as cache
from . import _pipeline as pipeline
from ._errors import (
    BadSpec,
    MissingData,
    ScenarioFailed,
    ScenarioNotFound,
    ServiceError,
    UpstreamUnavailable,
)
from ._spec import ScenarioSpec

LANE = "src/service"

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Live (in-process) scenario state, keyed by scenario id. `src.sched.dispatch` needs the
# exact frame `src.sched.schedule()` returned in this process, which JSON cannot carry.
_LIVE: dict[str, pipeline.LiveScenario] = {}


# ---------------------------------------------------------------------------
# public Python API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScenarioResult:
    """`contracts/src/service.md`'s top-level result.

    Every field is always present. An unknown figure is `None` (JSON `null`), never
    `0.0`: "an absent key and a zero must never be the same thing on the wire", and a
    zero that means "we could not compute it" is the same lie one level down.
    """

    id: str
    spec: dict
    totals: dict
    scorecard: dict | None
    calibration: dict
    warnings: list

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_doc(cls, doc: dict) -> "ScenarioResult":
        r = doc["result"]
        return cls(
            id=r["id"],
            spec=r["spec"],
            totals=r["totals"],
            scorecard=r["scorecard"],
            calibration=r["calibration"],
            warnings=r["warnings"],
        )


def run_scenario(spec: ScenarioSpec, *, root=None, progress=None) -> ScenarioResult:
    """The whole pipeline, once, cached on disk under `data/derived/service/<hash>.json`.

    Deterministic: the same `ScenarioSpec` yields the same `id` and byte-identical JSON,
    and changing any one field of the spec (including `seed`) changes the id, because
    the id *is* the hash of the spec.

    `root` and `progress` are keyword-only additions to the contract signature: `root`
    so a test can point at a fixture `DATA_ROOT`, `progress` so the HTTP layer can
    report a cold run's stage without this function knowing about HTTP.
    """
    root = Path(root) if root is not None else cache.data_root()
    doc = cache.read(spec.id, root)
    if doc is None:
        built, live = pipeline.build(spec, root=root, progress=progress)
        cache.write(spec.id, built, root)
        _LIVE[spec.id] = live
        # read back rather than returning `built`: the object handed to a caller is then
        # exactly what every route will serve, byte for byte.
        doc = cache.read(spec.id, root)
    return ScenarioResult.from_doc(doc)


# ---------------------------------------------------------------------------
# app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="FlexGrid scenario service",
    description="Runs one scenario through the FlexGrid pipeline and serves it as JSON.",
    version="0.1.0",
)


def _root() -> Path:
    return cache.data_root()


@app.exception_handler(ServiceError)
async def _service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    """Even a bug answers in the contract's shape, at 500 -- never an empty 200."""
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "detail": f"{type(exc).__name__}: {exc}",
            "how_to_fix": (
                "this is a bug in src/service or in a lane it composes; the traceback is "
                "in the server log"
            ),
        },
    )


async def _json_body(request: Request) -> object:
    raw = await request.body()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise BadSpec(f"request body is not valid JSON: {exc}", "send a JSON object") from exc


def _git_sha() -> str:
    """The commit this process is serving, read from `.git` (no subprocess, no network)."""
    env = os.environ.get("FLEXGRID_GIT_SHA")
    if env:
        return env
    try:
        git = _REPO_ROOT / ".git"
        if git.is_file():  # a worktree: ".git" is a pointer file
            git = Path(git.read_text(encoding="utf-8").split("gitdir:", 1)[1].strip())
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head
        ref = head.split(" ", 1)[1].strip()
        candidates = [git / ref]
        commondir = git / "commondir"
        if commondir.exists():
            candidates.append((git / commondir.read_text(encoding="utf-8").strip()).resolve() / ref)
        for candidate in candidates:
            if candidate.exists():
                return candidate.read_text(encoding="utf-8").strip()
            packed = candidate.parent
            for _ in range(4):
                packed_refs = packed / "packed-refs"
                if packed_refs.exists():
                    for line in packed_refs.read_text(encoding="utf-8").splitlines():
                        if line.endswith(" " + ref):
                            return line.split(" ", 1)[0]
                packed = packed.parent
    except (OSError, IndexError, ValueError):
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> JSONResponse:
    """`{status, git_sha, tables: {name: rows}}`.

    `tables` reports what is actually on disk under `DATA_ROOT`: a table that has not
    been built is `null` (not omitted, not `0`), so a missing table is visible here
    rather than as a 500 three routes later.
    """
    root = _root()
    tables: dict[str, int | None] = {}
    for name in pipeline.CANONICAL_TABLES:
        rows = None
        try:
            rows = int(data.meta(name, root=root)["rows"])
        except (data.MissingTable, KeyError, TypeError, ValueError):
            try:
                rows = int(len(data.load(name, root=root)))
            except data.MissingTable:
                rows = None
        tables[name] = rows
    required = ("sites", "prices", "weather")
    status = "ok" if all(tables.get(name) is not None for name in required) else "degraded"
    return JSONResponse(
        {
            "status": status,
            "git_sha": _git_sha(),
            "tables": tables,
            "data_root": str(root),
        }
    )


@app.get("/api/assumptions")
def assumptions() -> JSONResponse:
    """`src.market.ASSUMPTIONS`, verbatim. No reformatting, no rounding, no filtering --
    the provenance rule made inspectable in one click."""
    return JSONResponse({key: asdict(value) for key, value in market.ASSUMPTIONS.items()})


@app.get("/api/sites")
def sites(bbox: str | None = None, limit: int | None = None) -> JSONResponse:
    """Site geometry + profile + rated power, for the map.

    `bbox` is `min_lon,min_lat,max_lon,max_lat`; `limit` truncates *after* the bbox
    filter and reports that it did, so a truncated map is never mistaken for a complete
    one.
    """
    root = _root()
    try:
        frame = data.load("sites", root=root)
    except data.MissingTable as exc:
        raise MissingData("the `sites` canonical table is not built", exc.how_to_get_it) from exc

    warns = pipeline.Warnings()
    classify = pipeline._capability(pipeline.fleet, "classify_sites")
    if classify is None:
        warns.add(
            "upstream_unavailable",
            "src/fleet",
            "classify_sites() is not implemented; every site's `profile` is null",
            function="classify_sites",
        )
    else:
        frame = classify(frame)

    total = int(len(frame))
    if bbox is not None:
        parts = bbox.split(",")
        if len(parts) != 4:
            raise BadSpec(
                f"bbox={bbox!r} must be 4 comma-separated numbers",
                "pass bbox=min_lon,min_lat,max_lon,max_lat, e.g. 11.3,48.0,11.8,48.3",
            )
        try:
            min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
        except ValueError as exc:
            raise BadSpec(f"bbox={bbox!r} is not numeric", "pass four numbers") from exc
        frame = frame[
            frame["lon"].between(min_lon, max_lon) & frame["lat"].between(min_lat, max_lat)
        ]
    matched = int(len(frame))
    if limit is not None:
        if limit < 1:
            raise BadSpec(f"limit={limit} must be >= 1", "omit limit to get every site")
        frame = frame.head(int(limit))
        if matched > int(limit):
            warns.add(
                "sites_truncated",
                "src/service",
                f"{matched} sites matched but limit={limit} was applied",
                matched=matched,
                returned=int(len(frame)),
            )

    columns = [
        c
        for c in ("site_id", "operator", "lat", "lon", "postcode", "state", "rated_power_kw",
                  "n_points", "is_dc", "profile")
        if c in frame.columns
    ]
    return JSONResponse(
        cache.jsonable(
            {
                "sites": frame[columns].to_dict("records"),
                "total_sites": total,
                "matched": matched,
                "returned": int(len(frame)),
                "warnings": warns.as_list(),
            }
        )
    )


@app.post("/api/scenario")
async def post_scenario(request: Request) -> JSONResponse:
    """`ScenarioResult` from cache when warm; `202 {job_id, progress, ...}` when cold.

    Polling is a repeat POST of the same spec: the spec *is* the key, so a client never
    has to hold a job id to find its answer again.
    """
    spec = ScenarioSpec.from_dict(await _json_body(request))
    root = _root()

    doc = cache.read(spec.id, root)
    if doc is not None:
        return JSONResponse(ScenarioResult.from_doc(doc).to_dict())

    job = cache.JOBS.get(spec.id)
    if job is not None and job.status == "failed":
        payload, status = job.error, job.error_status
        cache.JOBS.forget(spec.id)  # a repeat POST retries rather than sticking on the failure
        return JSONResponse(status_code=status, content=payload)
    if job is None or job.status == "done":
        job = cache.JOBS.start(
            spec.id, lambda progress: _build_and_keep(spec, root, progress), root=root
        )
    return JSONResponse(status_code=202, content=job.to_dict())


def _build_and_keep(spec: ScenarioSpec, root: Path, progress) -> dict:
    """Run the pipeline for a cold POST and keep the in-process state `/dispatch` needs.

    Without this the background job would discard `live`, and the first `/dispatch` after
    a cold run would re-run every model in the pipeline behind an HTTP request -- the one
    thing issue #28 says a route must never do.
    """
    doc, live = pipeline.build(spec, root=root, progress=progress)
    _LIVE[spec.id] = live
    return doc


def _doc_or_404(scenario_id: str) -> dict:
    doc = cache.read(scenario_id, _root())
    if doc is None:
        job = cache.JOBS.get(scenario_id)
        if job is not None and job.status == "running":
            raise ScenarioNotFound(
                f"scenario {scenario_id} is still being computed ({job.stage}, "
                f"progress={job.progress:.2f})",
                "poll POST /api/scenario with the same spec until it answers 200",
            )
        raise ScenarioNotFound(
            f"no cached scenario with id {scenario_id}",
            "POST /api/scenario with the spec first; the id is the hash of the spec, so "
            "an id only exists once its scenario has been run",
        )
    return doc


@app.get("/api/scenario/{scenario_id}")
def get_scenario(scenario_id: str) -> JSONResponse:
    """The cached `ScenarioResult`.

    Additive to the frozen table (which only has `POST /api/scenario`): `src/voice`'s
    contract calls `tonight_summary() -> GET /api/scenario/{id}`, and a read-only fetch
    by id cannot be expressed as a POST of a spec the voice layer never saw.
    """
    return JSONResponse(ScenarioResult.from_doc(_doc_or_404(scenario_id)).to_dict())


@app.get("/api/scenario/{scenario_id}/timeseries")
def timeseries(scenario_id: str) -> JSONResponse:
    doc = _doc_or_404(scenario_id)
    return JSONResponse(
        {
            "id": scenario_id,
            "rows": doc["timeseries"],
            "warnings": doc["result"]["warnings"],
        }
    )


@app.get("/api/scenario/{scenario_id}/pooling")
def pooling(scenario_id: str) -> JSONResponse:
    doc = _doc_or_404(scenario_id)
    return JSONResponse(
        {
            "id": scenario_id,
            "diversification_curve": doc["pooling"],
            "warnings": doc["result"]["warnings"],
        }
    )


@app.get("/api/scenario/{scenario_id}/map")
def scenario_map(scenario_id: str) -> JSONResponse:
    doc = _doc_or_404(scenario_id)
    payload = dict(doc["map"])
    payload["id"] = scenario_id
    payload["warnings"] = doc["result"]["warnings"]
    return JSONResponse(payload)


@app.get("/api/scenario/{scenario_id}/stream")
def stream(scenario_id: str, interval_ms: int = 0) -> StreamingResponse:
    """SSE tick-by-tick playback of one day -- the demo's "press play".

    Reads the same cached rows `/timeseries` serves; pacing is the client's (`interval_ms`),
    so the server never holds a connection open longer than the caller asked for.
    """
    doc = _doc_or_404(scenario_id)
    rows = doc["timeseries"]
    if interval_ms < 0:
        raise BadSpec("interval_ms must be >= 0", "omit interval_ms to stream at full speed")

    def events():
        import time

        for index, row in enumerate(rows):
            payload = dict(row)
            payload["index"] = index
            payload["n"] = len(rows)
            yield f"event: tick\ndata: {json.dumps(payload, sort_keys=True)}\n\n"
            if interval_ms:
                time.sleep(interval_ms / 1000.0)
        summary = {"id": scenario_id, "n": len(rows), "warnings": doc["result"]["warnings"]}
        yield f"event: end\ndata: {json.dumps(summary, sort_keys=True)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


def _live(scenario_id: str) -> pipeline.LiveScenario:
    """The in-process scenario state, replayed from the cached spec if this process has
    none (a restarted server, or a cache warmed by another worker)."""
    live = _LIVE.get(scenario_id)
    if live is not None:
        return live
    doc = _doc_or_404(scenario_id)
    spec = ScenarioSpec.from_dict(doc["result"]["spec"])
    _, live = pipeline.build(spec, root=_root())
    _LIVE[scenario_id] = live
    return live


def _event_from_body(body: object) -> sched.ReductionEvent:
    if not isinstance(body, dict):
        raise BadSpec("expected a JSON object describing the ReductionEvent",
                      'POST {"call_t": "...", "notice_min": 5, "duration_min": 60, '
                      '"reduction_kw": 100}')
    required = ("call_t", "notice_min", "duration_min", "reduction_kw")
    missing = [k for k in required if k not in body]
    if missing:
        raise BadSpec(
            f"ReductionEvent is missing {missing}",
            f"a ReductionEvent is {list(required)} (src.sched.ReductionEvent)",
        )
    unknown = sorted(set(body) - set(required))
    if unknown:
        raise BadSpec(
            f"unknown ReductionEvent field(s): {unknown}",
            f"a ReductionEvent is exactly {list(required)}",
        )
    try:
        call_t = pd.Timestamp(body["call_t"])
    except (TypeError, ValueError) as exc:
        raise BadSpec(f"call_t={body['call_t']!r} is not a timestamp",
                      "pass an ISO-8601 UTC timestamp, e.g. '2026-03-04T17:00:00+00:00'") from exc
    if call_t.tzinfo is None:
        raise BadSpec(
            f"call_t={body['call_t']!r} is naive",
            "pass a tz-aware timestamp: everything stored or computed in this project is "
            "UTC (contracts/CONVENTIONS.md)",
        )
    numbers = {}
    for key in ("notice_min", "duration_min", "reduction_kw"):
        value = body[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BadSpec(f"{key}={value!r} must be a number", f"pass {key} as a number")
        numbers[key] = float(value)
    if numbers["duration_min"] <= 0:
        raise BadSpec(
            f"duration_min={numbers['duration_min']} is not a compliance window",
            "pass duration_min > 0 -- an end that does not follow its start caps nothing "
            "and would settle as full delivery",
        )
    if numbers["notice_min"] < 0:
        raise BadSpec(f"notice_min={numbers['notice_min']} must be >= 0", "pass notice_min >= 0")
    if numbers["reduction_kw"] < 0:
        raise BadSpec(
            f"reduction_kw={numbers['reduction_kw']} must be >= 0",
            "a reduction event asks for a non-negative kW reduction",
        )
    return sched.ReductionEvent(
        call_t=call_t.tz_convert("UTC").to_pydatetime(),
        notice_min=numbers["notice_min"],
        duration_min=numbers["duration_min"],
        reduction_kw=numbers["reduction_kw"],
    )


@app.post("/api/scenario/{scenario_id}/dispatch")
async def dispatch(scenario_id: str, request: Request) -> JSONResponse:
    """Apply a `ReductionEvent` via `src.sched.dispatch`; return amended timeseries plus
    promised vs delivered.

    Two different measurements sit side by side on purpose, and neither is averaged into
    the other:

    * `delivered_reduction_kw_worst_interval` is measured **here**, by diffing the
      committed and amended portfolio load over the compliance window and taking the
      worst interval -- the physical quantity a firm promise is judged on.
    * `sched_reduction_kw_achieved` is `src/sched`'s own figure, forwarded verbatim. It
      is a per-`(site, t)` worst-interval floor measurement, **not** a portfolio total,
      so it is never summed or averaged across sites here.

    Where they disagree, the discrepancy is visible rather than reconciled.
    """
    live = _live(scenario_id)
    event = _event_from_body(await _json_body(request))

    if live.optimised is None:
        raise ScenarioFailed(
            "this scenario has no feasible optimised schedule, so there is nothing to "
            "dispatch against",
            "see warnings[] for the binding constraint (code `schedule_infeasible`)",
        )

    warns = pipeline.Warnings()
    compliance_start = pd.Timestamp(event.call_t).tz_convert("UTC") + pd.Timedelta(
        minutes=event.notice_min
    )
    compliance_end = compliance_start + pd.Timedelta(minutes=event.duration_min)
    window = live.grid_index[
        (live.grid_index >= compliance_start) & (live.grid_index < compliance_end)
    ]
    if not len(window):
        raise BadSpec(
            f"the compliance window {compliance_start.isoformat()}..{compliance_end.isoformat()} "
            "contains no interval of this scenario's day",
            "place call_t inside the scenario day; the day runs "
            f"{live.grid_index[0].isoformat()}..{live.grid_index[-1].isoformat()}",
        )

    try:
        amended = sched.dispatch(live.optimised, event)
    except sched.Infeasible as exc:
        raise ScenarioFailed(
            f"src.sched.dispatch could not amend the schedule: {exc}",
            "reduce reduction_kw, lengthen notice_min, or pick a window with more load "
            "to shed",
        ) from exc
    except ValueError as exc:
        raise BadSpec(str(exc), "see src.sched.ReductionEvent for the accepted values") from exc

    scheduled_before = pipeline._by_t(
        pipeline._schedule_to_load(live.optimised), "load_kw", live.grid_index
    )
    scheduled_after = pipeline._by_t(
        pipeline._schedule_to_load(amended), "load_kw", live.grid_index
    )
    # The delivered figure is the diff over the sites `src/sched` actually scheduled. The
    # sites it could not are added to *both* reported curves (they charge the same either
    # way), so the rows show the whole portfolio while the measurement stays on what moved.
    unscheduled = live.unscheduled_by_t
    if unscheduled is None:
        unscheduled = pd.Series(0.0, index=live.grid_index)
    before = scheduled_before + unscheduled
    after = scheduled_after + unscheduled
    reduction = (scheduled_before - scheduled_after).reindex(live.grid_index)
    in_window = reduction.loc[window]

    delivered_worst = float(in_window.min())
    delivered_mean = float(in_window.mean())
    shortfall = max(0.0, float(event.reduction_kw) - delivered_worst)

    if shortfall > 1e-6:
        warns.add(
            "dispatch_under_delivered",
            "src/service",
            f"the amended schedule sheds {delivered_worst:.3f} kW in its worst interval "
            f"against a {event.reduction_kw:.3f} kW call; the shortfall is real, not a "
            "solver artefact -- it is a diff of the two schedules",
            promised_kw=float(event.reduction_kw),
            delivered_kw=delivered_worst,
            shortfall_kw=shortfall,
        )

    rows = [
        {
            "t": t.isoformat(),
            "load_kw_committed": pipeline._f(before.loc[t]),
            "load_kw_amended": pipeline._f(after.loc[t]),
            "reduction_kw": pipeline._f(reduction.loc[t]),
            "in_compliance_window": bool(t in window),
        }
        for t in live.grid_index
    ]

    return JSONResponse(
        cache.jsonable(
            {
                "id": scenario_id,
                "event": {
                    "call_t": pd.Timestamp(event.call_t).tz_convert("UTC").isoformat(),
                    "notice_min": event.notice_min,
                    "duration_min": event.duration_min,
                    "reduction_kw": event.reduction_kw,
                },
                "compliance_window": {
                    "start": compliance_start.isoformat(),
                    "end": compliance_end.isoformat(),
                    "intervals": int(len(window)),
                },
                "promised_reduction_kw": float(event.reduction_kw),
                "delivered_reduction_kw_worst_interval": delivered_worst,
                "delivered_reduction_kw_mean": delivered_mean,
                "shortfall_kw": shortfall,
                "delivered_measurement": (
                    "min over the compliance window of (committed portfolio load - amended "
                    "portfolio load); a diff of two schedules, not a solver feasibility flag"
                ),
                "sched_reduction_kw_achieved": pipeline._f(
                    amended.attrs.get("reduction_kw_achieved")
                ),
                "sched_reduction_shortfall_kw": pipeline._f(
                    amended.attrs.get("reduction_shortfall_kw")
                ),
                "sched_reduction_kw_achieved_note": (
                    "src/sched's own measurement: the worst per-(site, t) reduction across the "
                    "window. It is a per-site floor, NOT a portfolio total -- it is reported "
                    "beside the portfolio figure, never summed or averaged into it."
                ),
                "rows": rows,
                "warnings": live.warnings + warns.as_list(),
            }
        )
    )


__all__ = [
    "LANE",
    "ScenarioResult",
    "ScenarioSpec",
    "app",
    "run_scenario",
    "BadSpec",
    "MissingData",
    "ScenarioFailed",
    "ScenarioNotFound",
    "ServiceError",
    "UpstreamUnavailable",
]
