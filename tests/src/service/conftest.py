"""Fixture builders for tests/src/service.

The lane's verify command has to pass on a machine that has never downloaded a dataset
(contracts/CONVENTIONS.md), so every test here runs against a **3-site canonical data
root built in a tmp dir**: real `data/canonical/<table>.parquet` files written in the
shapes `contracts/src/data.md` documents, read back through `src.data.load()` exactly as
the service does in production. Nothing is monkeypatched into `src/data`.

The tables are deliberately built by a parametrised builder rather than as one frozen
fixture: several guarantees in `contracts/src/service.md` are only checkable by running
the *same* pipeline twice, once with a table (or a column, or a station) present and once
without, so that a warning is proven to move rather than merely to exist.
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

# a value that must never appear in any response
SECRET_VALUE = "sk-elevenlabs-not-a-real-key-0123456789"


def span_index(day: str = DAY, history_days: int = HISTORY_DAYS) -> pd.DatetimeIndex:
    """The full UTC 15-minute grid the pipeline needs: history + the scenario day."""
    d = date.fromisoformat(day)
    start = pd.Timestamp(d - timedelta(days=history_days), tz=LOCAL_TZ).tz_convert("UTC")
    end = pd.Timestamp(d + timedelta(days=1), tz=LOCAL_TZ).tz_convert("UTC")
    return pd.date_range(start, end, freq="15min", inclusive="left", tz="UTC")


def day_index(day: str = DAY) -> pd.DatetimeIndex:
    d = date.fromisoformat(day)
    start = pd.Timestamp(d, tz=LOCAL_TZ).tz_convert("UTC")
    end = pd.Timestamp(d + timedelta(days=1), tz=LOCAL_TZ).tz_convert("UTC")
    return pd.date_range(start, end, freq="15min", inclusive="left", tz="UTC")


# ---------------------------------------------------------------------------
# canonical table builders
# ---------------------------------------------------------------------------


def sites_frame(n: int = 3) -> pd.DataFrame:
    """`contracts/src/data.md`'s `sites` columns, three deliberately different sites.

    Site 0 is AC/workplace-shaped, site 1 DC-fast, site 2 a depot operator, so
    `src/fleet`'s three profile branches are all exercised by one portfolio.
    """
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
            "site_id": "BY-80339-bbbb0002",
            "operator": "Ionity",
            "lat": 48.140,
            "lon": 11.540,
            "postcode": "80339",
            "state": "BY",
            "rated_power_kw": 300.0,
            "n_points": 4,
            "is_dc": True,
            "commissioned": "2023-06-01",
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
    """A daily price shape with a real spread, so shifting load is worth something."""
    hours = index.tz_convert("UTC").hour + index.tz_convert("UTC").minute / 60.0
    price = 80.0 + 45.0 * np.sin((hours - 4.0) / 24.0 * 2 * np.pi)
    return pd.DataFrame({"t": index, "price_eur_mwh": price})


def weather_frame(index: pd.DatetimeIndex, stations: int = 1) -> pd.DataFrame:
    """`t, station_id -> temp_c, wind_ms, ghi_w_m2`; `stations > 1` forces the
    area-averaging substitution the pipeline has to report."""
    frames = []
    hours = index.tz_convert("UTC").hour + index.tz_convert("UTC").minute / 60.0
    for s in range(stations):
        frames.append(
            pd.DataFrame(
                {
                    "t": index,
                    "station_id": f"ST{s:03d}",
                    "temp_c": 4.0 + 6.0 * np.sin((hours - 9.0) / 24.0 * 2 * np.pi) + 2.0 * s,
                    "wind_ms": 3.0,
                    "ghi_w_m2": np.clip(400.0 * np.sin((hours - 6.0) / 12.0 * np.pi), 0.0, None),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def carbon_frame(index: pd.DatetimeIndex) -> pd.DataFrame:
    hours = index.tz_convert("UTC").hour + index.tz_convert("UTC").minute / 60.0
    return pd.DataFrame(
        {"t": index, "intensity_g_kwh": 320.0 + 90.0 * np.sin((hours - 3.0) / 24.0 * 2 * np.pi)}
    )


def balancing_frame(index: pd.DatetimeIndex, *, product: str = "aFRR") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "t": index,
            "product": product,
            "direction": "POS",
            "capacity_price_eur_mw_h": 12.0,
            "energy_price_eur_mwh": 95.0,
        }
    )


def write_table(root, table: str, frame: pd.DataFrame) -> None:
    """Write `frame` as `data/canonical/<table>.parquet` plus its provenance sidecar."""
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


def build_root(
    root,
    *,
    day: str = DAY,
    n_sites: int = 3,
    stations: int = 1,
    carbon: bool = True,
    balancing: bool = True,
    balancing_columns: tuple[str, ...] | None = None,
    balancing_product: str = "aFRR",
):
    """Build a canonical data root. Every keyword turns one table (or one column, or one
    station) on or off, which is how a warning gets pinned at both ends."""
    index = span_index(day)
    root.mkdir(parents=True, exist_ok=True)
    write_table(root, "sites", sites_frame(n_sites))
    write_table(root, "prices", prices_frame(index))
    write_table(root, "weather", weather_frame(index, stations=stations))
    if carbon:
        write_table(root, "carbon", carbon_frame(index))
    if balancing:
        frame = balancing_frame(index, product=balancing_product)
        if balancing_columns is not None:
            frame = frame[list(balancing_columns)]
        write_table(root, "balancing", frame)
    return root


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def full_root(tmp_path_factory):
    """A complete data root: every table present, one weather station."""
    return build_root(tmp_path_factory.mktemp("flexgrid-full"))


@pytest.fixture
def spec() -> ScenarioSpec:
    return ScenarioSpec(date=DAY, n_sites=3, seed=7)


@pytest.fixture
def rooted(monkeypatch):
    """Point the whole service (cache + `src.data` reads) at one data root.

    `FLEXGRID_DATA` rather than a monkeypatched attribute: `cache.data_root()` reads the
    environment on every call precisely so a test and the app cannot disagree about which
    root they are using.
    """

    def _use(root):
        monkeypatch.setenv("FLEXGRID_DATA", str(root))
        cache.JOBS.clear()
        return root

    return _use


@pytest.fixture(autouse=True)
def _no_secret_in_env(monkeypatch):
    """A secret is in the process environment for every test, so "no secrets in
    responses" is an assertion about the service and not about an empty environment."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", SECRET_VALUE)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

