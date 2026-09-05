"""src.forecast — public API.

Quantile load forecasting with calibration as the headline metric.

This module is the lane's ONLY cross-lane surface: other lanes import
`src.forecast.api` and nothing else. See `contracts/src/forecast.md` for the
interface this lane owes the rest of the project, and
`contracts/CONVENTIONS.md` for units, time handling and the data layout.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import QuantileRegressor

LANE = "src/forecast"

QUANTILES: tuple[float, ...] = (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)

_STEP = pd.Timedelta(15, unit="min")
_LAG_HOURS = (24, 48, 168)

# GUESS: heating-degree threshold below which load is assumed to pick up due to
# electric/space heating. No source for this figure is given anywhere in the
# contracts or the issue; 15 degC is a common rule-of-thumb "heating threshold"
# for German buildings but it has not been fit or validated against real data.
COLD_THRESHOLD_C = 15.0

# GUESS: the contract asks for "holiday by state" but neither the contract nor
# `load`/`weather`/`prices` define a site -> state mapping, and src/data (which
# might own that mapping) is unmerged and off-limits to import from this lane.
# We default every site to Bavaria ("BY"), matching the Munich hackathon
# setting, unless the caller's `load` frame carries an explicit "state" column.
_DEFAULT_STATE = "BY"


# --------------------------------------------------------------------------
# small private helpers (not part of the cross-lane surface)
# --------------------------------------------------------------------------


def _require_columns(df: pd.DataFrame, cols: list[str], name: str) -> None:
    """Assert `df` has every column in `cols`.

    `contracts/CONVENTIONS.md` points at `require_columns` in `src.data.api`,
    but `src/data` is an unmerged lane this lane must not import from (see
    `contracts/CONVENTIONS.md`'s one-public-module rule and this issue's
    cross-lane note). This is a local equivalent scoped to `src/forecast`.
    """
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{name}: missing required column(s) {missing}; has {list(df.columns)}")


def _easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday via the anonymous Gauss/Meeus algorithm (stdlib only)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


_HOLIDAY_CACHE: dict[tuple[int, str], set] = {}


def _german_holidays(year: int, state: str = _DEFAULT_STATE) -> set:
    key = (year, state)
    cached = _HOLIDAY_CACHE.get(key)
    if cached is not None:
        return cached
    easter = _easter_sunday(year)
    holidays = {
        date(year, 1, 1),
        date(year, 5, 1),
        date(year, 10, 3),
        date(year, 12, 25),
        date(year, 12, 26),
        easter - timedelta(days=2),
        easter + timedelta(days=1),
        easter + timedelta(days=39),
        easter + timedelta(days=50),
    }
    if state == "BY":
        holidays |= {
            date(year, 1, 6),
            easter + timedelta(days=60),
            date(year, 8, 15),
            date(year, 11, 1),
        }
    _HOLIDAY_CACHE[key] = holidays
    return holidays


def _is_holiday(d: date, state: str = _DEFAULT_STATE) -> bool:
    return d in _german_holidays(d.year, state)


def _q_col(tau: float) -> str:
    return f"q{round(tau * 100):02d}"


def _lookup(series: pd.Series, at: pd.DatetimeIndex | pd.Series) -> np.ndarray:
    """Sample a (regularly-spaced, sorted) time-indexed series at arbitrary timestamps.

    Timestamps with no exact match in `series.index` yield NaN, they are never
    invented (no ffill/bfill) — see CONVENTIONS' "no invented statistics" rule.
    """
    return series.reindex(pd.DatetimeIndex(at)).to_numpy()


# --------------------------------------------------------------------------
# features
# --------------------------------------------------------------------------


def make_features(
    load: pd.DataFrame,
    weather: pd.DataFrame,
    prices: pd.DataFrame,
    *,
    horizon_h: int = 36,
) -> pd.DataFrame:
    """Build a leakage-free feature frame, one row per (t, site_id) present in `load`.

    `t` is the *target* timestamp being forecast. Every feature value for a row
    is computed strictly from inputs timestamped `<= t - horizon_h` (the bid
    time) — the earliest a real bid must be placed. Calendar features (hour,
    weekday, holiday) are a function of `t` itself, not of any measured input,
    so they carry no leakage risk regardless of horizon.

    `load`: `t, site_id, load_kw`. `weather`: `t, temp_c` (site-agnostic — one
    weather series for the whole area). `prices`: `t, price_eur_mwh`
    (site-agnostic day-ahead price). Optionally `load` may carry a `state`
    column (one value per site) used for the holiday calendar; if absent every
    site defaults to Bavaria (see `_DEFAULT_STATE`).

    Columns returned: `t, site_id, hour, weekday, slot_15min, is_holiday,
    lag_h0_kw, lag_24h_kw, lag_48h_kw, lag_168h_kw, roll_mean_24h_kw,
    roll_mean_168h_kw, site_profile_mean_kw, temp_c_lag, cold_dev_c,
    price_eur_mwh_lag`.
    """
    _require_columns(load, ["t", "site_id", "load_kw"], "load")
    _require_columns(weather, ["t", "temp_c"], "weather")
    _require_columns(prices, ["t", "price_eur_mwh"], "prices")

    horizon = pd.Timedelta(horizon_h, unit="h")
    has_state = "state" in load.columns

    weather_series = weather.sort_values("t").set_index("t")["temp_c"]
    weather_series = weather_series[~weather_series.index.duplicated(keep="last")]
    price_series = prices.sort_values("t").set_index("t")["price_eur_mwh"]
    price_series = price_series[~price_series.index.duplicated(keep="last")]

    per_site_frames: list[pd.DataFrame] = []
    for site_id, g in load.sort_values("t").groupby("site_id", sort=True):
        g = g.drop_duplicates(subset="t", keep="last").set_index("t").sort_index()
        idx = g.index
        cutoff = idx - horizon
        load_series = g["load_kw"]

        roll_24h = load_series.rolling(pd.Timedelta(24, unit="h"), min_periods=1).mean()
        roll_168h = load_series.rolling(pd.Timedelta(168, unit="h"), min_periods=1).mean()
        expanding_mean = load_series.expanding(min_periods=1).mean()

        feat = pd.DataFrame({"t": idx, "site_id": site_id})
        feat["lag_h0_kw"] = _lookup(load_series, cutoff)
        for h in _LAG_HOURS:
            feat[f"lag_{h}h_kw"] = _lookup(load_series, cutoff - pd.Timedelta(h, unit="h"))
        feat["roll_mean_24h_kw"] = _lookup(roll_24h, cutoff)
        feat["roll_mean_168h_kw"] = _lookup(roll_168h, cutoff)
        feat["site_profile_mean_kw"] = _lookup(expanding_mean, cutoff)
        if has_state:
            feat["state"] = g["state"].to_numpy()
        per_site_frames.append(feat)

    feat_all = pd.concat(per_site_frames, ignore_index=True)

    cutoff_all = feat_all["t"] - horizon
    feat_all["temp_c_lag"] = _lookup(weather_series, cutoff_all)
    feat_all["cold_dev_c"] = (COLD_THRESHOLD_C - feat_all["temp_c_lag"]).clip(lower=0)
    feat_all["price_eur_mwh_lag"] = _lookup(price_series, cutoff_all)

    t = feat_all["t"]
    feat_all["hour"] = t.dt.hour
    feat_all["weekday"] = t.dt.weekday
    feat_all["slot_15min"] = t.dt.hour * 4 + t.dt.minute // 15
    if has_state:
        feat_all["is_holiday"] = [
            int(_is_holiday(ts.date(), st)) for ts, st in zip(t, feat_all["state"])
        ]
        feat_all = feat_all.drop(columns=["state"])
    else:
        feat_all["is_holiday"] = [int(_is_holiday(ts.date())) for ts in t]

    ordered_cols = [
        "t",
        "site_id",
        "hour",
        "weekday",
        "slot_15min",
        "is_holiday",
        "lag_h0_kw",
        "lag_24h_kw",
        "lag_48h_kw",
        "lag_168h_kw",
        "roll_mean_24h_kw",
        "roll_mean_168h_kw",
        "site_profile_mean_kw",
        "temp_c_lag",
        "cold_dev_c",
        "price_eur_mwh_lag",
    ]
    feat_all = feat_all.sort_values(["site_id", "t"]).reset_index(drop=True)
    return feat_all[ordered_cols]


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------

# GUESS: untuned defaults for the gradient-boosting quantile heads. Small on
# purpose (this is a hackathon-scale fixture, not a production hyperparameter
# search) and deliberately shallow/short — this also happens to be the regime
# where independently-fit quantile heads are most likely to cross, which is
# exactly what the non-crossing enforcement below needs to be exercised against.
_GBR_DEFAULTS = {"n_estimators": 40, "max_depth": 3, "learning_rate": 0.1}


@dataclass
class QuantileModel:
    """A fitted set of independent per-quantile regressors plus a fallback.

    `predict` sorts each row's raw per-quantile outputs ascending (the
    "post-hoc sort" the contract explicitly allows) and floors at zero, so the
    class guarantees monotone, non-negative output regardless of whether the
    underlying heads crossed. `last_predict_crossing_rate` and
    `last_predict_fallback_rate` are set on every `predict()` call so that how
    often those two coercions actually bind is an observable, not a secret —
    see the lane's tests for the numbers on the fixture used here.
    """

    quantiles: tuple[float, ...]
    model_type: str
    seed: int
    feature_cols: list[str]
    estimators: dict[float, object]
    known_sites: frozenset
    fallback_quantiles: dict[float, float]
    last_predict_crossing_rate: float | None = field(default=None, compare=False)
    last_predict_fallback_rate: float | None = field(default=None, compare=False)

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        """t, site_id, q05 … q95. Monotone, non-negative, one row per input row.

        A row whose feature vector is incomplete (e.g. too little history) or
        whose site_id was never seen during `fit` is never dropped and never
        NaN: it gets the pooled, fit-time unconditional quantiles as a
        fallback, with the lowest requested quantile forced to 0 (the
        contract's "q05 = 0, not omitted" guarantee for sites the model can't
        speak to).
        """
        _require_columns(features, ["t", "site_id", *self.feature_cols], "features")
        n = len(features)
        q_names = [_q_col(tau) for tau in self.quantiles]
        out = pd.DataFrame(
            {"t": features["t"].to_numpy(), "site_id": features["site_id"].to_numpy()}
        )
        if n == 0:
            for name in q_names:
                out[name] = pd.Series(dtype=float)
            self.last_predict_crossing_rate = 0.0
            self.last_predict_fallback_rate = 0.0
            return out

        x_full = features[self.feature_cols].to_numpy(dtype=float)
        complete_mask = ~np.isnan(x_full).any(axis=1)
        known_mask = features["site_id"].isin(self.known_sites).to_numpy()
        usable_mask = complete_mask & known_mask

        raw = np.full((n, len(self.quantiles)), np.nan, dtype=float)
        if usable_mask.any():
            x_usable = x_full[usable_mask]
            for j, tau in enumerate(self.quantiles):
                raw[usable_mask, j] = self.estimators[tau].predict(x_usable)

        fallback_row = np.array([self.fallback_quantiles[tau] for tau in self.quantiles])
        unusable_mask = ~usable_mask
        if unusable_mask.any():
            raw[unusable_mask] = fallback_row

        raw = np.clip(raw, 0.0, None)

        sorted_vals = np.sort(raw, axis=1)
        crossing = np.any(raw != sorted_vals, axis=1)
        self.last_predict_crossing_rate = float(crossing.mean())
        self.last_predict_fallback_rate = float(unusable_mask.mean())

        if unusable_mask.any():
            # lowest column post-sort is the row's minimum: forcing it to 0
            # cannot break the ascending order we just established.
            sorted_vals[unusable_mask, 0] = 0.0

        for j, name in enumerate(q_names):
            out[name] = sorted_vals[:, j]
        return out

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            pickle.dump(self, fh)


def load_model(path: Path) -> QuantileModel:
    with open(Path(path), "rb") as fh:
        obj = pickle.load(fh)
    if not isinstance(obj, QuantileModel):
        raise TypeError(f"load_model: {path} does not contain a QuantileModel")
    return obj


def fit(
    features: pd.DataFrame,
    target: pd.DataFrame,
    *,
    quantiles: tuple[float, ...] = QUANTILES,
    model: Literal["gbr", "linear"] = "gbr",
    seed: int = 0,
) -> QuantileModel:
    """Fit one regressor per quantile, independently (they may cross — `predict` fixes that).

    `target`: `t, site_id, load_kw`, the realised values for the same rows
    `features` was built for. Rows with any NaN feature or target (too little
    history to build a lag/rolling feature) are dropped from training — this
    is `fit`, not `predict`, so the "never drop a row" guarantee does not
    apply here.
    """
    _require_columns(features, ["t", "site_id"], "features")
    _require_columns(target, ["t", "site_id", "load_kw"], "target")

    feature_cols = [c for c in features.columns if c not in ("t", "site_id")]
    merged = features.merge(target[["t", "site_id", "load_kw"]], on=["t", "site_id"], how="inner")
    train = merged.dropna(subset=[*feature_cols, "load_kw"])
    if train.empty:
        raise ValueError("fit: no complete (feature, target) rows to train on")

    x = train[feature_cols].to_numpy(dtype=float)
    y = train["load_kw"].to_numpy(dtype=float)

    estimators: dict[float, object] = {}
    for tau in quantiles:
        if model == "gbr":
            estimator = GradientBoostingRegressor(
                loss="quantile", alpha=tau, random_state=seed, **_GBR_DEFAULTS
            )
        elif model == "linear":
            estimator = QuantileRegressor(quantile=tau, alpha=0.0, solver="highs")
        else:
            raise ValueError(f"fit: unknown model {model!r}, expected 'gbr' or 'linear'")
        estimator.fit(x, y)
        estimators[tau] = estimator

    fallback_quantiles = {tau: float(np.quantile(y, tau)) for tau in quantiles}
    return QuantileModel(
        quantiles=tuple(quantiles),
        model_type=model,
        seed=seed,
        feature_cols=feature_cols,
        estimators=estimators,
        known_sites=frozenset(train["site_id"].unique()),
        fallback_quantiles=fallback_quantiles,
    )


# --------------------------------------------------------------------------
# baselines — reported alongside every backtest, never invented ex post
# --------------------------------------------------------------------------


def _seasonal_naive_predict(history: pd.DataFrame, query: pd.DataFrame) -> np.ndarray:
    """Point forecast: last week, same 15-min slot. `history`/`query`: t, site_id, load_kw."""
    lag = pd.Timedelta(7, unit="D")
    preds = np.full(len(query), np.nan)
    for site_id, q in query.groupby("site_id"):
        h = history.loc[history["site_id"] == site_id].set_index("t")["load_kw"].sort_index()
        h = h[~h.index.duplicated(keep="last")]
        vals = _lookup(h, q["t"] - lag)
        preds[query["site_id"].to_numpy() == site_id] = vals
    return preds


def _climatological_quantiles(history: pd.DataFrame, quantiles: tuple[float, ...]) -> dict[float, float]:
    """Unconditional (pooled, all-sites) empirical quantiles of historical load."""
    y = history["load_kw"].dropna().to_numpy(dtype=float)
    return {tau: float(np.quantile(y, tau)) for tau in quantiles}


def pinball_loss(y_true, q_pred, tau: float) -> float:
    """Mean pinball (quantile) loss of `q_pred` against `y_true` at quantile level `tau`."""
    y_true = np.asarray(y_true, dtype=float)
    q_pred = np.asarray(q_pred, dtype=float)
    diff = y_true - q_pred
    return float(np.mean(np.maximum(tau * diff, (tau - 1) * diff)))


def coverage(y_true, q_pred, tau: float) -> float:
    """Empirical P(y <= q_pred). `tau` is the nominal level being checked against."""
    del tau  # nominal level is carried by the caller/plot, not needed for the computation itself
    y_true = np.asarray(y_true, dtype=float)
    q_pred = np.asarray(q_pred, dtype=float)
    return float(np.mean(y_true <= q_pred))


def reliability_curve(y_true, preds: pd.DataFrame) -> pd.DataFrame:
    """Nominal vs empirical coverage, one row per quantile column in `preds`.

    `y_true` is array-like, aligned row-wise with `preds` (same row order).
    `preds` is a `t, site_id, q05 … q95`-shaped frame (as returned by
    `QuantileModel.predict`).
    """
    y_true = np.asarray(y_true, dtype=float)
    rows = []
    for tau in QUANTILES:
        col = _q_col(tau)
        if col not in preds.columns:
            continue
        rows.append({"tau_nominal": tau, "coverage_empirical": coverage(y_true, preds[col], tau)})
    return pd.DataFrame(rows, columns=["tau_nominal", "coverage_empirical"])


def sharpness(preds: pd.DataFrame) -> float:
    """Mean width of the widest requested interval present in `preds` (default q95 - q05)."""
    cols = [c for c in preds.columns if c.startswith("q") and c[1:].isdigit()]
    if not cols:
        raise ValueError("sharpness: preds has no qNN columns")
    lo_col = min(cols, key=lambda c: int(c[1:]))
    hi_col = max(cols, key=lambda c: int(c[1:]))
    return float((preds[hi_col] - preds[lo_col]).mean())


def backtest(
    features: pd.DataFrame,
    target: pd.DataFrame,
    *,
    folds: int,
    quantiles: tuple[float, ...] = QUANTILES,
) -> pd.DataFrame:
    """Rolling-origin backtest: fold `i` trains on every earlier fold, tests on fold `i`.

    Never a random split — folds are contiguous, time-ordered chunks of the
    unique `t` values in `target`, so a fold's test period is always strictly
    later than everything it was trained on. One row per (fold, quantile);
    `site_set` is a single group ("all") — this lane does not yet partition
    sites into profile buckets.
    """
    if folds < 1:
        raise ValueError("backtest: folds must be >= 1")
    times = np.sort(target["t"].unique())
    if len(times) < folds + 1:
        raise ValueError("backtest: not enough distinct timestamps for the requested fold count")
    chunks = np.array_split(times, folds + 1)

    rows = []
    train_times = chunks[0]
    for fold_idx in range(folds):
        test_times = chunks[fold_idx + 1]
        train_mask_t = target["t"].isin(train_times)
        test_mask_t = target["t"].isin(test_times)

        train_target = target.loc[train_mask_t]
        train_features = features.loc[features["t"].isin(train_times)]
        test_features = features.loc[test_mask_t]
        # keep row order aligned between test_features and its matching target
        test_target = test_features[["t", "site_id"]].merge(
            target, on=["t", "site_id"], how="left"
        )

        model = fit(train_features, train_target, quantiles=quantiles, seed=0)
        preds = model.predict(test_features)
        naive_point = _seasonal_naive_predict(
            train_target[["t", "site_id", "load_kw"]], test_features[["t", "site_id"]]
        )
        clima = _climatological_quantiles(train_target, quantiles)

        y_true = test_target["load_kw"].to_numpy(dtype=float)
        valid = ~np.isnan(y_true)
        # the seasonal-naive baseline has its own, separate NaN pattern (it
        # needs a full 7-day-back lookup that early test rows may not have);
        # each metric masks against its own NaNs so one baseline's missing
        # values can't silently poison another's (or the model's) mean loss.
        naive_valid = valid & ~np.isnan(naive_point)
        for tau in quantiles:
            col = _q_col(tau)
            model_pred = preds[col].to_numpy()
            model_valid = valid & ~np.isnan(model_pred)
            rows.append(
                {
                    "fold": fold_idx,
                    "tau": tau,
                    "site_set": "all",
                    "n_obs": int(valid.sum()),
                    "n_obs_seasonal_naive": int(naive_valid.sum()),
                    "pinball_model": pinball_loss(
                        y_true[model_valid], model_pred[model_valid], tau
                    ),
                    "pinball_seasonal_naive": pinball_loss(
                        y_true[naive_valid], naive_point[naive_valid], tau
                    ),
                    "pinball_climatological": pinball_loss(
                        y_true[valid], np.full(int(valid.sum()), clima[tau]), tau
                    ),
                }
            )
        train_times = np.concatenate([train_times, test_times])

    return pd.DataFrame(rows)
