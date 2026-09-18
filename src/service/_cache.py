"""Private: the on-disk scenario cache and the cold-run job registry.

`contracts/src/service.md`: the cache key is the spec hash, stored under
`data/derived/service/<hash>.json`; a cold scenario returns 202 with a job id and a
`progress` field rather than blocking the request. Both live here so no route ever
re-runs a model.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from src.data import api as data

_REPO_ROOT = Path(__file__).resolve().parents[2]


def data_root() -> Path:
    """`FLEXGRID_DATA` or `<repo>/data`, resolved on every call.

    Read at call time, not at import: `src.data.api.DATA_ROOT` is bound at import, so a
    process that sets the env var afterwards (a test, a demo box) would otherwise write
    its cache to one root and read canonical tables from another.
    """
    env = os.environ.get("FLEXGRID_DATA")
    return Path(env).expanduser() if env else _REPO_ROOT / "data"


def cache_dir(root: Path | None = None) -> Path:
    return (Path(root) if root is not None else data_root()) / "derived" / "service"


def cache_path(scenario_id: str, root: Path | None = None) -> Path:
    return cache_dir(root) / f"{scenario_id}.json"


# ---------------------------------------------------------------------------
# input identity (issue #48.1)
# ---------------------------------------------------------------------------

# Every canonical table, deliberately a SUPERSET of the five `pipeline.build` reads
# today (`grid_load` is in the inventory but not currently loaded by a scenario).
# Fingerprinting it means a `grid_load` rebuild invalidates scenarios whose numbers
# could not have changed -- a real cost, accepted knowingly, because the two failure
# directions are not symmetric: over-invalidating is a slow correct answer, and
# under-invalidating is a fast wrong one with no signal, which is the entire defect
# this module is fixing. Keeping one tuple shared with `_pipeline.CANONICAL_TABLES`
# also means a lane that starts reading a table cannot forget to fingerprint it.
FINGERPRINTED_TABLES = ("sites", "grid_load", "prices", "weather", "balancing", "carbon")

# Top-level key holding the fingerprint of the inputs a cached document was computed
# from. Deliberately outside `doc["result"]`: it is provenance about the document, not
# part of the contract's result shape (`ScenarioResult.from_doc` reads only `result`).
INPUTS_KEY = "inputs_fingerprint"


def inputs_fingerprint(root: Path | None = None) -> str:
    """Short hash of the *identity* of every canonical table a scenario can read.

    `ScenarioSpec.id` hashes the request and nothing else, so it is unchanged when the
    tables underneath change -- which is exactly what wiring real data does. Without
    this, every scenario cached during the synthetic era stays warm and keeps serving
    pre-real numbers with no signal that anything moved (issue #48.1).

    Identity comes from each table's provenance sidecar (`src.data.meta`: source url,
    licence, `retrieved_at`, row count, resolution, and the raw file's sha256 where the
    canonicaliser records one), not from the parquet bytes -- reading six sidecars is
    cheap enough to do on every cache hit, and re-running `canonicalise()` against
    unchanged raw files is a no-op on provenance, so an unchanged table keeps its
    fingerprint.

    An absent table is part of the identity too (recorded as `None`): "no `carbon`
    table" and "a `carbon` table" are different worlds and must not share an answer.
    An unreadable sidecar is recorded as its error rather than swallowed -- the one
    thing this must never do is read as "same inputs" when it does not know.
    """
    resolved = Path(root) if root is not None else data_root()
    identity: dict[str, object] = {}
    for table in FINGERPRINTED_TABLES:
        try:
            identity[table] = data.meta(table, root=resolved)
        except data.MissingTable:
            identity[table] = None
        except (OSError, ValueError) as exc:  # unreadable/corrupt sidecar
            # Deliberately unique per call. Collapsing this to the exception class
            # would give two *different* unknown states one identity, so a document
            # written while a sidecar was corrupt would validate later while it was
            # still corrupt -- asserting validity exactly where provenance is unknown.
            # A never-equal value turns caching off for as long as the sidecar cannot
            # be read, which is the safe direction: every read misses and recomputes.
            identity[table] = f"unreadable:{type(exc).__name__}:{uuid.uuid4().hex}"
    blob = json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def jsonable(obj):
    """numpy/pandas -> plain JSON types, with NaN made explicit rather than silent.

    NaN is *not* mapped to 0.0 or dropped: it becomes `null`, so "we do not know" and
    "it is zero" stay different things on the wire (contracts/src/service.md).
    """
    if obj is None:
        return None
    if isinstance(obj, (str, bool, int)) and not isinstance(obj, np.generic):
        return obj
    if isinstance(obj, float):
        return None if (np.isnan(obj) or np.isinf(obj)) else obj
    if isinstance(obj, np.generic):
        return jsonable(obj.item())
    if isinstance(obj, (pd.Timestamp,)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, np.ndarray)):
        return [jsonable(v) for v in obj]
    if obj is pd.NaT:
        return None
    return str(obj)


def dumps(doc: dict) -> str:
    """Canonical, deterministic JSON. `allow_nan=False` so a NaN that escaped
    `jsonable` raises here instead of emitting non-standard `NaN` into a judge's
    browser."""
    return json.dumps(jsonable(doc), sort_keys=True, indent=2, allow_nan=False) + "\n"


def write(
    scenario_id: str,
    doc: dict,
    root: Path | None = None,
    *,
    expect_fingerprint: str | None = None,
) -> Path | None:
    """Stamps the inputs' fingerprint into the document as it is written, so a cached
    scenario always states which tables it was computed from (issue #48.1). The
    caller's dict is not mutated.

    `expect_fingerprint` is the identity the caller *read its inputs under*, captured
    before the pipeline ran. If the tables have moved since, this writes nothing and
    returns `None`: the document holds numbers from the previous generation, and
    stamping it with the current identity would mint exactly the lie this module
    exists to prevent -- a document that validates forever and was never computed from
    what it claims. Caching nothing is the safe outcome; the caller still has its
    result, it simply does not become the answer every later request is given.
    """
    if expect_fingerprint is not None and inputs_fingerprint(root) != expect_fingerprint:
        return None
    path = cache_path(scenario_id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped = {**doc, INPUTS_KEY: inputs_fingerprint(root)}
    tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    tmp.write_text(dumps(stamped), encoding="utf-8")
    os.replace(tmp, path)  # atomic: a reader never sees a half-written scenario
    return path


def read(scenario_id: str, root: Path | None = None, *, verify_inputs: bool = True) -> dict | None:
    """The cached document, or `None` if there is none *that is still valid*.

    A document whose stored fingerprint does not match the inputs on disk right now is
    reported as a miss rather than served: the numbers in it were computed from
    different tables, and the caller's own recompute path is the only thing that can
    produce an answer for the tables that are actually there. A document written before
    this key existed carries no fingerprint and is therefore also a miss -- it was
    computed against inputs nobody recorded, which is precisely the state this guards.

    `verify_inputs=False` is for the read-back immediately after `write`, where the
    fingerprint was just stamped by this process and re-deriving it would only open a
    window for a race to turn a fresh write into a miss.
    """
    path = cache_path(scenario_id, root)
    if not path.exists():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    if verify_inputs and doc.get(INPUTS_KEY) != inputs_fingerprint(root):
        return None
    return doc


# ---------------------------------------------------------------------------
# cold-run jobs
# ---------------------------------------------------------------------------


@dataclass
class Job:
    """One cold scenario run. `progress` is a fraction of *stages completed*, set by the
    pipeline as it finishes each one -- not a timer, so it cannot report progress a run
    is not making."""

    job_id: str
    scenario_id: str
    status: str = "running"  # running | done | failed
    progress: float = 0.0
    stage: str = "queued"
    error: dict | None = field(default=None)
    error_status: int = 500

    def to_dict(self) -> dict:
        out = {
            "job_id": self.job_id,
            "id": self.scenario_id,
            "status": self.status,
            "progress": round(float(self.progress), 4),
            "stage": self.stage,
        }
        if self.error is not None:
            out["error_detail"] = self.error
        return out


class JobRegistry:
    """Process-local registry of in-flight cold runs, keyed by scenario id."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_scenario: dict[str, Job] = {}

    def get(self, scenario_id: str) -> Job | None:
        with self._lock:
            return self._by_scenario.get(scenario_id)

    def start(self, scenario_id: str, target, *, root: Path | None = None) -> Job:
        """Return the running job for `scenario_id`, starting one if there is none.

        Two concurrent POSTs of the same cold spec must share one run: a second thread
        would duplicate several seconds of model fitting and race the other on the cache
        file.
        """
        with self._lock:
            existing = self._by_scenario.get(scenario_id)
            if existing is not None and existing.status == "running":
                return existing
            job = Job(job_id=uuid.uuid4().hex[:12], scenario_id=scenario_id)
            self._by_scenario[scenario_id] = job

        def progress(fraction: float, stage: str) -> None:
            job.progress = float(fraction)
            job.stage = stage

        def run() -> None:
            try:
                # captured before the pipeline reads a single table, so a rebuild
                # landing mid-run is caught rather than stamped over (PR #61 review)
                expected = inputs_fingerprint(root)
                doc = target(progress)
                if write(scenario_id, doc, root, expect_fingerprint=expected) is None:
                    job.error = {
                        "error": "inputs_changed_during_run",
                        "detail": (
                            "a canonical table was rebuilt while this scenario was "
                            "being computed, so its numbers come from tables that are "
                            "no longer on disk; nothing was cached"
                        ),
                        "how_to_fix": "POST the same spec again to run it against the current tables",
                    }
                    job.error_status = 409
                    job.status = "failed"
                    return
                job.progress, job.stage, job.status = 1.0, "done", "done"
            except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised to the poller
                from ._errors import ServiceError

                if isinstance(exc, ServiceError):
                    job.error = exc.to_dict()
                    job.error_status = exc.status_code
                else:
                    job.error = {
                        "error": "scenario_failed",
                        "detail": f"{type(exc).__name__}: {exc}",
                        "how_to_fix": (
                            "this is a bug or a bad input in the pipeline this scenario "
                            "composes; the failing stage is named in `stage`"
                        ),
                    }
                    job.error_status = 500
                job.status = "failed"

        thread = threading.Thread(target=run, name=f"scenario-{scenario_id}", daemon=True)
        thread.start()
        job.thread = thread  # type: ignore[attr-defined]
        return job

    def forget(self, scenario_id: str) -> None:
        """Drop a job so a repeat request starts a fresh run rather than replaying a
        recorded failure for the rest of the process's life."""
        with self._lock:
            self._by_scenario.pop(scenario_id, None)

    def clear(self) -> None:
        with self._lock:
            self._by_scenario.clear()


JOBS = JobRegistry()
