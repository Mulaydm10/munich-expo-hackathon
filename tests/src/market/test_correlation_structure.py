"""correlation_structure(): pairwise residual correlation vs great-circle distance."""

import numpy as np
import pandas as pd
import pytest

from src.market import api


def _fixture(n_rows: int = 300, seed: int = 1):
    rng = np.random.default_rng(seed)
    t = pd.date_range("2026-03-01T00:00:00Z", periods=n_rows, freq="15min")
    common = np.sin(np.linspace(0, 20 * np.pi, n_rows)) * 50 + 200

    load_a = common + rng.normal(0, 5, n_rows)
    load_b = common + rng.normal(0, 5, n_rows)              # near-identical to A -> high corr
    load_c = 0.1 * common + rng.normal(0, 40, n_rows) + 200  # mostly independent -> low corr

    load = pd.concat([
        pd.DataFrame({"t": t, "site_id": "A", "load_kw": load_a}),
        pd.DataFrame({"t": t, "site_id": "B", "load_kw": load_b}),
        pd.DataFrame({"t": t, "site_id": "C", "load_kw": load_c}),
    ], ignore_index=True)

    sites = pd.DataFrame({
        "site_id": ["A", "B", "C"],
        "lat": [48.10, 48.12, 52.52],   # A-B ~2km apart, C ~500km from both
        "lon": [11.60, 11.63, 13.40],
    })
    return load, sites


def test_correlation_decreases_with_distance_and_decay_length_is_positive():
    load, sites = _fixture()
    out = api.correlation_structure(load, sites)

    ab = out[(out.site_a == "A") & (out.site_b == "B")]["correlation"].iloc[0]
    ac = out[(out.site_a == "A") & (out.site_b == "C")]["correlation"].iloc[0]
    bc = out[(out.site_a == "B") & (out.site_b == "C")]["correlation"].iloc[0]

    assert ab > ac and ab > bc, "the nearby pair (A,B) must be more correlated than the far pairs"
    dist_ab = out[(out.site_a == "A") & (out.site_b == "B")]["distance_km"].iloc[0]
    dist_ac = out[(out.site_a == "A") & (out.site_b == "C")]["distance_km"].iloc[0]
    assert dist_ab < 10 < dist_ac

    decay = out.attrs["decay_length_km"]
    assert decay == decay  # not NaN
    assert decay > 0


def test_needs_at_least_two_sites():
    load, sites = _fixture()
    with pytest.raises(api.MarketError):
        api.correlation_structure(load[load.site_id == "A"], sites)


def test_high_load_regime_columns_present_and_finite_or_nan_gracefully():
    load, sites = _fixture()
    out = api.correlation_structure(load, sites)
    assert "correlation_high_load" in out.columns
    assert "regime_correlation_increase" in out.columns
    # must not raise even though we did not engineer a real holiday/cold-snap regime here
