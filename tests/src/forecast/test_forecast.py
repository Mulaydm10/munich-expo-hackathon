"""tests for src.forecast.

Fixtures are generated in-process from a fixed seed (no checked-in data
files) per the issue's notes — cheap, deterministic, and small enough that
the whole suite stays in the seconds range.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.forecast import api


# --------------------------------------------------------------------------
# synthetic fixture generator
# --------------------------------------------------------------------------


def _make_synthetic(n_days: int, sites: tuple[str, ...], *, seed: int, start: str = "2026-01-01"):
    """Two-site synthetic load/weather/price series with a real, learnable signal.

    Load = daily + weekly seasonality + a cold-weather effect + noise. Weather
    is a slow seasonal trend plus AR(1) noise that is deliberately NOT
    weekly-periodic, so a given week's cold snap does not repeat exactly one
    week later — this is what makes the seasonal-naive ("same slot, last
    week") baseline beatable: it has no way to see temperature at all, while
    `make_features`'s `cold_dev_c` feature does.
    """
    rng = np.random.default_rng(seed)
    t = pd.date_range(start, periods=n_days * 96, freq="15min", tz="UTC")
    n = len(t)
    hour = t.hour + t.minute / 60.0
    daily = 10 * np.sin((hour - 6) / 24 * 2 * np.pi)
    day_idx = np.arange(n) / 96.0

    temp = 8 + 6 * np.sin(day_idx / 30 * 2 * np.pi)
    ar_noise = np.zeros(n)
    for i in range(1, n):
        ar_noise[i] = 0.985 * ar_noise[i - 1] + rng.normal(0, 0.6)
    temp = temp + ar_noise
    weather = pd.DataFrame({"t": t, "temp_c": temp})
    prices = pd.DataFrame({"t": t, "price_eur_mwh": 60 + rng.normal(0, 5, n)})

    rows = []
    for i, site in enumerate(sites):
        base = 50.0 + 30.0 * i
        weekly = 4 * np.sin(t.dayofweek.to_numpy() / 7 * 2 * np.pi)
        cold_dev = np.clip(api.COLD_THRESHOLD_C - temp, 0, None)
        cold_effect = 1.2 * cold_dev
        noise = rng.normal(0, 2, n)
        load = base + daily + weekly + cold_effect + noise
        rows.append(pd.DataFrame({"t": t, "site_id": site, "load_kw": load}))
    load = pd.concat(rows, ignore_index=True)
    return load, weather, prices


@pytest.fixture(scope="module")
def trained():
    """56 days x 2 sites, fit on the first 42 days, predict on the held-out last 14."""
    load, weather, prices = _make_synthetic(56, ("s1", "s2"), seed=0)
    target = load[["t", "site_id", "load_kw"]]
    feats = api.make_features(load, weather, prices, horizon_h=36)

    cutoff_t = load["t"].min() + pd.Timedelta(42, unit="D")
    train_mask = (feats["t"] < cutoff_t).to_numpy()
    test_mask = ~train_mask

    model = api.fit(feats.loc[train_mask], target, seed=0)
    test_feats = feats.loc[test_mask].reset_index(drop=True)
    preds = model.predict(test_feats)
    y_true = (
        target.merge(test_feats[["t", "site_id"]], on=["t", "site_id"], how="inner")["load_kw"]
        .to_numpy()
    )
    return {
        "load": load,
        "weather": weather,
        "prices": prices,
        "target": target,
        "feats": feats,
        "train_mask": train_mask,
        "model": model,
        "test_feats": test_feats,
        "preds": preds,
        "y_true": y_true,
    }


def _quantile_cols(preds: pd.DataFrame) -> list[str]:
    return [api._q_col(tau) for tau in api.QUANTILES]


# --------------------------------------------------------------------------
# leakage
# --------------------------------------------------------------------------


def test_features_do_not_leak_future_target_values():
    """Corrupting load/weather/prices strictly after t0 must not change any
    feature row whose target time is <= t0 — that is the entire leakage
    contract. We also check that the corruption *does* reach features far
    enough past t0 for rows we did NOT protect, so this test is not vacuous:
    if leakage were introduced, an unprotected row would go red.
    """
    horizon_h = 36
    load, weather, prices = _make_synthetic(12, ("s1", "s2"), seed=1)
    feats_before = api.make_features(load, weather, prices, horizon_h=horizon_h)

    t0 = load["t"].min() + pd.Timedelta(8, unit="D")

    load_c = load.copy()
    load_c.loc[load_c["t"] > t0, "load_kw"] = 1_000_000.0
    weather_c = weather.copy()
    weather_c.loc[weather_c["t"] > t0, "temp_c"] = -999.0
    prices_c = prices.copy()
    prices_c.loc[prices_c["t"] > t0, "price_eur_mwh"] = 999_999.0

    feats_after = api.make_features(load_c, weather_c, prices_c, horizon_h=horizon_h)

    before_sorted = feats_before.sort_values(["site_id", "t"]).reset_index(drop=True)
    after_sorted = feats_after.sort_values(["site_id", "t"]).reset_index(drop=True)

    protected = before_sorted["t"] <= t0
    pd.testing.assert_frame_equal(
        before_sorted.loc[protected].reset_index(drop=True),
        after_sorted.loc[protected].reset_index(drop=True),
    )

    # sanity: the corruption must actually be visible far enough past t0,
    # otherwise this test would pass even with a leaking implementation.
    far_enough = before_sorted["t"] > t0 + pd.Timedelta(horizon_h, unit="h")
    assert far_enough.any()
    changed = ~np.isclose(
        before_sorted.loc[far_enough, "lag_h0_kw"].to_numpy(),
        after_sorted.loc[far_enough, "lag_h0_kw"].to_numpy(),
        equal_nan=True,
    )
    assert changed.any(), "corrupting the future did not change any later feature row — fixture is broken"


# --------------------------------------------------------------------------
# monotone, non-negative quantiles
# --------------------------------------------------------------------------


def test_predict_is_monotone_and_nonnegative(trained):
    preds = trained["preds"]
    cols = _quantile_cols(preds)
    vals = preds[cols].to_numpy()
    assert (np.diff(vals, axis=1) >= 0).all(), "quantiles are not monotone non-decreasing"
    assert (vals >= 0).all(), "quantiles must be non-negative"


def test_raw_quantile_heads_actually_cross_and_get_sorted(trained):
    """The independently-fit quantile heads DO cross on this fixture (proving
    the non-crossing enforcement is exercised, not a no-op), and the binding
    rate stays in a plausible range rather than 0% (enforcement never
    triggers — suspicious) or 100% (heads are essentially random).
    """
    rate = trained["model"].last_predict_crossing_rate
    assert rate is not None
    assert 0.0 < rate < 1.0, f"crossing rate {rate!r} outside the sane range — investigate the fixture/model"


# --------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------


def test_fit_predict_is_deterministic_given_seed(trained, tmp_path):
    feats = trained["feats"]
    target = trained["target"]
    train_mask = trained["train_mask"]
    test_feats = trained["test_feats"]

    model_a = api.fit(feats.loc[train_mask], target, seed=7)
    model_b = api.fit(feats.loc[train_mask], target, seed=7)
    preds_a = model_a.predict(test_feats)
    preds_b = model_b.predict(test_feats)
    pd.testing.assert_frame_equal(preds_a, preds_b)

    path = tmp_path / "model.pkl"
    model_a.save(path)
    loaded = api.load_model(path)
    preds_loaded = loaded.predict(test_feats)
    pd.testing.assert_frame_equal(preds_a, preds_loaded)


# --------------------------------------------------------------------------
# beats a real baseline
# --------------------------------------------------------------------------


def test_model_beats_seasonal_naive_on_mean_pinball_loss(trained):
    # seasonal-naive is trained on the same rows the model was trained on.
    feats = trained["feats"]
    train_mask = trained["train_mask"]
    train_times = feats.loc[train_mask, "t"]
    train_target = trained["target"].loc[trained["target"]["t"].isin(train_times)]

    naive_point = api._seasonal_naive_predict(
        train_target[["t", "site_id", "load_kw"]], trained["test_feats"][["t", "site_id"]]
    )
    naive_valid = ~np.isnan(naive_point)
    assert naive_valid.any()

    y_true = trained["y_true"]
    preds = trained["preds"]

    model_losses = [
        api.pinball_loss(y_true, preds[api._q_col(tau)].to_numpy(), tau) for tau in api.QUANTILES
    ]
    naive_losses = [
        api.pinball_loss(y_true[naive_valid], naive_point[naive_valid], tau) for tau in api.QUANTILES
    ]

    mean_model = float(np.mean(model_losses))
    mean_naive = float(np.mean(naive_losses))
    assert mean_model < mean_naive, f"model ({mean_model}) did not beat seasonal-naive ({mean_naive})"


# --------------------------------------------------------------------------
# unseen sites / insufficient history never produce NaN or dropped rows
# --------------------------------------------------------------------------


def test_predict_on_unseen_site_works_without_nan(trained):
    model = trained["model"]
    unseen = trained["test_feats"][trained["test_feats"]["site_id"] == "s1"].copy()
    unseen["site_id"] = "brand-new-site"

    preds = model.predict(unseen)
    cols = _quantile_cols(preds)
    assert len(preds) == len(unseen), "unseen-site rows must not be dropped"
    assert not preds[cols].isna().any().any(), "unseen-site rows must never be NaN"
    assert (preds["q05"] == 0).all(), "contract: q05 is 0, not omitted, when the model can't speak to a site"
    vals = preds[cols].to_numpy()
    assert (np.diff(vals, axis=1) >= 0).all()


def test_predict_on_row_with_insufficient_history_works_without_nan(trained):
    """A known site's very first rows lack 168h of lookback (NaN lag_168h_kw
    etc.) — this must hit the same never-NaN, never-omitted fallback as an
    unseen site, exercised via a genuinely different code path (incomplete
    features on a *known* site, not an unknown site_id).
    """
    model = trained["model"]
    feats = trained["feats"]
    early_rows = feats[(feats["site_id"] == "s1") & feats["lag_168h_kw"].isna()]
    assert len(early_rows) > 0, "fixture must contain early, history-starved rows for this test to mean anything"

    preds = model.predict(early_rows)
    cols = _quantile_cols(preds)
    assert len(preds) == len(early_rows)
    assert not preds[cols].isna().any().any()
    assert (preds["q05"] == 0).all()


# --------------------------------------------------------------------------
# metrics — small, hand-checkable examples
# --------------------------------------------------------------------------


def test_pinball_loss_matches_hand_computation():
    y_true = np.array([10.0, 10.0])
    q_pred = np.array([8.0, 12.0])
    # tau=0.5: 0.5*(10-8) + 0.5*(12-10) averaged -> (1.0 + 1.0) / 2 = 1.0
    assert api.pinball_loss(y_true, q_pred, 0.5) == pytest.approx(1.0)
    # tau=0.9 penalises under-prediction (y>q) more: 0.9*2=1.8, over-prediction: 0.1*2=0.2 -> mean 1.0
    assert api.pinball_loss(y_true, q_pred, 0.9) == pytest.approx(1.0)


def test_coverage_is_empirical_p_y_leq_q():
    y_true = np.array([1.0, 2.0, 3.0, 4.0])
    q_pred = np.array([2.0, 2.0, 2.0, 2.0])
    assert api.coverage(y_true, q_pred, 0.5) == pytest.approx(0.5)


def test_sharpness_is_mean_width_of_widest_interval():
    preds = pd.DataFrame({"q05": [1.0, 2.0], "q50": [2.0, 3.0], "q95": [4.0, 6.0]})
    assert api.sharpness(preds) == pytest.approx(((4 - 1) + (6 - 2)) / 2)


def test_reliability_curve_shape(trained):
    curve = api.reliability_curve(trained["y_true"], trained["preds"])
    assert list(curve.columns) == ["tau_nominal", "coverage_empirical"]
    assert len(curve) == len(api.QUANTILES)
    assert (curve["coverage_empirical"] >= 0).all() and (curve["coverage_empirical"] <= 1).all()


# --------------------------------------------------------------------------
# backtest — smoke only; calibration quality is out of scope for this issue
# --------------------------------------------------------------------------


def test_backtest_smoke():
    # needs enough days that fold 0's training chunk alone contains rows with
    # a full 168h lookback available (horizon_h + 168h ~= 8.5 days), otherwise
    # every training row is dropped for NaN lags and fit() has nothing to fit.
    load, weather, prices = _make_synthetic(40, ("s1",), seed=2)
    target = load[["t", "site_id", "load_kw"]]
    feats = api.make_features(load, weather, prices, horizon_h=36)

    result = api.backtest(feats, target, folds=2)
    assert set(result["fold"].unique()) == {0, 1}
    assert set(result["tau"].unique()) == set(api.QUANTILES)
    assert (result["site_set"] == "all").all()
    assert len(result) == 2 * len(api.QUANTILES)
    for col in ("pinball_model", "pinball_seasonal_naive", "pinball_climatological"):
        assert result[col].notna().all()


def test_unsorted_quantiles_are_not_mislabelled():
    """Found in review of PR #22.

    predict() sorts each row of raw predictions ascending to stop the
    independently-fitted heads crossing, but the column *names* came from the
    `quantiles` tuple as the caller passed it. An unsorted argument therefore
    labelled the smallest value with the highest quantile -- silently, with
    entirely plausible numbers. Measured before the fix:

        in=(0.1, 0.5, 0.9)  ->  q10=49.30  q50=53.08  q90=57.74
        in=(0.9, 0.1, 0.5)  ->  q90=49.30  q10=53.08  q50=57.74

    src/market sizes firm capacity off "the lower quantile" (#14), so this
    fails in the direction of promising flexibility that is not there.
    """
    load, weather, prices = _make_synthetic(40, ("s1",), seed=0)
    feats = api.make_features(load, weather, prices, horizon_h=1)

    ascending = api.fit(feats, load, quantiles=(0.1, 0.5, 0.9), seed=0)
    shuffled = api.fit(feats, load, quantiles=(0.9, 0.1, 0.5), seed=0)

    assert shuffled.quantiles == (0.1, 0.5, 0.9), "fit must store quantiles sorted"

    tail = feats.tail(60)
    a, b = ascending.predict(tail), shuffled.predict(tail)
    cols = [c for c in a.columns if c.startswith("q") and c[1:].isdigit()]
    assert cols == [c for c in b.columns if c.startswith("q") and c[1:].isdigit()]
    assert len(cols) == 3
    for c in cols:
        assert np.allclose(a[c].to_numpy(), b[c].to_numpy()), (
            f"{c} differs by input order -- labels follow argument order, not value order"
        )

    # Guard the guard: if the columns were all equal this test would pass
    # for the wrong reason, so require the quantiles to actually separate.
    assert a[cols[0]].mean() < a[cols[-1]].mean()
