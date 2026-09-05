"""src.ui — public API.

Judge-facing map and dashboard, served by src.service, no build step.

This module is the lane's ONLY cross-lane surface: other lanes import
`src.ui.api` and nothing else. See `contracts/src/ui.md` for the
interface this lane owes the rest of the project, and
`contracts/CONVENTIONS.md` for units, time handling and the data layout.

Nothing is implemented yet. Add functions from the contract; do not widen the
surface beyond it without a design PR.
"""

LANE = "src/ui"
