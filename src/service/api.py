"""src.service — public API.

FastAPI scenario service; the only surface the UI and voice layers read.

This module is the lane's ONLY cross-lane surface: other lanes import
`src.service.api` and nothing else. See `contracts/src/service.md` for the
interface this lane owes the rest of the project, and
`contracts/CONVENTIONS.md` for units, time handling and the data layout.

Nothing is implemented yet. Add functions from the contract; do not widen the
surface beyond it without a design PR.
"""

LANE = "src/service"
