# Judging alignment

Maps each rubric criterion (from `COMPETITION.md`) to the concrete artifact in this repo that
satisfies it, and its status. This is what stops a team building something judges never actually
score. **A criterion with no row, or a row with no artifact, is a gap to close before submission.**

Weights are unpublished, and two criteria sets appear in official material (Devpost's five, plus the
EV challenge page's four). Both are treated as scored. If the real weighted rubric appears, copy the
criteria verbatim into `COMPETITION.md` first, then reconcile this table to it.

## Devpost criteria

| Criterion | Artifact in this repo | Status |
|---|---|---|
| Problem relevance & impact | `VISION.md` thesis + the national totals from `src/market.peakers_displaced` / `co2` — "how much flexibility already exists, unused, in Germany's installed charging estate" | Planned |
| Technical excellence & feasibility | calibrated quantile forecasts (`contracts/src/forecast.md`), transformer thermal + per-phase envelope (`grid`), LP scheduler with hard deadline feasibility (`sched`), portfolio pooling (`market`) — each with contract-level tests | Planned |
| Innovation & originality | the two non-obvious claims: bids sized off a **calibrated lower quantile**, and a **measured diversification curve** (safe promise per site rising with portfolio size). Price-based smart charging alone is explicitly *not* the claim (`VISION.md`) | Planned |
| Practical applicability & scalability | the same pipeline run for one Munich depot and for the whole registry; scaling is per-site decomposition, and the benefit per site *improves* with scale | Planned |
| Presentation & communication | `DEMO.md` (`DEMO-0001` onward), the map + playback UI, the 2–3 min video, and `/api/assumptions` so every number on screen is traceable | Planned |

## EV challenge-page criteria

| Criterion | Artifact in this repo | Status |
|---|---|---|
| Forecast quality | `backtest` on real historical dates reporting **coverage and sharpness together**, against seasonal-naive and climatological baselines; calibration is reported before point error | Planned |
| Practicality of the scheduling / pricing suggestion | `src/sched.evaluate` scorecard vs the uncontrolled baseline: euros, peak kW, zero deadline misses, zero envelope violations — plus a live dispatch during the demo | Planned |
| Technical execution | deterministic seeded runs, no network in tests, one runtime (`ADR-0002`), nine contracted lanes built in parallel under `AGENTS.md` | In progress |
| Demo quality | a real map of Germany's charge points, one real night played back, one slider for the pooling result, and a voice answer for the depot operator | Planned |

## Known gaps
- Rubric weights unknown → cannot prioritise between criteria. Do not guess them.
- `Q-0004`: if teams of 2–6 are mandatory, "Presentation & communication" needs a team, not just a
  demo. Unresolved.
- Every "Planned" row above is a claim with no artifact yet. Each becomes a lane issue, and this
  table's Status column is the honest tracker — flip a row to "Done" only when the artifact runs.
