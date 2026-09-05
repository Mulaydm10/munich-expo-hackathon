"""src.market — public API.

Firm capacity, portfolio pooling, bidding and settlement in euros and CO2.

This module is the lane's ONLY cross-lane surface: other lanes import
`src.market.api` and nothing else. See `contracts/src/market.md` for the
interface this lane owes the rest of the project, and
`contracts/CONVENTIONS.md` for units, time handling and the data layout.

`src.data.api` is not implemented yet on this branch (it is a bare stub), so
this lane does not import it: the boundary-assert helper and canonical-table
column names are reproduced locally instead of imported, to avoid coupling to
a sibling lane's unfinished internals. Likewise `src.forecast` and
`src.sched` are consumed only "by contract" (this lane builds its own
quantile-shaped fixtures in tests), never by import, per lane discipline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np
import pandas as pd
from scipy.stats import norm

LANE = "src/market"


# ---------------------------------------------------------------------------
# local helpers (private -- not part of the cross-lane surface)
# ---------------------------------------------------------------------------

def _require_columns(df: pd.DataFrame, cols: Sequence[str]) -> None:
    """Boundary assert: mirrors src.data.api.require_columns, reproduced
    locally because that sibling lane has no implementation yet on this
    branch (see module docstring)."""
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"missing required column(s) {missing} (have {list(df.columns)})")


def _quantile_col(tau: float) -> str:
    """Map a tau value to its forecast-contract column name by explicit
    value, never by position. QUANTILES = (0.05, 0.1, 0.25, 0.5, 0.75, 0.9,
    0.95) -> q05, q10, q25, q50, q75, q90, q95.

    This is the fix for the #22-shaped bug: a column must be identified by
    what it claims to be (its tau), never by where it sits in the frame.
    """
    pct = tau * 100
    rounded = round(pct)
    if abs(pct - rounded) > 1e-6:
        raise ValueError(f"tau={tau} does not map to a supported quantile column")
    return f"q{rounded:02d}"


def _interval_hours(t: pd.Series) -> pd.Series:
    """Duration, in hours, that each row's `t` (interval-start) covers.

    Derived from the actual gaps between consecutive timestamps -- never a
    hard-coded `/4` -- per CONVENTIONS.md. Because it operates on absolute
    UTC instants, a DST wall-clock day (23h/25h in Europe/Berlin) does not
    perturb this at all; it only ever sees uniform elapsed time.

    The last row (no following timestamp) is assigned the duration of the
    row before it; a single-row series falls back to the native 15-minute
    resolution (documented assumption, only relevant to single-row inputs).
    """
    t = pd.to_datetime(pd.Series(t).reset_index(drop=True))
    n = len(t)
    if n == 0:
        return pd.Series([], dtype="float64")
    if n == 1:
        return pd.Series([0.25])
    deltas = t.diff().shift(-1)
    deltas.iloc[-1] = deltas.iloc[-2]
    return (deltas.dt.total_seconds() / 3600.0).reset_index(drop=True)


# ---------------------------------------------------------------------------
# ASSUMPTIONS / PRODUCTS
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Assumption:
    value: float
    unit: str
    source: str
    note: str = ""

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("an Assumption must always carry a source; use source='ASSUMED' "
                              "if it is not sourced -- an empty source is not allowed")


@dataclass(frozen=True)
class Product:
    name: str
    block_length_min: float
    min_bid_kw: float
    granularity_kw: float
    notice_period_min: float
    penalty_multiplier: float  # applied to the block's own energy price, per kWh of shortfall
    source: str

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("a Product must always carry a source; use source='ASSUMED' if it "
                              "is not sourced")


ASSUMPTIONS: dict[str, Assumption] = {
    "peaker_plant_capacity_mw": Assumption(
        value=50.0,
        unit="MW",
        source="ASSUMED",
        note=(
            "Typical small OCGT peaking-plant unit size, used only for the 'peakers displaced' "
            "headline count in peakers_displaced(). GUESS: not looked up against a specific "
            "plant register or capacity list in this session -- flag it wherever it is shown."
        ),
    ),
    "pool_default_pairwise_correlation": Assumption(
        value=0.3,
        unit="dimensionless (Pearson r)",
        source="ASSUMED",
        note=(
            "Baseline site-to-site correlation of residual delivery used by pool() when the "
            "caller has not supplied a fitted correlation_structure() output. GUESS, deliberately "
            "in the 'meaningfully correlated but far from 1' range that field data would need a "
            "real fit to pin down; correlation_structure() exists precisely to replace it."
        ),
    ),
    "pool_relative_uncertainty": Assumption(
        value=0.3,
        unit="dimensionless (std / mean)",
        source="ASSUMED",
        note=(
            "Coefficient of variation used to back out a Normal(mean, std) marginal for each "
            "site from its single tau-quantile firm_kw anchor, so pool() has a spread to "
            "correlate sites over. GUESS: a real implementation would carry the full q05..q95 "
            "curve into pool() instead of reconstructing spread from one point."
        ),
    ),
    "high_load_regime_percentile": Assumption(
        value=0.9,
        unit="dimensionless (quantile of pooled load)",
        source="ASSUMED",
        note=(
            "Threshold used by correlation_structure() to flag a 'system under stress' regime "
            "(top decile of total pooled load) whose pairwise correlation is compared against "
            "the all-sample correlation -- a proxy for the holiday/cold-snap regimes the contract "
            "names, since no calendar or weather join is available to this lane."
        ),
    ),
}


PRODUCTS: dict[str, Product] = {
    "aFRR": Product(
        name="aFRR",
        block_length_min=240.0,
        min_bid_kw=1000.0,
        granularity_kw=1000.0,
        notice_period_min=5.0,
        penalty_multiplier=3.0,
        source=(
            "ASSUMED -- recollection of regelleistung.net's post-2020 harmonised tender rules "
            "(uniform 1 MW minimum bid, 4-hour product blocks, <=5 min full activation for "
            "aFRR); NOT re-verified against a live source in this session (attempted, both "
            "regelleistung.net and a secondary explainer returned non-200/changed layouts). The "
            "non-delivery penalty_multiplier=3.0x the prevailing energy price is invented "
            "outright, not looked up anywhere -- treat it as a placeholder."
        ),
    ),
    "mFRR": Product(
        name="mFRR",
        block_length_min=240.0,
        min_bid_kw=1000.0,
        granularity_kw=1000.0,
        notice_period_min=12.5,
        penalty_multiplier=2.5,
        source=(
            "ASSUMED -- same basis as aFRR above (uniform post-2020 1 MW / 4h blocks, <=12.5 "
            "min full activation for mFRR); NOT re-verified live this session. "
            "penalty_multiplier=2.5x is invented, not sourced."
        ),
    ),
}


class MarketError(ValueError):
    """Base class for this lane's input-validation failures."""


