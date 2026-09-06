"""src.market — public API.

Firm capacity, portfolio pooling, bidding and settlement in euros and CO2.

The single-site money path (`firm_capacity`, `bid`, `settle`, `energy_cost`,
`co2`, `peakers_displaced`) shipped in #14. The portfolio path (`pool`,
`correlation_structure`, `diversification_curve`) shipped in #33 and carries
the project's central claim: a pool of imperfectly-correlated sites can sell
a firmer promise than the sum of its members' own conservative quantiles.
Every number in that claim is measured -- the dependence structure from
residuals, the shortfall rate from realised load -- never assumed.

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

import re
from dataclasses import dataclass
from datetime import date as _date, timedelta as _timedelta
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
    "cold_snap_temp_percentile": Assumption(
        value=0.10,
        unit="percentile (0-1)",
        source="ASSUMED",
        note=(
            "A 'cold snap' is defined as the coldest 10% of intervals in the weather frame "
            "supplied to correlation_structure(). GUESS: not taken from a DWD/BDEW cold-spell "
            "definition -- it is a relative threshold chosen so the regime is always populated "
            "for any fixture. Anything shown to a judge off this number must say so."
        ),
    ),
    "peak_load_regime_percentile": Assumption(
        value=0.90,
        unit="percentile (0-1)",
        source="ASSUMED",
        note=(
            "The 'peak load' regime is the top 10% of intervals by portfolio total load. "
            "GUESS: a relative threshold, not a system-peak definition from a TSO."
        ),
    ),
    "correlation_regime_flag_threshold": Assumption(
        value=0.80,
        unit="correlation (dimensionless)",
        source="ASSUMED",
        note=(
            "Mean pairwise residual correlation at or above which correlation_structure() "
            "flags a regime as 'correlation -> 1', i.e. the pool stops diversifying. GUESS: "
            "0.8 is a judgement call, not a literature value; the flag is a prompt to look at "
            "the measured number next to it, never a substitute for it."
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

    # Carry the whole marginal quantile curve through, not just the tau one.
    # pool() aggregates *distributions*; given a single quantile per site it
    # would have to invent a spread, and an invented spread would make the
    # diversification benefit an assumption instead of a measurement. Each
    # firm_q**_kw column is that quantile put through the same floor
    # subtraction and zero-clip as firm_kw, so firm_q<tau>_kw == firm_kw.
    curve = []
    for col in quantiles.columns:
        m = re.fullmatch(r"q(\d{2})", str(col))
        if m:
            curve.append((int(m.group(1)) / 100.0, str(col)))
    curve.sort()
    for tau_v, col in curve:
        out[f"firm_q{int(round(tau_v * 100)):02d}_kw"] = (
            (quantiles[col].fillna(0.0) - floor_kw).clip(lower=0.0).to_numpy()
        )
    out.attrs["marginal_quantile_taus"] = [tv for tv, _ in curve]
    out.attrs["floor_clamp_rate"] = float(floor_bound.mean()) if len(floor_bound) else 0.0
    out.attrs["nan_quantile_rate"] = float(nan_mask.mean()) if len(nan_mask) else 0.0
    out.attrs["negative_quantile_rate"] = float(negative_input.mean()) if len(negative_input) else 0.0
    out.attrs["lower_above_median_rate"] = lower_above_median_rate
    out.attrs["tau"] = tau
    return out


# ---------------------------------------------------------------------------
# pooling: measured correlation, copula aggregation, diversification curve
# ---------------------------------------------------------------------------
#
# The project's central claim lives here: aggregating imperfectly-correlated
# sites yields a firm promise LARGER than the sum of the per-site conservative
# quantiles. Three rules shape the implementation:
#
#   1. The dependence structure is *measured*, never assumed. `sum` is the
#      naive lower bound; `empirical` and `gaussian_copula` aggregate the
#      per-site marginal quantile curves through a dependence structure taken
#      from residuals (either the caller's `correlation_structure` output, or
#      measured from the firm frame itself). There is no "assumed rho"
#      constant anywhere in this file, and independence is never assumed --
#      independence flatters the answer exactly as much as perfect
#      correlation damns it, and an over-stated firm capacity is a real
#      penalty in settle().
#   2. The diversification benefit is reported against the *sum of individual
#      quantiles* -- a number this code did not choose -- never against an
#      internal baseline.
#   3. shortfall_rate is measured against realised load, never simulated from
#      the same model that produced the promise. A rate derived from the
#      promise's own generator proves nothing (the #24 penalty_bind_rate
#      lesson, and CONVENTIONS.md "measure the physical quantity").

# Monte-Carlo sizes. These are numerical parameters of the estimator, not
# quantities the project quotes, so they are module constants rather than
# ASSUMPTIONS entries. Draws are jittered-stratified (see _stratified_uniforms)
# so the tail quantile is far more stable than plain i.i.d. sampling at the
# same count; that matters because the whole result is a 5th percentile.
_POOL_DRAWS = 8000
_CURVE_DRAWS = 2000
_CURVE_REPLICATES = 12
_CHUNK_CELLS = 2_000_000

# A pooled quantile below the sum of the per-site quantiles is impossible in
# the population for any dependence structure this module can build, but the
# *estimator* is a sample quantile, so it can land microscopically below in
# the near-comonotonic regime where the true answer IS the sum. That is
# floored to the sum and the frequency is reported (`sum_floor_bind_rate`).
# A gap larger than this fraction of the sum is not estimator noise -- it is
# an aggregation bug -- and raises instead of being floored away.
_SUM_FLOOR_MATERIAL_REL = 0.20

# Fewer rows than this inside a regime is not a regime, it is an anecdote.
_MIN_REGIME_ROWS = 8


def _curve_columns(df: pd.DataFrame) -> tuple[list[float], list[str]]:
    """The per-site marginal quantile curve carried by a `firm_capacity` frame.

    `firm_capacity` emits `firm_q05_kw … firm_q95_kw` alongside `firm_kw`;
    pooling needs the whole curve, because aggregating *distributions* is the
    entire point -- a single quantile per site cannot be aggregated without
    inventing a spread, and an invented spread is an assumed answer.
    """
    found: list[tuple[float, str]] = []
    for col in df.columns:
        m = re.fullmatch(r"firm_q(\d{2})_kw", str(col))
        if m:
            found.append((int(m.group(1)) / 100.0, str(col)))
    found.sort()
    return [t for t, _ in found], [c for _, c in found]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km (mean Earth radius 6371 km)."""
    r = 6371.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlmb = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return float(2 * r * np.arcsin(np.sqrt(min(1.0, a))))


