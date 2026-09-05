# STATE — live snapshot

**This file is overwritten every session, not appended to.** If `STATE.md` and `worklog.md`
disagree about what is true *now*, **STATE wins** — the worklog only explains how we got here.

Note for bus workers: this is the *project* snapshot. The bus's lane/claim state lives in
`docs/STATE.md` and is written only under the `claim/state` lock. Two different files, on purpose.

Last updated: 2026-09-01 (design node session, kickoff)

## Deadline + time remaining
**Sun 20 Sep 2026, 17:00 Europe/Berlin (CEST)** — working assumption. A second official source
implies end of day on the 20th; the conflict is unresolved (`Q-0003`) and we plan against the
earlier one. See `COMPETITION.md`; do not restate the date anywhere else.

## Done
- Kickoff resolved the two blockers: `VISION.md` (thesis: FlexGrid) and `COMPETITION.md` (event
  facts, with two official contradictions preserved as `Q-0003`/`Q-0004`). `Q-0001` closed.
- `ADR-0002` Accepted — Python 3.12 only, FastAPI + Jinja2 + CDN front-end, no node toolchain.
  Canonical commands in `CLAUDE.md`; `Q-0002` closed.
- Test baseline exists: one green smoke test per lane, `python3 -m pytest tests -q` → 10 passed.
- Architecture split into nine real lanes with a design-owned contract each
  (`contracts/CONVENTIONS.md` + `contracts/src/<lane>.md`), replacing the `src/00_scaffold`
  placeholder. Data flow: data → fleet → forecast → grid/market/sched → service → ui/voice.

## In flight
- Design PR from `bot/join-real-lane-split` carrying everything above, for the human to merge.
  It changes `docs/verify.txt` + `requirements-dev.txt`, so it needs a worker-authored pre-merge
  canary and a post-merge canary commit (`AGENTS.md`) — requested from Claude on the canary issues.
- First `status:queued` lane issues for the upstream lanes (`src/data`, `src/fleet`, `src/forecast`,
  `src/grid`).

## Blocked
- `Q-0003` (exact deadline) and `Q-0004` (solo vs 2–6 team) need an organizer answer. No organizer
  contact may be made without the Main Agent's explicit authorization.
- Organizers' sample charging dataset is only handed out at the on-site briefing (`Q-0007`). Nothing
  in the build depends on it, by design.

## Next intended step
Merge the design PR (human), then keep the lane queue ≥2 deep per active lane while workers
implement upstream-first. First runnable end-to-end target is a single Munich site for one
historical date: real prices + real registry entry → synthetic sessions → quantile forecast →
envelope → schedule → euros. `DEMO.md` gets its first `DEMO-0001` scenario the moment that runs.

## Latest experiment
- (none yet — see `experiments/experiment_log.md`)

## Work-claims table
Claim a row before starting work on a surface; release it (delete the row, or mark Released) the
moment you stop — a stale claim blocks others worse than no claim at all.

| Claimed by | Surface | Claimed at | Status |
|---|---|---|---|
| devin-ai-integration[bot] (design) | governance + contracts + docs (`bot/join-real-lane-split`) | 2026-09-01 | Released on PR open — lane work is the workers' |