# ---------------------------------------------------------------------------
# firm capacity
# ---------------------------------------------------------------------------

def firm_capacity(quantiles: pd.DataFrame, *, tau: float = 0.05, floor_kw: float = 0.0) -> pd.DataFrame:
    """t, site_id, firm_kw -- reduction deliverable with probability >= 1 - tau.

    Uses the lower quantile (named/selected by tau, never by column
    position), never the median. `floor_kw` is the load the scheduler
    commits to hold, so it is subtracted before the promise is floored at
    zero (a site cannot be sold as negative capacity).
    """
    _require_columns(quantiles, ["t", "site_id"])
    lo_col = _quantile_col(tau)
    med_col = _quantile_col(0.5)
    if lo_col not in quantiles.columns:
        raise MarketError(
            f"quantiles frame has no column {lo_col!r} for tau={tau}; "
            f"available columns: {list(quantiles.columns)}"
        )

    lo = quantiles[lo_col]

    # NaN in a forecast quantile means "we do not know", not "no limit" --
    # made explicit rather than left to fall out of a bare comparison (a bare
    # `NaN > x` is False and would silently take the permissive branch, the
    # exact shape of the #21 bug). We treat an unknown quantile as zero
    # deliverable capacity: the safe direction for a promise.
    nan_mask = lo.isna()
    lo_filled = lo.fillna(0.0)

    if med_col in quantiles.columns:
        med = quantiles[med_col].fillna(0.0)
        bad = (lo_filled > med + 1e-9) & ~nan_mask
        frac_bad = float(bad.mean()) if len(bad) else 0.0
        if frac_bad > 0.5:
            raise MarketError(
                f"quantiles frame looks mislabelled: the tau={tau} column ({lo_col}) exceeds "
                f"the median column ({med_col}) on {frac_bad:.0%} of rows. A lower quantile must "
                "not be systematically above the median -- refusing to size firm capacity off "
                "it. This is exactly the #22-style bug: identical numbers, inverted labels."
            )

    raw = lo_filled - floor_kw
    firm_kw = raw.clip(lower=0.0)

    floor_bound = raw < 0.0
    negative_input = (lo_filled < 0.0) & ~nan_mask

    out = quantiles[["t", "site_id"]].copy()
    out["firm_kw"] = firm_kw.to_numpy()
    out.attrs["floor_clamp_rate"] = float(floor_bound.mean()) if len(floor_bound) else 0.0
    out.attrs["nan_quantile_rate"] = float(nan_mask.mean()) if len(nan_mask) else 0.0
    out.attrs["negative_quantile_rate"] = float(negative_input.mean()) if len(negative_input) else 0.0
    out.attrs["tau"] = tau
    return out


