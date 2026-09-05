"""src.market — public API.

Firm capacity, portfolio pooling, bidding and settlement in euros and CO2.

This module is the lane's ONLY cross-lane surface: other lanes import
`src.market.api` and nothing else. See `contracts/src/market.md` for the
interface this lane owes the rest of the project, and
`contracts/CONVENTIONS.md` for units, time handling and the data layout.

Nothing is implemented yet. Add functions from the contract; do not widen the
surface beyond it without a design PR.
"""

LANE = "src/market"