#: a portfolio every site of which `src/sched` can schedule
FEASIBLE = {"date": DAY, "n_sites": 2, "seed": 7}
#: the same day with the DC fast-charging site added -- its short dwells are infeasible
#: on `src/sched`'s 15-minute grid, which is the contract's "infeasible site" case
WITH_INFEASIBLE_SITE = {"date": DAY, "n_sites": 3, "seed": 7}


@pytest.fixture
def client(rooted, full_root):
    """A `TestClient` bound to the complete data root."""
    from fastapi.testclient import TestClient

    from src.service import api

    rooted(full_root)
    return TestClient(api.app)


@pytest.fixture
def client_on(rooted):
    """`client_on(root)` -> a `TestClient` bound to any data root."""
    from fastapi.testclient import TestClient

    from src.service import api

    def _make(root, **kwargs):
        rooted(root)
        return TestClient(api.app, **kwargs)

    return _make


def warm(client, body, timeout_s: float = 60.0):
    """POST a spec and poll until the scenario is warm; returns the `ScenarioResult`.

    Polling is a repeat POST of the same spec (the spec is the cache key), which is the
    flow `src/ui` uses.
    """
    import time

    response = client.post("/api/scenario", json=body)
    deadline = time.monotonic() + timeout_s
    while response.status_code == 202 and time.monotonic() < deadline:
        time.sleep(0.02)
        response = client.post("/api/scenario", json=body)
    assert response.status_code == 200, f"{response.status_code}: {response.text}"
    return response.json()
