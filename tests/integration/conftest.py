"""Fixture builders for tests/integration.

`tests/integration/` is the cross-lane tier described in `tests/integration/README.md`
and `contracts/CONVENTIONS.md` ("Cross-lane integration tier"): unlike `tests/src/<lane>/`,
which may import only its own lane's `api.py` (contracts/CONVENTIONS.md, "Python"), this
directory is allowed to import across lanes and is deliberately kept separate from any
one lane's confined test tree so no `claim/*` PR can touch it (see `.github/workflows/checks.yml`'s
"files stay in lane" step).

This file intentionally does NOT import `tests/src/service/conftest.py`: that module is
lane-confined (`tests/README.md`: "ONLY that lane's claim may touch them"), and coupling
this tier to its internals would let an in-lane refactor of `src/service`'s tests break
cross-lane coverage silently. The fixture-building code below is a small, self-contained
duplicate of the same idea -- real `data/canonical/<table>.parquet` files, written in the
shapes `contracts/src/data.md` documents, read back through `src.data.load()` exactly as
the service does in production. Nothing is monkeypatched into `src/data`.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.service import _cache as cache
from src.service._pipeline import HISTORY_DAYS
from src.service._spec import ScenarioSpec

DAY = "2026-03-04"  # a plain winter Wednesday; no DST transition
LOCAL_TZ = "Europe/Berlin"

# A portfolio every site of which `src/sched` can schedule (the DC-fast site's short
# dwells are infeasible on the 15-minute grid -- see tests/src/service/conftest.py's
# WITH_INFEASIBLE_SITE for that case, which is this lane's, not this tier's, to cover).
FEASIBLE_SPEC = {"date": DAY, "n_sites": 2, "seed": 7}


def span_index(day: str = DAY, history_days: int = HISTORY_DAYS) -> pd.DatetimeIndex:
    d = date.fromisoformat(day)
    start = pd.Timestamp(d - timedelta(days=history_days), tz=LOCAL_TZ).tz_convert("UTC")
    end = pd.Timestamp(d + timedelta(days=1), tz=LOCAL_TZ).tz_convert("UTC")
    return pd.date_range(start, end, freq="15min", inclusive="left", tz="UTC")


def sites_frame(n: int = 2) -> pd.DataFrame:
    rows = [
        {
            "site_id": "BY-80331-aaaa0001",
            "operator": "Stadtwerke Muenchen",
            "lat": 48.137,
            "lon": 11.575,
            "postcode": "80331",
            "state": "BY",
            "rated_power_kw": 44.0,
            "n_points": 2,
            "is_dc": False,
            "commissioned": "2022-01-01",
        },
        {
            "site_id": "BW-70173-cccc0003",
            "operator": "Deutsche Post Fleet Depot",
            "lat": 48.778,
            "lon": 9.180,
            "postcode": "70173",
            "state": "BW",
            "rated_power_kw": 66.0,
            "n_points": 6,
            "is_dc": False,
            "commissioned": "2021-03-01",
        },
    ]
    return pd.DataFrame(rows[:n])


def prices_frame(index: pd.DatetimeIndex) -> pd.DataFrame:
    hours = index.tz_convert("UTC").hour + index.tz_convert("UTC").minute / 60.0
    price = 80.0 + 45.0 * np.sin((hours - 4.0) / 24.0 * 2 * np.pi)
    return pd.DataFrame({"t": index, "price_eur_mwh": price})


def weather_frame(index: pd.DatetimeIndex) -> pd.DataFrame:
    hours = index.tz_convert("UTC").hour + index.tz_convert("UTC").minute / 60.0
    return pd.DataFrame(
        {
            "t": index,
            "station_id": "ST000",
            "temp_c": 4.0 + 6.0 * np.sin((hours - 9.0) / 24.0 * 2 * np.pi),
            "wind_ms": 3.0,
            "ghi_w_m2": np.clip(400.0 * np.sin((hours - 6.0) / 12.0 * np.pi), 0.0, None),
        }
    )


def carbon_frame(index: pd.DatetimeIndex) -> pd.DataFrame:
    hours = index.tz_convert("UTC").hour + index.tz_convert("UTC").minute / 60.0
    return pd.DataFrame(
        {"t": index, "intensity_g_kwh": 320.0 + 90.0 * np.sin((hours - 3.0) / 24.0 * 2 * np.pi)}
    )


def balancing_frame(index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "t": index,
            "product": "aFRR",
            "direction": "POS",
            "capacity_price_eur_mw_h": 12.0,
            "energy_price_eur_mwh": 95.0,
        }
    )


def write_table(root, table: str, frame: pd.DataFrame) -> None:
    canonical = root / "canonical"
    canonical.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(canonical / f"{table}.parquet", index=False)
    (canonical / f"{table}.meta.json").write_text(
        json.dumps(
            {
                "source_url": f"test://fixture/{table}",
                "retrieved_at": "2026-09-06T00:00:00+00:00",
                "rows": int(len(frame)),
                "resolution_min": 15,
                "license": "test fixture",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def build_root(root, *, day: str = DAY, n_sites: int = 2):
    """A complete canonical data root: every table `_pipeline.build()` reads, real
    enough that `src.market.pool()`/`diversification_curve()` run for real -- no lane
    is stubbed out at this boundary, which is the entire point of this tier."""
    index = span_index(day)
    root.mkdir(parents=True, exist_ok=True)
    write_table(root, "sites", sites_frame(n_sites))
    write_table(root, "prices", prices_frame(index))
    write_table(root, "weather", weather_frame(index))
    write_table(root, "carbon", carbon_frame(index))
    write_table(root, "balancing", balancing_frame(index))
    return root


@pytest.fixture(scope="session")
def full_root(tmp_path_factory):
    return build_root(tmp_path_factory.mktemp("flexgrid-integration"))


@pytest.fixture
def feasible_spec() -> ScenarioSpec:
    return ScenarioSpec(**FEASIBLE_SPEC)


@pytest.fixture
def rooted(monkeypatch):
    def _use(root):
        monkeypatch.setenv("FLEXGRID_DATA", str(root))
        cache.JOBS.clear()
        return root

    return _use


@pytest.fixture
def client(rooted, full_root):
    from fastapi.testclient import TestClient

    from src.service import api

    rooted(full_root)
    return TestClient(api.app)


def warm(client, body: dict, timeout_s: float = 60.0) -> dict:
    """POST a spec and poll until the scenario is warm; returns the raw JSON body.

    Polling is a repeat POST of the same spec (the spec is the cache key) -- the same
    flow `src/ui` is documented to use.
    """
    import time

    response = client.post("/api/scenario", json=body)
    deadline = time.monotonic() + timeout_s
    while response.status_code == 202 and time.monotonic() < deadline:
        time.sleep(0.02)
        response = client.post("/api/scenario", json=body)
    return response
