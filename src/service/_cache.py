"""Private: the on-disk scenario cache and the cold-run job registry.

`contracts/src/service.md`: the cache key is the spec hash, stored under
`data/derived/service/<hash>.json`; a cold scenario returns 202 with a job id and a
`progress` field rather than blocking the request. Both live here so no route ever
re-runs a model.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

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


def write(scenario_id: str, doc: dict, root: Path | None = None) -> Path:
    path = cache_path(scenario_id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    tmp.write_text(dumps(doc), encoding="utf-8")
    os.replace(tmp, path)  # atomic: a reader never sees a half-written scenario
    return path


def read(scenario_id: str, root: Path | None = None) -> dict | None:
    path = cache_path(scenario_id, root)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


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
                doc = target(progress)
                write(scenario_id, doc, root)
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
