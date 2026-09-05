"""diversification_curve(): the safe promise per site rises with portfolio size."""

import pandas as pd

from src.market import api


def _firm_frame(n_sites: int = 10, firm_kw: float = 100.0) -> pd.DataFrame:
    t = pd.Timestamp("2026-04-01T00:00:00Z")
    df = pd.DataFrame({
        "t": [t] * n_sites,
        "site_id": [f"site-{i}" for i in range(n_sites)],
        "firm_kw": [firm_kw] * n_sites,
    })
    df.attrs["tau"] = 0.05
    return df


def test_firm_kw_per_site_rises_with_portfolio_size():
    firm = _firm_frame(n_sites=12)
    out = api.diversification_curve(firm, sizes=[1, 4, 8, 12], seed=7)
    assert list(out["n_sites"]) == [1, 4, 8, 12]

    per_site_1 = out.loc[out.n_sites == 1, "firm_kw_per_site"].iloc[0]
    per_site_12 = out.loc[out.n_sites == 12, "firm_kw_per_site"].iloc[0]
    assert per_site_12 > per_site_1, (
        f"pooling thesis violated: per-site firm capacity did not rise with portfolio size "
        f"(n=1: {per_site_1}, n=12: {per_site_12})"
    )


def test_shortfall_rate_stays_near_tau():
    firm = _firm_frame(n_sites=8)
    out = api.diversification_curve(firm, sizes=[8], seed=3)
    rate = out["shortfall_rate"].iloc[0]
    assert 0.0 <= rate <= 0.15
