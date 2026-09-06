"""The cache key is the spec hash, and a cold scenario answers 202 rather than blocking.

Two directions matter and both are tested: the same spec must return the same result
(otherwise the cache is not a cache), and a spec that differs in *any single field* must
land on a different key (otherwise two different questions silently share one answer).
"""

from __future__ import annotations

import hashlib
import json
import time

import pytest

from src.service import _cache as cache
from src.service import _pipeline as pipeline
from src.service.api import ScenarioResult, run_scenario
from src.service._spec import ScenarioSpec

from .conftest import DAY, FEASIBLE, build_root, warm


def test_the_id_is_the_sha256_of_the_canonical_spec_json():
    """Recomputed here from the spec's own fields, not read back off the object."""
    spec = ScenarioSpec(date=DAY, n_sites=2, seed=7)
    payload = {
        "date": DAY,
        "site_ids": None,
        "region": None,
        "n_sites": 2,
        "seed": 7,
        "product": "aFRR",
        "tau": 0.05,
        "policy": "optimised",
        "pool_method": "empirical",
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert spec.id == hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def test_the_cache_file_is_the_spec_hash_under_data_derived_service(rooted, full_root):
    rooted(full_root)
    spec = ScenarioSpec(date=DAY, n_sites=2, seed=7)
    run_scenario(spec)
    path = full_root / "derived" / "service" / f"{spec.id}.json"
    assert path.exists(), f"expected the cache at {path}"
    assert json.loads(path.read_text())["result"]["id"] == spec.id


def test_the_same_spec_returns_byte_identical_json(rooted, tmp_path):
    """Determinism, run twice into two independent roots so the second is a real re-run
    of the pipeline and not a cache read."""
    first_root = build_root(tmp_path / "det-a")
    second_root = build_root(tmp_path / "det-b")
    spec = ScenarioSpec(date=DAY, n_sites=2, seed=7)

    rooted(first_root)
    first = run_scenario(spec)
    rooted(second_root)
    second = run_scenario(spec)

    assert first.id == second.id == spec.id
    a = (first_root / "derived" / "service" / f"{spec.id}.json").read_text()
    b = (second_root / "derived" / "service" / f"{spec.id}.json").read_text()
    assert a == b, "two runs of the same spec produced different JSON"


@pytest.mark.parametrize(
    "field,value",
    [
        ("date", "2026-03-05"),
        ("seed", 8),
        ("product", "mFRR"),
        ("tau", 0.1),
        ("policy", "baseline"),
        ("pool_method", "gaussian_copula"),
        ("n_sites", 3),
    ],
)
def test_changing_one_field_changes_the_id(field, value):
    base = ScenarioSpec(date=DAY, n_sites=2, seed=7)
    changed = ScenarioSpec(**{**base.to_dict(), field: value, "site_ids": None})
    assert changed.id != base.id, f"{field} does not take part in the cache key"


def test_different_portfolio_selectors_do_not_collide():
    ids = {
        ScenarioSpec(date=DAY, n_sites=2, seed=7).id,
        ScenarioSpec(date=DAY, region="BY", seed=7).id,
        ScenarioSpec(date=DAY, site_ids=("BY-80331-aaaa0001",), seed=7).id,
        ScenarioSpec(date=DAY, site_ids=("BY-80331-aaaa0001", "BW-70173-cccc0003"), seed=7).id,
    }
    assert len(ids) == 4


def test_two_different_specs_do_not_share_one_answer(client):
    """The failure the hash exists to prevent, checked end to end over HTTP."""
    two_sites = warm(client, FEASIBLE)
    other_seed = warm(client, {**FEASIBLE, "seed": 8})
    assert two_sites["id"] != other_seed["id"]
    assert two_sites["spec"]["seed"] == 7 and other_seed["spec"]["seed"] == 8


def test_a_cold_scenario_answers_202_with_a_job_id_and_progress(client):
    """"A cold scenario returns 202 with a job id and a progress field rather than
    blocking a request for minutes."""
    body = {**FEASIBLE, "seed": 4242}  # a spec no other test has warmed
    response = client.post("/api/scenario", json=body)
    assert response.status_code == 202
    payload = response.json()
    assert payload["job_id"]
    assert payload["status"] == "running"
    assert 0.0 <= payload["progress"] <= 1.0
    assert payload["stage"]  # a named stage, not a timer

    result = warm(client, body)
    assert result["id"] == payload["id"]
    assert client.post("/api/scenario", json=body).status_code == 200


def test_progress_names_the_stage_it_reached(rooted, full_root):
    """`progress` is stages completed, so it must move and end at 1.0 -- not a timer."""
    rooted(full_root)
    seen: list[tuple[float, str]] = []
    spec = ScenarioSpec(date=DAY, n_sites=2, seed=99)
    pipeline.build(spec, root=full_root, progress=lambda f, s: seen.append((float(f), s)))
    assert seen, "the pipeline reported no progress at all"
    fractions = [f for f, _ in seen]
    assert fractions == sorted(fractions)
    assert fractions[-1] == 1.0
    assert {"data:sites", "forecast:fit", "sched:schedule", "done"} <= {s for _, s in seen}


def test_a_warm_route_never_re_runs_a_model(client, monkeypatch):
    """"A route must never re-run a model" (issue #28): with the pipeline booby-trapped,
    every warm route still answers from the cache."""
    result = warm(client, FEASIBLE)

    def explode(*args, **kwargs):
        raise AssertionError("a warm request re-ran the pipeline")

    monkeypatch.setattr(pipeline, "build", explode)
    assert client.post("/api/scenario", json=FEASIBLE).status_code == 200
    for route in ("", "/timeseries", "/pooling", "/map", "/stream"):
        assert client.get(f"/api/scenario/{result['id']}{route}").status_code == 200


def test_the_result_handed_back_is_what_the_routes_serve(client):
    """`run_scenario` reads its answer back out of the cache, so the Python API and the
    HTTP API cannot drift apart."""
    result = warm(client, FEASIBLE)
    served = client.get(f"/api/scenario/{result['id']}").json()
    assert served == result


def test_a_failed_cold_run_is_retried_rather_than_stuck(client_on, tmp_path, monkeypatch):
    """A recorded failure must not become this process's permanent answer."""
    root = build_root(tmp_path / "retry")
    client = client_on(root)
    body = {**FEASIBLE, "seed": 31337}

    calls = {"n": 0}
    real_build = pipeline.build

    def flaky(spec, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return real_build(spec, **kwargs)

    monkeypatch.setattr(pipeline, "build", flaky)

    first = client.post("/api/scenario", json=body)
    assert first.status_code == 202
    deadline = time.monotonic() + 30
    while client.post("/api/scenario", json=body).status_code == 202:
        assert time.monotonic() < deadline
        time.sleep(0.02)

    monkeypatch.setattr(pipeline, "build", real_build)
    assert warm(client, body)["id"]
    assert calls["n"] >= 1
