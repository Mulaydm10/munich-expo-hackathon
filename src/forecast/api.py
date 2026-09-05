"""src.forecast — public API.

Quantile load forecasting with calibration as the headline metric.

This module is the lane's ONLY cross-lane surface: other lanes import
`src.forecast.api` and nothing else. See `contracts/src/forecast.md` for the
interface this lane owes the rest of the project, and
`contracts/CONVENTIONS.md` for units, time handling and the data layout.

Nothing is implemented yet. Add functions from the contract; do not widen the
surface beyond it without a design PR.
"""

LANE = "src/forecast"
