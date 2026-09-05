# src/market — contract

Converts forecast quantiles into a **sellable promise**, pools promises across sites so the pool is
worth more than its parts, and settles the result in euros, penalties and CO₂.

This lane holds the project's central claim: pooling manufactures reliability out of randomness, so
the safe promise per site *rises* with portfolio size. If that curve is wrong, the pitch is wrong.

Read `contracts/CONVENTIONS.md` first.

## Public API — `src/market/api.py`

```python
ASSUMPTIONS: dict[str, Assumption]   # every non-derived number the project quotes, with source + units
PRODUCTS: dict[str, Product]         # aFRR / mFRR: block length, min bid MW, notice period, penalty rule

def firm_capacity(quantiles: pd.DataFrame, *, tau: float = 0.05, floor_kw: float = 0.0) -> pd.DataFrame
    """t, site_id, firm_kw — reduction that is deliverable with probability >= 1 - tau. Uses the
    lower quantile, never the median. `floor_kw` is the load the scheduler commits to hold."""

def pool(firm: pd.DataFrame, *, method: Literal["sum", "empirical", "gaussian_copula"],
         sites: Sequence[str] | None = None, seed: int = 0) -> pd.DataFrame
    """t, pool_firm_kw. `sum` is the naive lower bound (each site's own worst case, added up);
    `empirical`/`gaussian_copula` aggregate the *distributions* and take the pool's own 5th
    percentile. The gap between them is the diversification benefit and the headline result."""

def correlation_structure(load: pd.DataFrame, sites: pd.DataFrame) -> pd.DataFrame
    """Pairwise residual correlation vs great-circle distance, plus the fitted decay length.
    Also flags the regimes where correlation -> 1 (holidays, cold snaps): exactly when the grid
    needs the pool most and it is weakest. That finding is a deliverable, not a caveat."""

def diversification_curve(firm: pd.DataFrame, *, sizes: Sequence[int], seed: int) -> pd.DataFrame
    """n_sites, firm_kw_per_site, shortfall_rate — the curve src/ui animates."""

def bid(pool_firm: pd.DataFrame, product: str, prices: pd.DataFrame) -> pd.DataFrame
    """block_start, block_end, capacity_kw, expected_revenue_eur. Rounds down to the product's
    granularity and refuses blocks under its minimum size."""

def settle(bids: pd.DataFrame, delivered: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame
    """block, capacity_kw, delivered_kw, capacity_revenue_eur, energy_cost_eur, penalty_eur, net_eur.
    Penalty rule comes from PRODUCTS, is cited, and is applied on every shortfall — a settlement
    that cannot go negative is not a settlement."""

def energy_cost(load_kw: pd.DataFrame, prices: pd.DataFrame) -> float
def co2(load_kw: pd.DataFrame, carbon: pd.DataFrame) -> float          # kg
def peakers_displaced(pool_firm_mw: float) -> tuple[float, str]        # (count, the assumption used)
```

## Guarantees
- `pool(method="empirical") >= pool(method="sum")` for every `t` (diversification cannot be
  negative); a violation is a bug and the test asserts it.
- `settle` on a portfolio that always over-delivers yields zero penalty; on one that never delivers
  yields negative `net_eur`. Both are tested.
- Every euro, kg and gas-plant count returned by this lane names its `ASSUMPTIONS` key. A number
  with no key cannot be returned — `Assumption` is required at construction.
- No network access, no reading of raw files: consumes frames from `src/data.load` and quantiles
  from `src/forecast`.

## Explicitly not this lane's job
Producing the quantiles (`src/forecast`), holding the physical floor (`src/sched`), knowing whether
the connection can carry it (`src/grid`).
