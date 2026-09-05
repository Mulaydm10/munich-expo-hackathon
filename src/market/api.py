"""src.market — public API.

Firm capacity, bidding and settlement in euros and CO2: issue #14's scope, the
single-site money path end to end. Portfolio pooling (`pool`,
`correlation_structure`, `diversification_curve`) is a separate, deferred
issue per design's sequencing -- that work exists on branch
`parked/market-pooling`, not here, so it does not pre-empt acceptance
criteria design has not written yet.

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
    penalty_multiplier: float  # default rate, applied to |the block's own energy price|, per kWh
    # of shortfall; settle()'s penalty_multiplier= kwarg overrides this per call for scenario runs
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

    # Tracked (and exposed below) regardless of whether it crosses the raise threshold: a
    # 40%-inverted frame that doesn't trip the >0.5 guard must still leave a trace, not
    # proceed invisibly. A rate asserted only where it binds can't distinguish a working
    # measurement from one stuck at 0%.
    lower_above_median_rate = 0.0
    if med_col in quantiles.columns:
        med = quantiles[med_col].fillna(0.0)
        bad = (lo_filled > med + 1e-9) & ~nan_mask
        frac_bad = float(bad.mean()) if len(bad) else 0.0
        lower_above_median_rate = frac_bad
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
    out.attrs["lower_above_median_rate"] = lower_above_median_rate
    out.attrs["tau"] = tau
    return out


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

    expected_rows_per_block = int(round(spec.block_length_min / 15.0))

    n_raw = 0
    n_rounddown_bind = 0
    n_dropped_below_min = 0

    out_rows = []
    for block_start, grp in firm_df.groupby("block_start"):
        n_raw += 1

        # A block's committed window is the full block_length_min, so it must be backed by
        # a forecast row for every 15-min interval in that window -- not fewer (a gap we'd be
        # selling capacity for with no forecast at all) and not more (duplicates/misassigned
        # rows). Counting the shortfall and bidding anyway is itself the coercion this guards
        # against, so this raises rather than merely tracking a rate.
        expected_grid = pd.date_range(block_start, periods=expected_rows_per_block, freq="15min")
        if set(grp["t"]) != set(expected_grid):
            raise MarketError(
                f"bid(): block starting {block_start} has {grp['t'].nunique()} distinct 15-min "
                f"forecast row(s), not the {expected_rows_per_block} needed to cover the full "
                f"{int(spec.block_length_min)}-minute committed window; refusing to sell "
                "capacity for a period with no forecast backing it"
            )

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
    return out


def _penalty_rate_eur_per_kwh(avg_energy_price_eur_mwh: float, multiplier: float) -> tuple[float, bool]:
    """Non-negative EUR/kWh penalty rate for a shortfall, priced off the block's own average
    energy price and `multiplier`. A negative day-ahead price is a genuine German market
    condition (and correlates with exactly the hours a downward-flexibility product gets
    dispatched), so it must never flip a non-delivery into a reward -- this always prices off
    the *magnitude* of the price, never its sign. Returns (rate, was_negative_price) so the
    caller can track how often the clamp actually bound."""
    was_negative = avg_energy_price_eur_mwh < 0.0
    rate = abs(avg_energy_price_eur_mwh) / 1000.0 * multiplier
    return rate, was_negative


def settle(
    bids: pd.DataFrame,
    delivered: pd.DataFrame,
    prices: pd.DataFrame,
    *,
    penalty_multiplier: float | None = None,
) -> pd.DataFrame:
    """block, capacity_kw, delivered_kw, capacity_revenue_eur, energy_cost_eur, penalty_eur,
    net_eur. Penalty rate comes from PRODUCTS by default (via `bids.attrs['product']`), cited in
    `.attrs['penalty_rule_source']`; pass `penalty_multiplier` to run an explicit scenario at a
    different rate instead (e.g. "what if the multiplier were 4x") -- either way the multiplier
    actually used is surfaced in `.attrs['penalty_multiplier']`, never left implicit. Applied on
    every shortfall -- a settlement that cannot go negative is not a settlement.
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

    multiplier = spec.penalty_multiplier if penalty_multiplier is None else float(penalty_multiplier)
    multiplier_source = (
        spec.source if penalty_multiplier is None
        else f"override: caller-supplied penalty_multiplier={multiplier}x (scenario parameter, "
             f"lane default is {spec.penalty_multiplier}x per {spec.source})"
    )

    # Pre-aggregated calling convention: caller already resolved one delivered_kw value per
    # block (no per-interval detail available to check), used as-is.
    use_preaggregated = (
        "block_start" in delivered.columns and "delivered_kw" in delivered.columns
        and len(delivered) == delivered["block_start"].nunique()
    )
    if use_preaggregated:
        delivered_scalar_by_block = delivered.set_index("block_start")["delivered_kw"]
    else:
        _require_columns(delivered, ["t", "delivered_kw"])

    price_series = prices.set_index("t")["energy_price_eur_mwh"]
    expected_rows_per_block = int(round(spec.block_length_min / 15.0))

    rows = []
    n_negative_price = 0
    n_shortfall = 0
    for row in bids.itertuples(index=False):
        block_start, block_end = row.block_start, row.block_end
        capacity_kw, cap_revenue = float(row.capacity_kw), float(row.expected_revenue_eur)
        block_hours = (block_end - block_start).total_seconds() / 3600.0

        mask_price = (price_series.index >= block_start) & (price_series.index < block_end)
        block_prices = price_series[mask_price]
        if len(block_prices) == 0:
            raise MarketError(f"settle(): no energy price available for block {block_start}")
        avg_energy_price_eur_mwh = float(block_prices.mean())

        if use_preaggregated:
            delivered_kw = delivered_scalar_by_block.get(block_start, float("nan"))
            if pd.isna(delivered_kw):
                raise MarketError(
                    f"settle(): no delivered reading for block {block_start}; a missing "
                    "delivery record must not be treated as either full or zero delivery"
                )
            delivered_kw = float(delivered_kw)
            shortfall_kwh = max(capacity_kw - delivered_kw, 0.0) * block_hours
            delivered_kwh = max(delivered_kw, 0.0) * block_hours
            delivered_kw_report = delivered_kw
        else:
            mask = (delivered["t"] >= block_start) & (delivered["t"] < block_end)
            sub = delivered.loc[mask].sort_values("t")
            if len(sub) == 0:
                raise MarketError(
                    f"settle(): no delivered reading for block {block_start}; a missing "
                    "delivery record must not be treated as either full or zero delivery"
                )

            # Full-block coverage, checked against the exact 15-min grid the block commits
            # to -- never just "at least one reading". A single reading (or any strict
            # subset of the grid) must not be averaged over and stretched across the whole
            # block: that is precisely how a partially-reported block gets settled as if it
            # were fully reported, and how intra-block over-delivery masks a real shortfall
            # elsewhere in the same block (both #24-review-flagged shapes share this root
            # cause).
            expected_grid = pd.date_range(block_start, periods=expected_rows_per_block, freq="15min")
            if set(sub["t"]) != set(expected_grid):
                raise MarketError(
                    f"settle(): block {block_start} has {sub['t'].nunique()} distinct 15-min "
                    f"delivery reading(s), not the {expected_rows_per_block} needed to cover "
                    "the full committed window; a partially-reported block must not be "
                    "settled as if it were completely reported"
                )
            if sub["delivered_kw"].isna().any():
                raise MarketError(f"settle(): NaN delivered_kw reading(s) within block {block_start}")

            sub_by_t = sub.set_index("t")["delivered_kw"].reindex(expected_grid)
            delivered_vals = sub_by_t.to_numpy(dtype="float64")
            boundaries = list(expected_grid) + [block_end]
            interval_hours = np.array([
                (boundaries[i + 1] - boundaries[i]).total_seconds() / 3600.0
                for i in range(len(expected_grid))
            ])

            # Shortfall is clipped to zero PER INTERVAL, before summing -- not on a
            # block-level mean. Over-delivery in one interval must never offset
            # under-delivery in another: the promise is per-interval, so the penalty has to
            # be too.
            per_interval_shortfall_kw = np.clip(capacity_kw - delivered_vals, 0.0, None)
            shortfall_kwh = float(np.sum(per_interval_shortfall_kw * interval_hours))
            delivered_kwh = float(np.sum(np.clip(delivered_vals, 0.0, None) * interval_hours))
            delivered_kw_report = float(np.mean(delivered_vals))

        # Penalty rate is priced off |price|, never a signed price: a negative day-ahead
        # price (a real German day-ahead condition) must never flip a non-delivery penalty
        # into a reward.
        penalty_rate_eur_per_kwh, was_negative_price = _penalty_rate_eur_per_kwh(
            avg_energy_price_eur_mwh, multiplier
        )
        if was_negative_price:
            n_negative_price += 1
        penalty_eur = shortfall_kwh * penalty_rate_eur_per_kwh
        if shortfall_kwh > 1e-9:
            n_shortfall += 1

        energy_cost_eur_val = delivered_kwh * (avg_energy_price_eur_mwh / 1000.0)
        net_eur = cap_revenue - energy_cost_eur_val - penalty_eur

        rows.append({
            "block": block_start,
            "capacity_kw": capacity_kw,
            "delivered_kw": delivered_kw_report,
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
    out.attrs["penalty_multiplier"] = multiplier
    out.attrs["penalty_multiplier_source"] = multiplier_source
    # Bind rate is measured off the physical shortfall (shortfall_kwh > 0), never off the
    # sign of penalty_eur -- a 100% shortfall during negative prices must still show a 100%
    # bind rate, not a 0% one just because the (now-fixed) euro amount happens to be small.
    out.attrs["penalty_bind_rate"] = float(n_shortfall / len(rows)) if rows else 0.0
    out.attrs["negative_energy_price_rate"] = float(n_negative_price / len(rows)) if rows else 0.0
    return out


def _aggregate_load(load_kw: pd.DataFrame) -> pd.DataFrame:
    _require_columns(load_kw, ["load_kw"])
    # A missing (NaN) load reading must raise, matching the lane's existing policy for a
    # missing price or carbon-intensity record -- never propagate silently into a NaN total
    # (or, via a group-sum's default skipna, into a silently-wrong non-NaN total).
    if load_kw["load_kw"].isna().any():
        n_missing = int(load_kw["load_kw"].isna().sum())
        raise MarketError(
            f"{n_missing} load_kw reading(s) are missing (NaN); a missing load record must "
            "raise a MarketError, never silently resolve to NaN or a wrong total downstream"
        )
    if "site_id" in load_kw.columns:
        agg = load_kw.groupby("t")["load_kw"].sum().reset_index()
    else:
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
