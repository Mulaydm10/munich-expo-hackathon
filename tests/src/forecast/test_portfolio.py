from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.forecast import api


def _load() -> pd.DataFrame:
    t = pd.to_datetime(
        [
            "2026-01-01 00:00",
            "2026-01-01 00:15",
            "2026-01-02 00:00",
            "2026-01-02 00:15",
        ],
        utc=True,
    )
    return pd.DataFrame(
        {
            "t": list(t) * 2,
            "site_id": ["a"] * 4 + ["b"] * 4,
            "load_kw": [6.0, 0.0, 6.0, 0.0, 4.0, 0.0, 4.0, 0.0],
        }
    )


def test_site_shares_sum_to_one_for_every_slot():
    shares = api.site_shares(_load())
    sums = shares.groupby("slot_15min")["share"].sum()
    assert len(shares) == 2 * 96
    assert np.allclose(sums.to_numpy(), 1.0, atol=1e-9)


def test_site_shares_zero_slot_falls_back_to_overall_share():
    shares = api.site_shares(_load())
    zero_slot = shares[shares["slot_15min"] == 1].set_index("site_id")["share"]
    assert zero_slot["a"] == pytest.approx(0.6)
    assert zero_slot["b"] == pytest.approx(0.4)


def test_split_portfolio_sums_back_and_preserves_quantile_invariants():
    times = pd.date_range("2026-01-03", periods=2, freq="15min", tz="UTC")
    preds = pd.DataFrame(
        {
            "t": times,
            "site_id": api.PORTFOLIO_ID,
            "q05": [0.0, 1.0],
            "q50": [10.0, 20.0],
            "q95": [30.0, 40.0],
        }
    )
    shares = api.site_shares(_load())
    split = api.split_portfolio(preds, shares)
    for t in times:
        rows = split[split["t"] == t]
        for col in ["q05", "q50", "q95"]:
            assert rows[col].sum() == pytest.approx(preds.loc[preds["t"] == t, col].iloc[0])
    vals = split[["q05", "q50", "q95"]].to_numpy()
    assert (vals >= 0).all()
    assert (np.diff(vals, axis=1) >= 0).all()


def test_split_portfolio_rejects_non_portfolio_predictions():
    preds = pd.DataFrame(
        {
            "t": pd.date_range("2026-01-03", periods=1, freq="15min", tz="UTC"),
            "site_id": "a",
            "q05": 1.0,
        }
    )
    with pytest.raises(ValueError, match="only"):
        api.split_portfolio(preds, api.site_shares(_load()))


def test_seasonal_naive_looks_back_and_returns_nan_when_missing():
    history = pd.DataFrame(
        {
            "t": pd.to_datetime(["2026-01-01 00:15", "2026-01-08 00:15"], utc=True),
            "load_kw": [12.0, 8.0],
        }
    )
    times = pd.to_datetime(["2026-01-08 00:30", "2026-01-08 00:15"], utc=True)
    assert np.isnan(api.seasonal_naive(history, times)[0])
    assert api.seasonal_naive(history, times)[1] == pytest.approx(12.0)


def test_point_metrics_drop_nan_pairs_and_compute_wape():
    metrics = api.point_metrics([1.0, 3.0, np.nan, 4.0], [2.0, 1.0, 7.0, np.nan])
    assert metrics == {"mae_kw": 1.5, "wape": 0.75, "n": 2}
