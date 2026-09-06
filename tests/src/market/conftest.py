"""Portfolio builders for the src/market pooling tests (#33).

Every fixture here is built from *analytically known* marginals -- each site's
quantile column is `mu_s(t) + Phi^-1(tau) * sigma` -- so a test can state what
the pooled answer must be without asking the implementation. That matters more
here than anywhere else in the lane: the whole point of `pool` is that the
diversification benefit is a measurement, and a benefit checked against a
number the code chose would be unfalsifiable.

The dependence structure is built the same way: each site's shock is
`sqrt(rho) * common + sqrt(1 - rho) * idiosyncratic`, so `rho` is the designed
pairwise correlation and the test knows it before calling anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from src.market import api

# The forecast contract's quantile grid, restated here rather than imported:
# a lane may only import another lane's `api` module.
QUANTILE_TAUS = (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)

PERIODS_PER_DAY = 96  # 15-minute intervals, CONVENTIONS.md native resolution


def daily_shape(n_days: int, amplitude: float = 40.0) -> np.ndarray:
    """The same commuter-shaped time-of-day profile every site follows.

    Shared *deliberately*: `correlation_structure` must divide it out, and a
    fixture where it is absent could not tell a residual correlation from a
    raw-load one.
    """
    one_day = np.sin(np.arange(PERIODS_PER_DAY) / PERIODS_PER_DAY * 2 * np.pi) * amplitude
    return np.tile(one_day, n_days)


def utc_index(n_days: int, start: str = "2026-01-05T00:00:00Z") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=PERIODS_PER_DAY * n_days, freq="15min")


def quantile_frame(mu_by_site: dict[str, np.ndarray], sigma: float,
                   t: pd.DatetimeIndex) -> pd.DataFrame:
    """A `src/forecast`-shaped quantiles frame with known Normal marginals."""
    frames = []
    for site_id, mu in mu_by_site.items():
        cols: dict[str, object] = {"t": t, "site_id": [site_id] * len(t)}
        for tau in QUANTILE_TAUS:
            cols[api._quantile_col(tau)] = np.asarray(mu, dtype=float) + norm.ppf(tau) * sigma
        frames.append(pd.DataFrame(cols))
    return pd.concat(frames, ignore_index=True)


@dataclass
class Portfolio:
    t: pd.DatetimeIndex
    site_ids: list[str]
    mu: dict[str, np.ndarray]
    sigma: float
    quantiles: pd.DataFrame
    realised: pd.DataFrame
    firm: pd.DataFrame

    def sum_of_individual_quantiles_kw(self, sites: Sequence[str] | None = None) -> np.ndarray:
        """The naive lower bound, computed here from the per-site q05 columns.

        Deliberately NOT read back from `pool(method='sum')` or from any
        `.attrs` the implementation set: the diversification benefit is only
        evidence if its baseline comes from outside the code under test.
        """
        q = self.quantiles
        if sites is not None:
            q = q[q["site_id"].isin(list(sites))]
        # Clipped at zero because "a site cannot be sold as negative capacity"
        # is the contract, not an implementation detail of firm_capacity.
        per_site = q[["t", "site_id", "q05"]].copy()
        per_site["q05"] = per_site["q05"].clip(lower=0.0)
        return per_site.groupby("t")["q05"].sum().sort_index().to_numpy(dtype=float)


def factor_portfolio(n_sites: int, n_days: int, rho: float, seed: int,
                     base: float | Sequence[float] = 600.0, sigma: float = 50.0,
                     shock_kw: float = 40.0, shape_kw: float = 40.0,
                     start: str = "2026-01-05T00:00:00Z",
                     realised_noise: float | None = None) -> Portfolio:
    """`n_sites` sites whose day-to-day shocks have designed correlation `rho`.

    `realised` is drawn from each site's own true marginal, so a shortfall rate
    measured against it is a measurement of the promise, not a restatement of
    the model that made it.
    """
    rng = np.random.default_rng(seed)
    t = utc_index(n_days, start=start)
    n = len(t)
    shape = daily_shape(n_days, amplitude=shape_kw)
    common = rng.normal(0.0, 1.0, n)
    bases = [float(base)] * n_sites if np.isscalar(base) else [float(b) for b in base]
    site_ids = [f"s{i}" for i in range(n_sites)]

    mu: dict[str, np.ndarray] = {}
    realised_frames = []
    noise = sigma if realised_noise is None else realised_noise
    for i, site_id in enumerate(site_ids):
        idio = rng.normal(0.0, 1.0, n)
        shock = np.sqrt(rho) * common + np.sqrt(1.0 - rho) * idio
        mu[site_id] = bases[i] + shape + shock_kw * shock
        realised_frames.append(pd.DataFrame({
            "t": t,
            "site_id": site_id,
            "realised_kw": np.clip(mu[site_id] + rng.normal(0.0, noise, n), 0.0, None),
        }))

    quantiles = quantile_frame(mu, sigma, t)
    return Portfolio(
        t=t,
        site_ids=site_ids,
        mu=mu,
        sigma=sigma,
        quantiles=quantiles,
        realised=pd.concat(realised_frames, ignore_index=True),
        firm=api.firm_capacity(quantiles, tau=0.05),
    )


def load_frame(mu_by_site: dict[str, np.ndarray], t: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.concat(
        [pd.DataFrame({"t": t, "site_id": s, "load_kw": np.asarray(v, dtype=float)})
         for s, v in mu_by_site.items()],
        ignore_index=True,
    )


def site_table(site_ids: Sequence[str], lats: Sequence[float], lons: Sequence[float]) -> pd.DataFrame:
    return pd.DataFrame({"site_id": list(site_ids), "lat": list(lats), "lon": list(lons)})


def constant_matrix(site_ids: Sequence[str], rho: float) -> dict:
    """A nested {site: {site: rho}} mapping -- the shape `pool` accepts."""
    return {a: {b: (1.0 if a == b else rho) for b in site_ids} for a in site_ids}


@pytest.fixture(scope="module")
def diversified() -> Portfolio:
    """Six sites, designed pairwise correlation 0.3 -- the realistic case."""
    return factor_portfolio(n_sites=6, n_days=4, rho=0.3, seed=11)


@pytest.fixture(scope="module")
def comonotonic() -> Portfolio:
    """Five sites of very unequal size whose shocks are near-identical.

    The regime where pooling buys nothing: the honest curve here is flat, and a
    curve that rises anyway would be manufacturing the project's headline.
    """
    return factor_portfolio(n_sites=5, n_days=4, rho=0.995, seed=9,
                            base=[400.0, 500.0, 700.0, 1100.0, 1900.0])
