> **LOCKED governing file.** Do not edit in place. See `GOVERNANCE.md`.

# experiments/ — experiment / spike log

Any experiment, spike, or throwaway prototype gets an `EXP-####` ID (zero-padded, monotonically
increasing, never reused) and a row in `experiment_log.md`. This is separate from `worklog.md`:
worklog is narrative history of *sessions*, this is a structured record of *experiments specifically*
— what was tried, what was measured, what was concluded.

## Schema (one row per experiment in experiment_log.md)
`EXP-#### | Date | Hypothesis | Result | Artifact/log path | Status`

- **Artifact/log path** should point into `logs/<EXP-####>_<YYYY-MM-DD>_<slug>/` if the experiment
  produced logs/output worth keeping (see `logs/README.md`).
- **Status**: `Running | Done | Abandoned | Superseded by EXP-####`
- `experiment_log.md` itself is **append-only** — never edit or delete a past row; add a new one
  (with a "superseded by" or "correction to EXP-####" note) if a past entry turns out wrong.

## Why this exists even with no build yet
So the structure is in place the moment the first spike happens — no agent should have to invent an
experiment-logging convention mid-hackathon.
