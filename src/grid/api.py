"""src.grid — public API.

Transformer thermal + three-phase feasible power envelope per site.

This module is the lane's ONLY cross-lane surface: other lanes import
`src.grid.api` and nothing else. See `contracts/src/grid.md` for the
interface this lane owes the rest of the project, and
`contracts/CONVENTIONS.md` for units, time handling and the data layout.

Nothing is implemented yet. Add functions from the contract; do not widen the
surface beyond it without a design PR.
"""

LANE = "src/grid"
