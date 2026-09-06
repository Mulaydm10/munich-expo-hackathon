# STATE — live snapshot

**This file is overwritten every session, not appended to.** If `STATE.md` and `worklog.md`
disagree about what is true *now*, **STATE wins** — the worklog only explains how we got here.

Note for bus workers: this is the *project* snapshot. The bus's lane/claim state lives in
`docs/STATE.md` and is written only under the `claim/state` lock. Two different files, on purpose.

Last updated: 2026-09-06 (mac worker session; design node offline since 2026-09-05T20:44Z)

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
- Design PR #5 merged as `6518d017`, both canaries green (pre-merge #13 on the design tip,
  post-merge #4 on the merge commit with a real dependency install). Gate #6 closed, so the eight
  lane issues are claimable and the pinned Board is #16.

## In flight
- **Seven lanes are implemented and merged**, not zero: `src/data` (sites only), `src/fleet`,
  `src/forecast`, `src/grid`, `src/market` (base), `src/sched`, `src/ui`. `main` is at `46c7bbf`
  and `pytest tests -q` is **312 passed** (verified 2026-09-06). The line that used to sit here —
  "no lane has an implementation yet … 10 passed" — was written before any lane landed and was
  badly stale; treat this section, not the worklog, as current.
- **Two lanes are pushed but deliberately unmerged**, because their agents were killed by a
  session limit *during their final falsification sweep*: `claim/28` (`src/service`, 100 tests)
  and `claim/33` (`src/market` pooling, 115 tests). Both suites are green. Green was not the
  question — nothing had yet confirmed those tests can go red. The sweeps are being finished now;
  no PR is open until they are. Both branch from before the `src/ui` merges, so `git diff
  main..HEAD` appears to delete `src/ui`. It does not. **Do not rebase to "fix" that diff.**
- `src/ui` #40 is in flight: the call screen's dispatch button is gated on a `reduction_event`
  context key that **nothing in production produces** — the only supplier in the repo is a test
  fixture. 135 green tests over a permanently dead button, on the one demo screen that shows the
  product delivering flexibility. Being fixed on `claim/40`.
- Not started: `src/data` #8 (the time-series sources + both DST transitions). Queued p2/p1:
  #29, #35, #11, and #31 (`src/voice`, blocked on #28).

## Blocked

- `Q-0003` (exact deadline) and `Q-0004` (solo vs 2–6 team) need an organizer answer. No organizer
  contact may be made without the Main Agent's explicit authorization.
- Organizers' sample charging dataset is only handed out at the on-site briefing (`Q-0007`). Nothing
  in the build depends on it, by design.
- **`Q-0008`: participation mode is unset and no attendee ticket is held.** Human-only, and unlike
  the other open questions it is an action rather than an answer — tickets can sell out, so its
  real deadline is unknown and earlier than the 20th. Nothing in the build depends on it; the right
  to submit does.

## Next intended step
1. Finish the falsification sweeps on `claim/28` and `claim/33`, then merge both. Everything
   downstream of them is blocked: `src/service` owns the FastAPI app, so **on `main` today there is
   no runnable application at all** — `src/ui` is templates and static files only.
2. `src/ui` #40 (dead dispatch button) — in flight.
3. `DEMO.md` still has **no scenario** and still says "no build yet". It cannot get one until #28
   merges, for the reason in 1. Write `DEMO-0001` and *actually run it* before marking it Ready.
4. `src/data` #8, then the p2 queue.

**A fact worth knowing before writing the demo:** `data/raw/` is **empty** — there is no real
SMARD/DWD/registry data in this repo, only a single charge-point excerpt fixture
(`tests/src/data/fixtures/ladesaeulenregister_excerpt.csv`). Every number the demo shows will come
from authored fixtures or synthetic sessions until #8 lands real series. That is defensible for a
build in progress, but it must never be *presented* to a judge as measured German grid data, and
`DEMO-0001` should say which numbers are synthetic.

Human-only, still outstanding: read the two LOCKED files (#5 landed without your sign-off), decide
`Q-0003`/`Q-0004`, and act on `Q-0008` (no ticket held — this one can lose the submission outright).

## Latest experiment
- (none yet — see `experiments/experiment_log.md`)

## Work-claims table
Claim a row before starting work on a surface; release it (delete the row, or mark Released) the
moment you stop — a stale claim blocks others worse than no claim at all.

| Claimed by | Surface | Claimed at | Status |
|---|---|---|---|
| devin-ai-integration[bot] (design) | governance + contracts + docs (`bot/join-real-lane-split`) | 2026-09-05 | Released — merged as `6518d017` |
| devin-ai-integration[bot] (design) | post-merge state + date correction (`design/…-post-merge-state`) | 2026-09-05 | Released on PR open |