# ---------------------------------------------------------------------------
# pooling (correlation-aware)
# ---------------------------------------------------------------------------

def _site_marginal_params(firm_kw: np.ndarray, tau: float, rel_uncertainty: float) -> tuple[np.ndarray, np.ndarray]:
    """Back out a Normal(mu, sigma) marginal per site from its tau-quantile anchor.

    ASSUMED simplification: a real implementation would carry the site's
    full quantile curve (q05..q95) into pool() rather than reconstructing a
    spread from one point and a fixed coefficient of variation -- see
    ASSUMPTIONS['pool_relative_uncertainty'].
    """
    z = norm.ppf(tau)
    sigma = np.abs(firm_kw) * rel_uncertainty
    mu = firm_kw - z * sigma
    return mu, sigma


def _equicorrelated_sum_std(sigma: np.ndarray, rho: float) -> float:
    """Std of sum(X_i) for equicorrelated Normals with per-site std `sigma`."""
    var = float(np.sum(sigma ** 2))
    if len(sigma) > 1:
        s = float(np.sum(sigma))
        cross = s ** 2 - float(np.sum(sigma ** 2))  # sum_{i != j} sigma_i * sigma_j
        var += rho * cross
    return float(np.sqrt(max(var, 0.0)))


def _pool_analytic(firm_kw: np.ndarray, *, tau: float, rho: float, rel_uncertainty: float) -> float:
    """Closed-form pool tau-quantile assuming equicorrelated Normal marginals.

    At rho=1 this collapses exactly to sum(firm_kw) (no diversification
    benefit -- everyone's bad case coincides); rho<1 always gives a value
    >= sum(firm_kw), and it strictly falls as rho rises. That monotonicity
    is the pooling thesis expressed as arithmetic.
    """
    mu, sigma = _site_marginal_params(firm_kw, tau, rel_uncertainty)
    mu_sum = float(np.sum(mu))
    std_sum = _equicorrelated_sum_std(sigma, rho)
    z = norm.ppf(tau)
    return mu_sum + z * std_sum


def _pool_montecarlo(firm_kw: np.ndarray, *, tau: float, rho: float, rel_uncertainty: float,
                      seed: int, n_draws: int = 50_000) -> float:
    """Empirical pool tau-quantile: simulate correlated site draws, take the sample quantile."""
    n = len(firm_kw)
    if n == 0:
        return 0.0
    mu, sigma = _site_marginal_params(firm_kw, tau, rel_uncertainty)
    corr = np.full((n, n), rho)
    np.fill_diagonal(corr, 1.0)
    cov = np.outer(sigma, sigma) * corr
    rng = np.random.default_rng(seed)
    draws = rng.multivariate_normal(mean=mu, cov=cov, size=n_draws)
    totals = draws.sum(axis=1)
    return float(np.quantile(totals, tau))