def _easter_sunday(year: int) -> _date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lam = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lam) // 451
    month, day = divmod(h + lam - 7 * m + 114, 31)
    return _date(year, month, day + 1)


def _german_public_holidays(year: int) -> set:
    """The nine nationwide German public holidays for `year`.

    Nationwide only (state-specific days such as Fronleichnam are deliberately
    excluded): a portfolio spanning several Bundeslaender should not have a
    regime that applies to part of it. Reimplemented here rather than imported
    from src/forecast because a lane may only import another lane's `api`
    module, and that calendar is private to it.
    """
    easter = _easter_sunday(year)
    return {
        _date(year, 1, 1),                        # Neujahr
        easter - _timedelta(days=2),              # Karfreitag
        easter + _timedelta(days=1),              # Ostermontag
        _date(year, 5, 1),                        # Tag der Arbeit
        easter + _timedelta(days=39),             # Christi Himmelfahrt
        easter + _timedelta(days=50),             # Pfingstmontag
        _date(year, 10, 3),                       # Tag der Deutschen Einheit
        _date(year, 12, 25),                      # 1. Weihnachtstag
        _date(year, 12, 26),                      # 2. Weihnachtstag
    }


def _time_of_day_residuals(wide: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    """Remove each site's OWN time-of-day mean profile; return (residuals, rate).

    Residuals, not raw load. Two depots that both fill up at 08:00 and empty at
    18:00 are not thereby correlated in the way pooling cares about: that shape
    is forecastable and is already inside each site's quantiles. What threatens
    a pooled promise is the sites deviating from their own normal shape at the
    same moment. Correlating raw load would report ~1.0 everywhere and
    manufacture the result out of the daily commuter cycle.

    A time-of-day bucket seen only once has a residual of exactly zero by
    construction -- a coercion that silently deflates every correlation it
    touches -- so the fraction of rows in such buckets is returned and surfaced
    by the caller as `residual_degenerate_bucket_rate`.
    """
    idx = pd.DatetimeIndex(wide.index)
    bucket = idx.hour * 60 + idx.minute
    counts = pd.Series(bucket).value_counts()
    degenerate = pd.Series(bucket).map(counts).to_numpy() < 2
    resid = wide.sub(wide.groupby(bucket).transform("mean"))
    rate = float(degenerate.mean()) if len(degenerate) else 0.0
    return resid, rate


def _pseudo_observations(resid: pd.DataFrame) -> pd.DataFrame:
    """Rank-transform each site's residual series to (0, 1) -- the empirical copula."""
    m = len(resid)
    return resid.rank(axis=0, method="average") / (m + 1.0)


def _correlation_from_residuals(resid: pd.DataFrame) -> pd.DataFrame:
    flat = resid.std(axis=0, ddof=0)
    dead = [str(s) for s, v in flat.items() if not np.isfinite(v) or v <= 1e-12]
    if dead:
        raise MarketError(
            f"cannot measure correlation: site(s) {dead} have zero residual variation, so "
            "their pairwise correlation is undefined. Pass an explicit `correlation=` "
            "(from correlation_structure) rather than letting an undefined correlation be "
            "silently treated as zero -- assumed independence overstates the pooled promise."
        )
    return resid.corr()


def _nearest_psd(corr: np.ndarray) -> tuple[np.ndarray, float]:
    """Project a correlation matrix onto the PSD cone; return (matrix, repair magnitude).

    A measured correlation matrix with missing/regime-subset entries can come
    back indefinite, and an indefinite matrix has no Cholesky factor. Clipping
    the negative eigenvalues is a coercion, so its magnitude (the largest
    negative eigenvalue removed, 0.0 when nothing was repaired) is returned and
    surfaced as `correlation_psd_repair`.
    """
    sym = (corr + corr.T) / 2.0
    vals, vecs = np.linalg.eigh(sym)
    worst = float(min(vals.min(), 0.0))
    if worst >= 0.0:
        return sym, 0.0
    vals = np.clip(vals, 0.0, None)
    fixed = vecs @ np.diag(vals) @ vecs.T
    d = np.sqrt(np.clip(np.diag(fixed), 1e-12, None))
    fixed = fixed / np.outer(d, d)
    np.fill_diagonal(fixed, 1.0)
    return fixed, -worst


def _stratified_uniforms(n_draws: int, n_dim: int, rng: np.random.Generator) -> np.ndarray:
    """Jittered-stratified (Latin-hypercube) uniforms, shape (n_draws, n_dim).

    Each dimension's draws cover [0, 1] one stratum apiece, jittered inside the
    stratum by `rng`. The seed therefore genuinely changes the draw (different
    seeds are independent) while the tail quantile has a fraction of the noise
    of i.i.d. sampling -- which is what keeps the perfect-correlation boundary
    case collapsing onto `sum` instead of wobbling around it.
    """
    out = np.empty((n_draws, n_dim), dtype=float)
    base = np.arange(n_draws, dtype=float)
    for j in range(n_dim):
        out[:, j] = (base + rng.random(n_draws))[rng.permutation(n_draws)] / n_draws
    return np.clip(out, 1e-12, 1.0 - 1e-12)


def _gaussian_copula_uniforms(corr: np.ndarray, n_draws: int,
                              rng: np.random.Generator) -> np.ndarray:
    n = corr.shape[0]
    z = norm.ppf(_stratified_uniforms(n_draws, n, rng))
    jitter = 1e-9
    chol = np.linalg.cholesky(corr + np.eye(n) * jitter)
    return np.clip(norm.cdf(z @ chol.T), 1e-12, 1.0 - 1e-12)


def _empirical_copula_uniforms(pseudo: np.ndarray, n_draws: int,
                               rng: np.random.Generator) -> np.ndarray:
    """Resample the observed joint ranks -- no parametric dependence assumption.

    The observed rank vectors are resampled with a stratified position `s` in
    [0, 1) rather than a uniform row index, and the sub-rank offset is shared
    across sites within a draw. Under comonotonic residuals that makes the
    first site's uniform exactly `s`, so the perfectly-correlated boundary case
    reproduces `sum` rather than being smeared by resampling noise.
    """
    m, n = pseudo.shape
    order = np.argsort(pseudo[:, 0], kind="stable")
    ranks = np.clip(np.rint(pseudo * (m + 1.0)).astype(int) - 1, 0, m - 1)
    s = _stratified_uniforms(n_draws, 1, rng)[:, 0]
    idx = np.clip((s * m).astype(int), 0, m - 1)
    frac = s * m - idx
    rows = order[idx]
    u = (ranks[rows, :] + frac[:, None]) / m
    return np.clip(u, 1e-12, 1.0 - 1e-12)


def _marginal_draws(vals: np.ndarray, u: np.ndarray, z_knots: np.ndarray) -> tuple[np.ndarray, int]:
    """Invert one site's marginal quantile curve at uniforms `u`.

    `vals` is (n_t, k): the site's firm_q**_kw curve at each timestamp.
    Interpolation is linear in z = Phi^-1(tau) space, so the tails extrapolate
    like a Normal instead of like a straight line in probability -- the tail is
    where a 5th percentile lives, and linear-in-tau extrapolation there is
    wildly optimistic. Draws below zero are clipped (a site cannot deliver
    negative reduction) and the clip count is returned so the rate is
    observable.
    """
    z = norm.ppf(u)
    k = len(z_knots)
    seg = np.clip(np.searchsorted(z_knots, z) - 1, 0, k - 2)
    w = (z - z_knots[seg]) / (z_knots[seg + 1] - z_knots[seg])
    lo = vals[:, seg]
    hi = vals[:, seg + 1]
    x = lo + (hi - lo) * w[None, :]
    n_clipped = int(np.count_nonzero(x < 0.0))
    return np.clip(x, 0.0, None), n_clipped


def _matrix_to_mapping(mat: pd.DataFrame) -> dict:
    """Square correlation frame -> nested plain dict.

    Correlation matrices travel in `.attrs`, and pandas compares two frames'
    `.attrs` with `==` whenever it finalises a concat (which `DataFrame.__repr__`
    itself triggers on a wide frame). A DataFrame or ndarray in there makes that
    comparison raise "truth value is ambiguous" -- i.e. merely *printing* the
    returned frame would explode in a consumer lane. Plain nested dicts of
    floats compare cleanly.
    """
    return {str(a): {str(b): float(mat.loc[a, b]) for b in mat.columns} for a in mat.index}


def _mapping_to_matrix(mapping: dict, site_ids: list[str]) -> np.ndarray:
    missing = [s for s in site_ids if s not in mapping]
    if not missing:
        missing = [b for a in site_ids for b in site_ids if b not in mapping[a]]
    if missing:
        raise MarketError(
            f"the supplied correlation mapping is missing site(s) {sorted(set(missing))}; "
            "refusing to assume a correlation for a site it does not cover"
        )
    return np.array([[float(mapping[a][b]) for b in site_ids] for a in site_ids], dtype=float)


def _corr_matrix_from_arg(correlation: object, site_ids: list[str]) -> tuple[np.ndarray, str]:
    """Accept a nested dict, a square correlation frame, or the pairwise frame."""
    if isinstance(correlation, dict):
        return _mapping_to_matrix(correlation, site_ids), "supplied:mapping"
    if isinstance(correlation, pd.DataFrame) and {"site_a", "site_b", "correlation"} <= set(correlation.columns):
        mat = pd.DataFrame(np.eye(len(site_ids)), index=site_ids, columns=site_ids)
        seen = set()
        for row in correlation.itertuples(index=False):
            a, b = str(row.site_a), str(row.site_b)
            if a in mat.index and b in mat.index:
                mat.loc[a, b] = mat.loc[b, a] = float(row.correlation)
                seen.add((a, b))
        missing = [(a, b) for i, a in enumerate(site_ids) for b in site_ids[i + 1:]
                   if (a, b) not in seen and (b, a) not in seen]
        if missing:
            raise MarketError(
                f"the supplied correlation frame has no entry for pair(s) {missing[:5]}; a "
                "missing pair must not default to zero correlation (that is assumed "
                "independence, which overstates the pooled promise)"
            )
        return mat.to_numpy(dtype=float), "supplied:correlation_structure"
    if isinstance(correlation, pd.DataFrame):
        missing = [s for s in site_ids if s not in correlation.index or s not in correlation.columns]
        if missing:
            raise MarketError(
                f"the supplied correlation matrix is missing site(s) {missing}; refusing to "
                "assume a correlation for a site the matrix does not cover"
            )
        return correlation.loc[site_ids, site_ids].to_numpy(dtype=float), "supplied:matrix"
    raise MarketError(
        "correlation= must be a nested {site: {site: rho}} mapping, a square DataFrame "
        "indexed by site_id, or the pairwise frame returned by correlation_structure; got "
        f"{type(correlation).__name__}"
    )


def pool(firm: pd.DataFrame, *, method: Literal["sum", "empirical", "gaussian_copula"],
         sites: Sequence[str] | None = None, seed: int = 0,
         correlation: pd.DataFrame | dict | None = None,
         n_draws: int | None = None) -> pd.DataFrame:
    """t, pool_firm_kw -- the firm capacity of a *portfolio*.

    `sum` is the naive lower bound: every site's own 5th percentile added up,
    i.e. the portfolio priced as if every site had its worst day at the same
    moment. `empirical` and `gaussian_copula` aggregate the per-site marginal
    *distributions* (the `firm_q**_kw` curve `firm_capacity` carries) through a
    measured dependence structure and take the pool's own tau-quantile. The gap
    between them, reported in `.attrs['diversification_benefit_kw_mean']`
    against the `sum` baseline, is the headline result.

    Dependence is never assumed. Pass `correlation=` -- either
    `correlation_structure`'s pairwise frame (recommended: its correlations are
    fitted on realised-load residuals, and the frame carries the joint ranks
    `empirical` needs) or a square matrix indexed by site_id. With no
    `correlation=`, the structure is measured from the firm frame's own
    residuals; a site with no residual variation raises rather than silently
    contributing an independent (flattering) column.

    A one-site pool returns that site's own firm_kw exactly: there is nothing
    to aggregate, so the pool's tau-quantile IS the site's tau-quantile, and no
    estimator is allowed near it.

    Observability (`.attrs`): `sum_floor_bind_rate` / `sum_floor_max_gap_kw`
    (how often, and by how much, the sample quantile landed under the naive
    sum and was floored to it), `draw_clip_rate` (fraction of simulated site
    draws clipped at zero), `correlation_psd_repair` (largest negative
    eigenvalue removed from the measured matrix), `mean_pairwise_correlation`,
    `correlation_source`, `n_draws`.
    """
    _require_columns(firm, ["t", "site_id", "firm_kw"])
    if method not in ("sum", "empirical", "gaussian_copula"):
        raise MarketError(
            f"unknown pool method {method!r}; expected 'sum', 'empirical' or 'gaussian_copula'"
        )

    df = firm
    if sites is not None:
        wanted = [str(s) for s in sites]
        known = set(df["site_id"].astype(str))
        unknown = [s for s in wanted if s not in known]
        if unknown:
            raise MarketError(
                f"pool(): site(s) {unknown} requested but absent from the firm frame; "
                "refusing to pool a portfolio that silently lost members"
            )
        df = df[df["site_id"].astype(str).isin(wanted)]

    if df["firm_kw"].isna().any():
        raise MarketError(
            "pool(): NaN firm_kw in the input; a missing per-site promise must be resolved, "
            "never treated as zero (which understates) or dropped (which overstates)"
        )

    site_ids = sorted(str(s) for s in df["site_id"].unique())
    n_sites = len(site_ids)
    if n_sites == 0:
        raise MarketError("pool(): no sites to pool")

    ts = pd.DatetimeIndex(sorted(df["t"].unique()))
    per_t_sites = df.groupby("t")["site_id"].nunique()
    if not bool((per_t_sites == n_sites).all()):
        bad = per_t_sites[per_t_sites != n_sites]
        raise MarketError(
            f"pool(): ragged panel -- {len(bad)} timestamp(s) do not carry all {n_sites} sites "
            f"(first offender {bad.index[0]} has {int(bad.iloc[0])}). Pooling a subset of the "
            "portfolio at some timestamps would understate those timestamps and silently "
            "change what is being sold."
        )

    tau = float(firm.attrs.get("tau", 0.05))
    sum_kw = df.groupby("t")["firm_kw"].sum().reindex(ts).to_numpy(dtype=float)

    out = pd.DataFrame({"t": ts, "pool_firm_kw": sum_kw})
    out.attrs["method"] = method
    out.attrs["tau"] = tau
    out.attrs["n_sites"] = n_sites
    out.attrs["sites"] = site_ids
    out.attrs["sum_firm_kw_mean"] = float(np.mean(sum_kw)) if len(sum_kw) else 0.0
    out.attrs["diversification_benefit_kw_mean"] = 0.0
    out.attrs["sum_floor_bind_rate"] = 0.0
    out.attrs["sum_floor_max_gap_kw"] = 0.0
    out.attrs["draw_clip_rate"] = 0.0
    out.attrs["correlation_psd_repair"] = 0.0
    out.attrs["single_site_exact"] = bool(n_sites == 1)

    if method == "sum":
        out.attrs["correlation_source"] = "none (naive lower bound)"
        out.attrs["mean_pairwise_correlation"] = float("nan")
        out.attrs["n_draws"] = 0
        return out

    if n_sites == 1:
        # Exact, not estimated: the pool's tau-quantile is the site's own.
        out.attrs["correlation_source"] = "none (single-site pool is exact)"
        out.attrs["mean_pairwise_correlation"] = float("nan")
        out.attrs["n_draws"] = 0
        return out

    taus, curve_cols = _curve_columns(df)
    if len(taus) < 2:
        raise MarketError(
            f"pool(method={method!r}) needs the per-site marginal quantile curve "
            "(firm_q05_kw … firm_q95_kw, as emitted by firm_capacity) to aggregate "
            "distributions. Only a single quantile per site was supplied, and manufacturing "
            "a spread around it would make the diversification benefit an assumption "
            "rather than a measurement."
        )

    # dependence structure -------------------------------------------------
    pseudo: np.ndarray | None = None
    if correlation is not None:
        corr_raw, corr_source = _corr_matrix_from_arg(correlation, site_ids)
        if isinstance(correlation, pd.DataFrame):
            po = correlation.attrs.get("pseudo_observations")
            if isinstance(po, dict) and all(s in po for s in site_ids):
                pseudo = np.column_stack([np.asarray(po[s], dtype=float) for s in site_ids])
    else:
        wide = df.pivot(index="t", columns="site_id", values="firm_kw").sort_index()
        wide.columns = [str(c) for c in wide.columns]
        wide = wide[site_ids]
        resid, _degenerate = _time_of_day_residuals(wide)
        corr_df = _correlation_from_residuals(resid)
        corr_raw = corr_df.loc[site_ids, site_ids].to_numpy(dtype=float)
        pseudo = _pseudo_observations(resid)[site_ids].to_numpy(dtype=float)
        corr_source = "measured:firm_kw residuals"

    if not np.isfinite(corr_raw).all():
        raise MarketError(
            "the correlation structure contains non-finite entries; a NaN correlation must "
            "not reach the aggregation (NaN comparisons take the permissive branch silently)"
        )
    corr_psd, repair = _nearest_psd(np.clip(corr_raw, -1.0, 1.0))
    off = ~np.eye(n_sites, dtype=bool)
    out.attrs["correlation_source"] = corr_source
    out.attrs["correlation_psd_repair"] = repair
    out.attrs["mean_pairwise_correlation"] = float(corr_raw[off].mean())

    if method == "empirical":
        if pseudo is None:
            raise MarketError(
                "pool(method='empirical') needs the joint ranks, not just a correlation "
                "matrix. Pass the frame returned by correlation_structure (it carries them in "
                ".attrs['pseudo_observations']), or omit correlation= to measure them from the "
                "firm frame; use method='gaussian_copula' if only a matrix is available."
            )
        if pseudo.shape[0] < 4:
            raise MarketError(
                f"pool(method='empirical') has only {pseudo.shape[0]} joint observation(s); "
                "an empirical copula estimated from that is not evidence"
            )

    # marginal curves ------------------------------------------------------
    mats = []
    for col in curve_cols:
        w = df.pivot(index="t", columns="site_id", values=col)
        w.columns = [str(c) for c in w.columns]
        w = w.reindex(index=ts)[site_ids]
        if w.isna().any().any():
            raise MarketError(f"pool(): missing {col} for at least one (t, site_id)")
        mats.append(w.to_numpy(dtype=float))
    cube = np.stack(mats, axis=0)  # (k, n_t, n_sites)
    if bool((np.diff(cube, axis=0) < -1e-6).any()):
        raise MarketError(
            "pool(): a site's marginal quantile curve is not monotone in tau (a higher "
            "quantile sits below a lower one). Refusing to silently sort it -- crossed "
            "quantiles mean the upstream forecast is mislabelled or broken."
        )

    rng = np.random.default_rng(seed)
    n_draws = _POOL_DRAWS if n_draws is None else int(n_draws)
    if n_draws < 100:
        raise MarketError(f"pool(): n_draws={n_draws} is too few to estimate a {tau:.0%} quantile")
    if method == "gaussian_copula":
        u = _gaussian_copula_uniforms(corr_psd, n_draws, rng)
    else:
        u = _empirical_copula_uniforms(pseudo, n_draws, rng)

    z_knots = norm.ppf(np.asarray(taus, dtype=float))
    n_t = len(ts)
    step = max(1, int(_CHUNK_CELLS // max(n_draws, 1)))
    pooled = np.empty(n_t, dtype=float)
    n_clipped = 0
    n_cells = 0
    for start in range(0, n_t, step):
        stop = min(start + step, n_t)
        totals = np.zeros((stop - start, n_draws), dtype=float)
        for i in range(n_sites):
            vals = cube[:, start:stop, i].T  # (chunk, k)
            x, c = _marginal_draws(vals, u[:, i], z_knots)
            totals += x
            n_clipped += c
            n_cells += x.size
        pooled[start:stop] = np.quantile(totals, tau, axis=1)

    # The naive sum is the floor the contract guarantees. Report how often the
    # sample quantile landed beneath it (only possible in the near-comonotonic
    # regime, where the true answer IS the sum), and refuse to paper over a gap
    # too large to be estimator noise.
    gap = sum_kw - pooled
    scale = np.maximum(np.abs(sum_kw), 1.0)
    material = gap / scale > _SUM_FLOOR_MATERIAL_REL
    if bool(material.any()):
        worst = int(np.argmax(gap / scale))
        raise MarketError(
            f"pool(method={method!r}) produced {pooled[worst]:.1f} kW at {ts[worst]}, "
            f"{gap[worst] / scale[worst]:.1%} BELOW the naive sum of the per-site quantiles "
            f"({sum_kw[worst]:.1f} kW). A gap that large is not estimator noise: it is either "
            "an aggregation bug or genuine quantile non-subadditivity (a strongly left-skewed "
            "marginal, where the contract's pool >= sum guarantee does not actually hold). "
            "Either way it is refused rather than floored away silently, because floating it "
            "up to the sum would sell a promise the aggregation says is not deliverable."
        )
    bind = gap > 0.0
    out["pool_firm_kw"] = np.maximum(pooled, sum_kw)
    out.attrs["sum_floor_bind_rate"] = float(bind.mean()) if n_t else 0.0
    out.attrs["sum_floor_max_gap_kw"] = float(gap[bind].max()) if bool(bind.any()) else 0.0
    out.attrs["draw_clip_rate"] = float(n_clipped / n_cells) if n_cells else 0.0
    out.attrs["diversification_benefit_kw_mean"] = float(
        np.mean(out["pool_firm_kw"].to_numpy() - sum_kw)
    ) if n_t else 0.0
    out.attrs["n_draws"] = n_draws
    return out


def correlation_structure(load: pd.DataFrame, sites: pd.DataFrame, *,
                          weather: pd.DataFrame | None = None) -> pd.DataFrame:
    """site_a, site_b, distance_km, correlation (+ per-regime columns).

    Pairwise correlation of *residuals* -- load minus each site's own
    time-of-day profile -- against great-circle distance from `sites`' lat/lon,
    plus the fitted exponential decay length in `.attrs['decay_length_km']`.

    Regimes where correlation runs toward 1 are flagged in
    `.attrs['regime_flags']`: cold snaps (needs `weather` with `temp_c`),
    German public holidays, and peak-load intervals. These are exactly the
    hours the grid needs the pool and exactly the hours the pool is weakest, so
    the flag is a deliverable, not a caveat: feed
    `.attrs['correlation_matrix_by_regime'][regime]` back into `pool` to see
    the pooled promise fall.

    `.attrs` also carries `correlation_matrix` (baseline),
    `pseudo_observations` (the joint ranks `pool(method='empirical')` consumes)
    and `residual_degenerate_bucket_rate`.
    """
    _require_columns(load, ["t", "site_id", "load_kw"])
    _require_columns(sites, ["site_id", "lat", "lon"])
    if load["load_kw"].isna().any():
        raise MarketError(
            "correlation_structure(): NaN load_kw; a missing reading must not be pairwise-"
            "dropped into a correlation estimated on a different sample per pair"
        )

    wide = load.pivot(index="t", columns="site_id", values="load_kw").sort_index()
    wide.columns = [str(c) for c in wide.columns]
    site_ids = sorted(wide.columns)
    wide = wide[site_ids]
    if len(site_ids) < 2:
        raise MarketError("correlation_structure() needs at least two sites")
    if wide.isna().any().any():
        raise MarketError(
            "correlation_structure(): the load panel is ragged (a site is missing rows some "
            "other site has); align it before measuring correlation"
        )

    resid, degenerate_rate = _time_of_day_residuals(wide)
    if degenerate_rate >= 1.0:
        raise MarketError(
            "correlation_structure(): every time-of-day bucket was seen exactly once, so all "
            "residuals are identically zero and no correlation is measurable. Supply more "
            "than one day of load."
        )
    base_corr = _correlation_from_residuals(resid)

    idx = pd.DatetimeIndex(wide.index)
    berlin_dates = idx.tz_convert("Europe/Berlin").date
    years = {d.year for d in berlin_dates}
    holidays: set = set()
    for y in years:
        holidays |= _german_public_holidays(y)

    regimes: dict[str, np.ndarray] = {}
    unavailable: dict[str, str] = {}

    regimes["holiday"] = np.array([d in holidays for d in berlin_dates])

    total = wide.sum(axis=1)
    peak_pct = ASSUMPTIONS["peak_load_regime_percentile"].value
    regimes["peak_load"] = (total >= total.quantile(peak_pct)).to_numpy()

    if weather is None:
        unavailable["cold_snap"] = "no weather frame supplied (needs t, temp_c)"
    else:
        _require_columns(weather, ["t", "temp_c"])
        temp = weather.set_index("t")["temp_c"].reindex(idx)
        if temp.isna().any():
            raise MarketError(
                "correlation_structure(): the weather frame does not cover every load "
                "timestamp; a missing temperature must not be treated as 'not cold'"
            )
        cold_pct = ASSUMPTIONS["cold_snap_temp_percentile"].value
        regimes["cold_snap"] = (temp <= temp.quantile(cold_pct)).to_numpy()

    regime_corr: dict[str, pd.DataFrame] = {}
    for name, mask in regimes.items():
        if int(mask.sum()) < _MIN_REGIME_ROWS:
            unavailable[name] = f"only {int(mask.sum())} interval(s) in regime (need {_MIN_REGIME_ROWS})"
            continue
        sub_resid, _ = _time_of_day_residuals(wide.loc[mask])
        try:
            regime_corr[name] = _correlation_from_residuals(sub_resid)
        except MarketError as exc:
            unavailable[name] = str(exc).split("\n")[0]

    site_loc = sites.drop_duplicates("site_id").set_index("site_id")
    missing_loc = [s for s in site_ids if s not in site_loc.index]
    if missing_loc:
        raise MarketError(f"correlation_structure(): no lat/lon for site(s) {missing_loc}")

    rows = []
    for i, a in enumerate(site_ids):
        for b in site_ids[i + 1:]:
            row = {
                "site_a": a,
                "site_b": b,
                "distance_km": _haversine_km(
                    float(site_loc.loc[a, "lat"]), float(site_loc.loc[a, "lon"]),
                    float(site_loc.loc[b, "lat"]), float(site_loc.loc[b, "lon"]),
                ),
                "correlation": float(base_corr.loc[a, b]),
            }
            for name, cm in regime_corr.items():
                row[f"correlation_{name}"] = float(cm.loc[a, b])
                row[f"{name}_increase"] = float(cm.loc[a, b]) - row["correlation"]
            rows.append(row)
    out = pd.DataFrame(rows)

    # exponential decay fit: corr ~ exp(-d / L)
    fit = out[(out["correlation"] > 1e-6) & np.isfinite(out["distance_km"])]
    if len(fit) >= 2 and fit["distance_km"].nunique() > 1:
        slope, _intercept = np.polyfit(fit["distance_km"].to_numpy(),
                                       np.log(fit["correlation"].to_numpy()), 1)
        decay_km = float(-1.0 / slope) if slope < 0 else float("inf")
    else:
        decay_km = float("nan")

    off = ~np.eye(len(site_ids), dtype=bool)
    baseline_mean = float(base_corr.to_numpy()[off].mean())
    threshold = ASSUMPTIONS["correlation_regime_flag_threshold"].value
    flags = {}
    for name, cm in regime_corr.items():
        mean_corr = float(cm.to_numpy()[off].mean())
        flags[name] = {
            "mean_correlation": mean_corr,
            "baseline_mean_correlation": baseline_mean,
            "increase": mean_corr - baseline_mean,
            "n_intervals": int(regimes[name].sum()),
            "flagged": bool(mean_corr >= threshold and mean_corr > baseline_mean),
        }

    out.attrs["decay_length_km"] = decay_km
    out.attrs["decay_fit_pairs"] = int(len(fit))
    out.attrs["correlation_matrix"] = _matrix_to_mapping(base_corr)
    out.attrs["correlation_matrix_by_regime"] = {
        name: _matrix_to_mapping(cm) for name, cm in regime_corr.items()
    }
    out.attrs["pseudo_observations"] = {
        s: [float(v) for v in _pseudo_observations(resid)[s].to_numpy()] for s in site_ids
    }
    out.attrs["baseline_mean_correlation"] = baseline_mean
    out.attrs["regime_flags"] = flags
    out.attrs["regimes_unavailable"] = unavailable
    out.attrs["residual_degenerate_bucket_rate"] = degenerate_rate
    out.attrs["assumptions_used"] = [
        "peak_load_regime_percentile",
        "correlation_regime_flag_threshold",
    ] + (["cold_snap_temp_percentile"] if weather is not None else [])
    return out


def diversification_curve(firm: pd.DataFrame, *, sizes: Sequence[int], seed: int,
                          realised: pd.DataFrame | None = None,
                          correlation: pd.DataFrame | None = None,
                          method: Literal["empirical", "gaussian_copula"] = "gaussian_copula",
                          ) -> pd.DataFrame:
    """n_sites, firm_kw_per_site, shortfall_rate -- the curve src/ui animates.

    For each portfolio size, `_CURVE_REPLICATES` seeded random subsets of that
    many sites are pooled; `firm_kw_per_site` is the mean pooled promise
    divided by the size. It rises with `n_sites` exactly to the extent the
    sites are imperfectly correlated -- that rise IS the claim.

    `shortfall_rate` is measured, not asserted: `realised` (t, site_id,
    realised_kw -- the reduction the sites actually had available) is required,
    and the rate is the fraction of intervals where the subset's realised total
    fell below the promise made for it. Simulating the shortfall from the same
    copula that produced the promise would guarantee ~tau by construction and
    prove nothing, which is why `realised` has no default.

    Monotonicity is reported, never enforced: `.attrs['monotone_in_n_sites']`
    and `.attrs['monotonicity_violations']` say whether the curve actually
    rises, so a portfolio that does not diversify says so instead of being
    smoothed into the claim.
    """
    _require_columns(firm, ["t", "site_id", "firm_kw"])
    if realised is None:
        raise MarketError(
            "diversification_curve() requires `realised` (t, site_id, realised_kw): "
            "shortfall_rate must be measured against realised load. Deriving it from the "
            "same distribution that produced the promise would make it ~tau by construction "
            "-- a number that cannot move is not evidence."
        )
    _require_columns(realised, ["t", "site_id", "realised_kw"])
    if realised["realised_kw"].isna().any():
        raise MarketError("diversification_curve(): NaN realised_kw; a missing realisation "
                          "must not be scored as either a hit or a shortfall")

    site_ids = sorted(str(s) for s in firm["site_id"].unique())
    n_avail = len(site_ids)
    wanted = [int(s) for s in sizes]
    bad = [s for s in wanted if s < 1 or s > n_avail]
    if bad:
        raise MarketError(
            f"diversification_curve(): size(s) {bad} are outside 1..{n_avail} available sites; "
            "silently truncating a requested size would plot a point the portfolio cannot "
            "support under a label saying it can"
        )

    realised_wide = realised.pivot(index="t", columns="site_id", values="realised_kw").sort_index()
    realised_wide.columns = [str(c) for c in realised_wide.columns]

    rng = np.random.default_rng(seed)
    rows = []
    n_shortfall_intervals = 0
    for n in wanted:
        per_site = []
        shortfalls = []
        for rep in range(_CURVE_REPLICATES):
            subset = sorted(rng.choice(site_ids, size=n, replace=False).tolist())
            pooled = pool(firm, method=method, sites=subset, seed=seed + rep,
                          correlation=correlation, n_draws=_CURVE_DRAWS)
            promise = pooled.set_index("t")["pool_firm_kw"]
            per_site.append(float(promise.mean()) / n)

            missing = [s for s in subset if s not in realised_wide.columns]
            if missing:
                raise MarketError(
                    f"diversification_curve(): no realised_kw for site(s) {missing}; a promise "
                    "with no realisation to check it against cannot be scored"
                )
            actual = realised_wide[subset].sum(axis=1).reindex(promise.index)
            if actual.isna().any():
                raise MarketError(
                    "diversification_curve(): realised_kw does not cover every timestamp the "
                    "promise was made for; an unscored interval must not count as a hit"
                )
            short = (actual.to_numpy() < promise.to_numpy() - 1e-9)
            n_shortfall_intervals += int(short.sum())
            shortfalls.append(float(short.mean()))
        rows.append({
            "n_sites": n,
            "firm_kw_per_site": float(np.mean(per_site)),
            "shortfall_rate": float(np.mean(shortfalls)),
        })

    out = pd.DataFrame(rows, columns=["n_sites", "firm_kw_per_site", "shortfall_rate"])
    ordered = out.sort_values("n_sites")
    diffs = np.diff(ordered["firm_kw_per_site"].to_numpy())
    violations = [
        (int(ordered["n_sites"].iloc[i]), int(ordered["n_sites"].iloc[i + 1]), float(d))
        for i, d in enumerate(diffs) if d < -1e-9
    ]
    out.attrs["method"] = method
    out.attrs["seed"] = seed
    out.attrs["replicates"] = _CURVE_REPLICATES
    out.attrs["n_draws"] = _CURVE_DRAWS
    out.attrs["monotone_in_n_sites"] = bool(not violations)
    out.attrs["monotonicity_violations"] = violations
    out.attrs["shortfall_intervals_observed"] = n_shortfall_intervals
    out.attrs["tau"] = float(firm.attrs.get("tau", 0.05))
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
