"""Regression coverage for the grouped prior-load lookup in `_build_envelope`."""

from __future__ import annotations

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from src.grid import api as grid
from src.service import _pipeline as pipeline
from src.service._spec import ScenarioSpec

from .conftest import DAY, day_index, sites_frame, weather_frame


def test_build_envelope_matches_per_site_prior_filter() -> None:
    sites = sites_frame()
    spec = ScenarioSpec(date=DAY, n_sites=3, seed=7)
    scenario_index = day_index()
    day_start = scenario_index[0]
    prior_index = pd.date_range(
        day_start - pd.Timedelta(days=1),
        day_start,
        freq="15min",
        inclusive="left",
        tz="UTC",
    )
    base_load = pd.concat(
        [
            pd.DataFrame(
                {
                    "t": prior_index,
                    "site_id": sites.iloc[0]["site_id"],
                    "load_kw": 8.0,
                }
            ),
            pd.DataFrame(
                {
                    "t": prior_index,
                    "site_id": sites.iloc[1]["site_id"],
                    "load_kw": np.where(np.arange(len(prior_index)) == 10, np.nan, 5.0),
                }
            ),
        ],
        ignore_index=True,
    )
    weather = weather_frame(scenario_index)

    grouped_warns = pipeline.Warnings()
    grouped, grouped_rate = pipeline._build_envelope(
        sites, spec, weather, base_load, scenario_index, day_start, grouped_warns
    )

    electrical = grid.infer_electrical(sites, seed=spec.seed)
    ambient = weather.set_index("t")["temp_c"].reindex(scenario_index).astype(float)
    prior_window_start = day_start - pd.Timedelta(days=1)
    reference_frames = []
    clipped_num = 0.0
    clipped_den = 0
    n_no_prior = 0
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
        reference_frames.append(env[["site_id", "t", "max_kw"]])

    reference_warns = pipeline.Warnings()
    if n_no_prior:
        reference_warns.add(
            "envelope_cold_start",
            "src/service",
            f"{n_no_prior} site(s) had no prior-day load to warm the transformer thermal "
            "state, so their envelope starts from ambient",
            sites=int(n_no_prior),
        )
    reference = pd.concat(reference_frames, ignore_index=True)
    reference_rate = (clipped_num / clipped_den) if clipped_den else 0.0

    assert_frame_equal(grouped, reference)
    assert grouped_rate == reference_rate
    assert grouped_warns.as_list() == reference_warns.as_list()