def pool(firm: pd.DataFrame, *, method: Literal["sum", "empirical", "gaussian_copula"],
         sites: Sequence[str] | None = None, seed: int = 0) -> pd.DataFrame:
    """t, pool_firm_kw.

    `sum` is the naive lower bound (each site's own worst case, added up);
    `empirical`/`gaussian_copula` aggregate the *distributions* (each site's
    firm_kw anchor plus an assumed spread, correlated via
    ASSUMPTIONS['pool_default_pairwise_correlation']) and take the pool's
    own tau-quantile. The gap between them is the diversification benefit.
    """
    _require_columns(firm, ["t", "site_id", "firm_kw"])
    if method not in ("sum", "empirical", "gaussian_copula"):
        raise MarketError(f"unknown pool method {method!r}")

    df = firm.copy()
    if sites is not None:
        df = df[df["site_id"].isin(list(sites))]

    tau = firm.attrs.get("tau", 0.05)
    rho = ASSUMPTIONS["pool_default_pairwise_correlation"].value
    rel_unc = ASSUMPTIONS["pool_relative_uncertainty"].value

    rows = []
    for t_val, grp in df.groupby("t", sort=True):
        vals = grp["firm_kw"].fillna(0.0).to_numpy()
        if method == "sum":
            pool_kw = float(vals.sum())
        elif method == "gaussian_copula":
            pool_kw = _pool_analytic(vals, tau=tau, rho=rho, rel_uncertainty=rel_unc)
        else:  # empirical
            pool_kw = _pool_montecarlo(vals, tau=tau, rho=rho, rel_uncertainty=rel_unc, seed=seed)
        rows.append((t_val, max(pool_kw, 0.0)))

    out = pd.DataFrame(rows, columns=["t", "pool_firm_kw"])
    out.attrs["method"] = method
    out.attrs["assumed_correlation"] = rho
    out.attrs["tau"] = tau
    return out


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlmb = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return float(2 * r * np.arcsin(np.sqrt(a)))


def correlation_structure(load: pd.DataFrame, sites: pd.DataFrame) -> pd.DataFrame:
    """Pairwise residual correlation vs great-circle distance, plus the fitted decay length
    (in `.attrs['decay_length_km']`). Also flags the regime where total load is high (a proxy
    for the holiday/cold-snap regimes the contract names, since no calendar/weather join is
    available here): `correlation_high_load` and `regime_correlation_increase` columns.
    """
    _require_columns(load, ["t", "site_id", "load_kw"])
    _require_columns(sites, ["site_id", "lat", "lon"])

    wide = load.pivot(index="t", columns="site_id", values="load_kw").sort_index()
    site_ids = list(wide.columns)
    if len(site_ids) < 2:
        raise MarketError("correlation_structure needs at least two sites")

    corr = wide.corr(min_periods=3)

    total = wide.sum(axis=1)
    hi_thr = ASSUMPTIONS["high_load_regime_percentile"].value
    hi_mask = total >= total.quantile(hi_thr)
    if hi_mask.sum() >= 3:
        corr_hi = wide.loc[hi_mask].corr(min_periods=3)
    else:
        corr_hi = pd.DataFrame(index=corr.index, columns=corr.columns, dtype=float)

    site_loc = sites.set_index("site_id")

    rows = []
    for i, a in enumerate(site_ids):
        for b in site_ids[i + 1:]:
            dist = _haversine_km(
                float(site_loc.loc[a, "lat"]), float(site_loc.loc[a, "lon"]),
                float(site_loc.loc[b, "lat"]), float(site_loc.loc[b, "lon"]),
            )
            corr_hi_val = corr_hi.loc[a, b] if (a in corr_hi.index and b in corr_hi.columns) else np.nan
            rows.append({
                "site_a": a,
                "site_b": b,
                "distance_km": dist,
                "correlation": corr.loc[a, b],
                "correlation_high_load": corr_hi_val,
            })

    out = pd.DataFrame(rows)
    out["regime_correlation_increase"] = out["correlation_high_load"] - out["correlation"]

    valid = out.dropna(subset=["correlation", "distance_km"])
    valid = valid[valid["correlation"] > 1e-6]
    if len(valid) >= 2 and valid["distance_km"].nunique() > 1:
        slope, _intercept = np.polyfit(valid["distance_km"], np.log(valid["correlation"]), 1)
        decay_km = float(-1.0 / slope) if slope < 0 else float("inf")
    else:
        decay_km = float("nan")

    out.attrs["decay_length_km"] = decay_km
    out.attrs["high_load_threshold_percentile"] = hi_thr
    return out


