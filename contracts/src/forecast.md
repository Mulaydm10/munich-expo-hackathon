# src/forecast — contract

Predicts site load as a **distribution**, never a point. Everything the project earns depends on the
lower tail being honest, so this lane's quality metric is calibration, not error.

Read `contracts/CONVENTIONS.md` first.

## Public API — `src/forecast/api.py`

```python
QUANTILES: tuple[float, ...] = (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)

def make_features(load: pd.DataFrame, weather: pd.DataFrame, prices: pd.DataFrame,
                  *, horizon_h: int = 36) -> pd.DataFrame
    """t, site_id, <feature columns>. Calendar (hour, weekday, holiday by state), lags at
    24h/48h/168h, rolling means, temperature and its cold-threshold interaction, site profile.
    Must be computable at bid time: no feature may use data from after `t - horizon_h`."""

def fit(features: pd.DataFrame, target: pd.DataFrame, *, quantiles=QUANTILES,
        model: Literal["gbr", "linear"] = "gbr", seed: int = 0) -> QuantileModel
def QuantileModel.predict(features: pd.DataFrame) -> pd.DataFrame
    """t, site_id, q05 … q95. Monotone across quantiles by construction (sorted post-hoc if the
    fitted heads cross) and non-negative."""
def QuantileModel.save(path: Path) -> None ;  load_model(path: Path) -> QuantileModel

def backtest(features, target, *, folds: int, quantiles=QUANTILES) -> pd.DataFrame
    """Rolling-origin, never random split. One row per (fold, quantile, site set)."""

def pinball_loss(y_true, q_pred, tau) -> float
def coverage(y_true, q_pred, tau) -> float          # empirical P(y <= q_tau); |coverage - tau| is the headline
def reliability_curve(y_true, preds) -> pd.DataFrame  # nominal vs empirical, the plot src/ui shows
def sharpness(preds) -> float                        # mean q95-q05 width; report next to coverage, never alone
```

## The bar this lane is judged against
1. **Calibration first.** `|coverage(0.05) - 0.05| <= 0.02` on held-out data, per profile. A model
   with lower pinball loss but 0.05-coverage of 0.12 is worse for us, not better — it silently
   overstates what can be sold.
2. **Beats a real baseline.** Seasonal naive (same weekday, same 15-min slot, last week) and a
   climatological quantile baseline are both implemented here and reported in every backtest table.
   A model that does not beat both is not shipped.
3. **Sharpness reported alongside.** A model that predicts `[0, ∞)` is perfectly calibrated and
   worthless; both numbers appear together or neither does.

## Guarantees other lanes depend on
- `predict` output is monotone in `tau`, non-negative, and covers every requested `(t, site_id)` —
  no silent row dropping.
- `q05` is a *lower* bound the market lane may sell against; if the model cannot produce one for a
  site (too little history), the row is emitted with `q05 = 0`, not omitted.
- Fitting is deterministic given `seed`.

## Explicitly not this lane's job
Deciding how much to bid (`src/market` — it consumes these quantiles), physical limits (`src/grid`).