def diversification_curve(firm: pd.DataFrame, *, sizes: Sequence[int], seed: int) -> pd.DataFrame:
    """n_sites, firm_kw_per_site, shortfall_rate -- the curve src/ui animates.

    For each requested portfolio size, draws several random subsets of that
    many sites (seeded), pools them analytically, reports the average firm
    capacity available per site, and separately Monte Carlo simulates
    realised delivery to measure how often the pooled promise is actually
    broken (should track tau by construction; reported empirically anyway).
    """
    _require_columns(firm, ["t", "site_id", "firm_kw"])
    tau = firm.attrs.get("tau", 0.05)
    site_ids = sorted(firm["site_id"].unique())
    rho = ASSUMPTIONS["pool_default_pairwise_correlation"].value
    rel_unc = ASSUMPTIONS["pool_relative_uncertainty"].value
    rng = np.random.default_rng(seed)

    n_replicates = 10
    n_shortfall_draws = 500

    rows = []
    for n in sizes:
        n = min(int(n), len(site_ids))
        if n <= 0:
            continue
        per_site_kw = []
        shortfalls = []
        for _rep in range(n_replicates):
            subset = rng.choice(site_ids, size=n, replace=False)
            sub = firm[firm["site_id"].isin(subset)]
            per_t = []
            for t_val, grp in sub.groupby("t"):
                vals = grp["firm_kw"].fillna(0.0).to_numpy()
                pool_kw = max(_pool_analytic(vals, tau=tau, rho=rho, rel_uncertainty=rel_unc), 0.0)
                per_t.append((t_val, pool_kw, vals))
            if not per_t:
                continue
            per_site_kw.append(float(np.mean([p[1] for p in per_t])) / n)
            for _t_val, pool_kw, vals in per_t:
                mu, sigma = _site_marginal_params(vals, tau, rel_unc)
                cov = np.outer(sigma, sigma) * rho
                np.fill_diagonal(cov, sigma ** 2)
                draws = rng.multivariate_normal(mean=mu, cov=cov, size=n_shortfall_draws)
                realised = draws.sum(axis=1)
                shortfalls.append(float(np.mean(realised < pool_kw)))
        rows.append({
            "n_sites": n,
            "firm_kw_per_site": float(np.mean(per_site_kw)) if per_site_kw else 0.0,
            "shortfall_rate": float(np.mean(shortfalls)) if shortfalls else float("nan"),
        })

    return pd.DataFrame(rows, columns=["n_sites", "firm_kw_per_site", "shortfall_rate"])


# ---------------------------------------------------------------------------
# bidding and settlement
# ---------------------------------------------------------------------------

def bid(pool_firm: pd.DataFrame, product: str, prices: pd.DataFrame) -> pd.DataFrame:
    """block_start, block_end, capacity_kw, expected_revenue_eur.

    Rounds capacity DOWN to the product's granularity and drops blocks
    under its minimum bid size -- never rounds up into a promise that
    cannot be kept. Sizes each block off the *minimum* pool_firm_kw within
    it (the promise must hold for the whole block, not on average).
    """
    _require_columns(pool_firm, ["t", "pool_firm_kw"])
    _require_columns(prices, ["t", "capacity_price_eur_mw_h"])
    if product not in PRODUCTS:
        raise MarketError(f"unknown product {product!r}; known products: {list(PRODUCTS)}")
    spec = PRODUCTS[product]

    firm_df = pool_firm.copy()
    if firm_df["pool_firm_kw"].isna().any():
        raise MarketError(
            "bid() refuses to size a block against a NaN pool_firm_kw; a missing forecast must "
            "be resolved before bidding, not silently treated as zero or unlimited capacity"
        )

    block_len_str = f"{int(spec.block_length_min)}min"
    firm_df["block_start"] = firm_df["t"].dt.floor(block_len_str)

    price_df = prices.copy()
    price_df["block_start"] = price_df["t"].dt.floor(block_len_str)
    price_by_block = price_df.groupby("block_start")["capacity_price_eur_mw_h"].mean()

    expected_rows_per_block = spec.block_length_min / 15.0

    n_raw = 0
    n_rounddown_bind = 0
    n_dropped_below_min = 0
    n_partial_coverage = 0

    out_rows = []
    for block_start, grp in firm_df.groupby("block_start"):
        n_raw += 1
        if len(grp) < expected_rows_per_block:
            n_partial_coverage += 1

        capacity_kw_raw = float(grp["pool_firm_kw"].min())
        capacity_kw = float(np.floor(capacity_kw_raw / spec.granularity_kw) * spec.granularity_kw)
        if capacity_kw < capacity_kw_raw - 1e-9:
            n_rounddown_bind += 1

        if capacity_kw < spec.min_bid_kw:
            n_dropped_below_min += 1
            continue

        block_end = block_start + pd.Timedelta(spec.block_length_min, unit="m")
        price = price_by_block.get(block_start, np.nan)
        if pd.isna(price):
            raise MarketError(
                f"bid(): no capacity price available for block starting {block_start}; "
                "refusing to invent a revenue number for a priceless block"
            )
        revenue = (capacity_kw / 1000.0) * float(price) * (spec.block_length_min / 60.0)
        out_rows.append({
            "block_start": block_start,
            "block_end": block_end,
            "capacity_kw": capacity_kw,
            "expected_revenue_eur": revenue,
        })

    out = pd.DataFrame(out_rows, columns=["block_start", "block_end", "capacity_kw", "expected_revenue_eur"])
    out.attrs["product"] = product
    out.attrs["blocks_considered"] = n_raw
    out.attrs["blocks_dropped_below_minimum"] = n_dropped_below_min
    out.attrs["blocks_dropped_rate"] = (n_dropped_below_min / n_raw) if n_raw else 0.0
    out.attrs["blocks_rounddown_bind_rate"] = (n_rounddown_bind / n_raw) if n_raw else 0.0
    out.attrs["blocks_partial_coverage"] = n_partial_coverage
    return out


def settle(bids: pd.DataFrame, delivered: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """block, capacity_kw, delivered_kw, capacity_revenue_eur, energy_cost_eur, penalty_eur,
    net_eur. Penalty rule comes from PRODUCTS (via `bids.attrs['product']`), is cited, and is
    applied on every shortfall -- a settlement that cannot go negative is not a settlement.
    """
    _require_columns(bids, ["block_start", "block_end", "capacity_kw", "expected_revenue_eur"])
    _require_columns(prices, ["t", "energy_price_eur_mwh"])

    product_name = bids.attrs.get("product")
    spec = PRODUCTS.get(product_name) if product_name else None
    if spec is None:
        raise MarketError(
            "settle(): bids frame carries no recognised 'product' in .attrs (set by bid()); "
            "cannot look up the penalty rule without knowing the product"
        )

    if "block_start" in delivered.columns and "delivered_kw" in delivered.columns and \
            len(delivered) == delivered["block_start"].nunique():
        delivered_by_block = delivered.set_index("block_start")["delivered_kw"]
    else:
        _require_columns(delivered, ["t", "delivered_kw"])
        delivered_by_block = {}
        for row in bids.itertuples(index=False):
            mask = (delivered["t"] >= row.block_start) & (delivered["t"] < row.block_end)
            sub = delivered.loc[mask, "delivered_kw"]
            delivered_by_block[row.block_start] = float(sub.mean()) if len(sub) else float("nan")

    price_series = prices.set_index("t")["energy_price_eur_mwh"]

    rows = []
    for row in bids.itertuples(index=False):
        block_start, block_end = row.block_start, row.block_end
        capacity_kw, cap_revenue = float(row.capacity_kw), float(row.expected_revenue_eur)
        block_hours = (block_end - block_start).total_seconds() / 3600.0

        if isinstance(delivered_by_block, dict):
            delivered_kw = delivered_by_block.get(block_start, float("nan"))
        else:
            delivered_kw = delivered_by_block.get(block_start, float("nan"))
        if pd.isna(delivered_kw):
            raise MarketError(
                f"settle(): no delivered reading for block {block_start}; a missing delivery "
                "record must not be treated as either full or zero delivery"
            )
        delivered_kw = float(delivered_kw)

        mask = (price_series.index >= block_start) & (price_series.index < block_end)
        block_prices = price_series[mask]
        if len(block_prices) == 0:
            raise MarketError(f"settle(): no energy price available for block {block_start}")
        avg_energy_price_eur_mwh = float(block_prices.mean())

        shortfall_kw = max(capacity_kw - delivered_kw, 0.0)
        shortfall_kwh = shortfall_kw * block_hours
        penalty_eur = shortfall_kwh * (avg_energy_price_eur_mwh / 1000.0) * spec.penalty_multiplier

        delivered_kwh = max(delivered_kw, 0.0) * block_hours
        energy_cost_eur_val = delivered_kwh * (avg_energy_price_eur_mwh / 1000.0)

        net_eur = cap_revenue - energy_cost_eur_val - penalty_eur

        rows.append({
            "block": block_start,
            "capacity_kw": capacity_kw,
            "delivered_kw": delivered_kw,
            "capacity_revenue_eur": cap_revenue,
            "energy_cost_eur": energy_cost_eur_val,
            "penalty_eur": penalty_eur,
            "net_eur": net_eur,
        })

    out = pd.DataFrame(rows, columns=["block", "capacity_kw", "delivered_kw",
                                       "capacity_revenue_eur", "energy_cost_eur",
                                       "penalty_eur", "net_eur"])
    out.attrs["product"] = product_name
    out.attrs["penalty_rule_source"] = spec.source
    out.attrs["penalty_bind_rate"] = float((out["penalty_eur"] > 0).mean()) if len(out) else 0.0
    return out


def _aggregate_load(load_kw: pd.DataFrame) -> pd.DataFrame:
    if "site_id" in load_kw.columns:
        agg = load_kw.groupby("t")["load_kw"].sum().reset_index()
    else:
        _require_columns(load_kw, ["load_kw"])
        agg = load_kw[["t", "load_kw"]].copy()
    return agg.sort_values("t").reset_index(drop=True)


def energy_cost(load_kw: pd.DataFrame, prices: pd.DataFrame) -> float:
    """Total energy cost, in EUR, of running `load_kw` against `prices['price_eur_mwh']`."""
    _require_columns(load_kw, ["t"])
    _require_columns(prices, ["t", "price_eur_mwh"])
    agg = _aggregate_load(load_kw)

    price_series = prices.set_index("t")["price_eur_mwh"]
    matched_price = agg["t"].map(price_series)
    if matched_price.isna().any():
        n_missing = int(matched_price.isna().sum())
        raise MarketError(
            f"energy_cost(): {n_missing} timestamp(s) in load_kw have no matching price row; "
            "refusing to silently price them at zero or drop them"
        )

    hours = _interval_hours(agg["t"])
    mwh = (agg["load_kw"].to_numpy() * hours.to_numpy()) / 1000.0
    return float(np.sum(mwh * matched_price.to_numpy()))


def co2(load_kw: pd.DataFrame, carbon: pd.DataFrame) -> float:
    """Total emissions, in kg CO2, of running `load_kw` against `carbon['intensity_g_kwh']`."""
    _require_columns(load_kw, ["t"])
    _require_columns(carbon, ["t", "intensity_g_kwh"])
    agg = _aggregate_load(load_kw)

    intensity_series = carbon.set_index("t")["intensity_g_kwh"]
    matched = agg["t"].map(intensity_series)
    if matched.isna().any():
        n_missing = int(matched.isna().sum())
        raise MarketError(
            f"co2(): {n_missing} timestamp(s) in load_kw have no matching carbon intensity row"
        )

    hours = _interval_hours(agg["t"])
    kwh = agg["load_kw"].to_numpy() * hours.to_numpy()
    g = kwh * matched.to_numpy()
    return float(np.sum(g) / 1000.0)


def peakers_displaced(pool_firm_mw: float) -> tuple[float, str]:
    """(count, the assumption used) -- the loudest claim the project makes, inspectable in
    one click: the count is always returned next to the ASSUMPTIONS entry it depends on."""
    a = ASSUMPTIONS["peaker_plant_capacity_mw"]
    count = float(pool_firm_mw) / a.value
    return count, f"ASSUMPTIONS['peaker_plant_capacity_mw'] = {a.value:.0f} {a.unit} ({a.source})"
