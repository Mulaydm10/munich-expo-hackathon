# STATE — live snapshot

**This file is overwritten every session, not appended to.** If `STATE.md` and `worklog.md`
disagree about what is true *now*, **STATE wins** — the worklog only explains how we got here.

Note for bus workers: this is the *project* snapshot. The bus's lane/claim state lives in
`docs/STATE.md` and is written only under the `claim/state` lock. Two different files, on purpose.

Last updated: 2026-09-06 evening (mac worker; design offline since 2026-09-05T20:44Z)

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
- **Nothing is in flight.** No open PRs, no running agents, no unmerged branches. `main` is
  `981d6f5` and `pytest tests -q` is **614 passed, 1 xfailed** (verified 2026-09-06 evening).

## What is true now
- **All nine lanes are merged and the app runs.** `src/service` owns the FastAPI app; before
  2026-09-06 there was no runnable application at all. All routes in `contracts/src/service.md`
  are live.
- **Cross-lane defects are now caught automatically.** `tests/integration/` (#38) runs on
  **every PR regardless of lane**, via a CI job that deliberately does not depend on lane
  resolution. It covers the pipeline seam through the real unstubbed `src/market`, the
  `src/ui` <-> `src/service` contract, and dead context keys. This exists because per-lane CI
  structurally could not see between lanes: `src/service` and `src/market` were each green and
  jointly broken, and merging #33 took 58 of 105 service tests to a 500 with no CI run ever
  being wrong.
- **`ReductionEvent` is finally specified** in `contracts/src/sched.md` (#52). It had been a
  documented GUESS that three lanes were built on.

## The data situation — read before writing any demo
`data/raw/` is **EMPTY**. The only real data in this repo is one Bundesnetzagentur charge-point
registry excerpt. Every time series is a hand-authored fixture stamped `# SYNTHETIC FIXTURE`,
and `fetch()` still raises `NotImplementedError` for every source. **The engine is real; its
inputs are invented.** That is fine mid-build and must never be shown to a judge as measured
German grid data.

Two different situations, do not conflate them:
- **Charging sessions are synthetic by necessity.** Nobody publishes them; the organizers hand
  out a sample only at the on-site briefing (`Q-0007`). This is standard practice, not a gap.
- **Prices, load, weather and carbon are synthetic by gap** — and that gap is closable. SMARD's
  `chart_data` API is confirmed live and needs **no key**. This is the highest-value next move.

`capacity_revenue_eur` is priced off a **synthetic balancing table** (decided #51: no
regelleistung.net account, no ENTSO-E token). That is the revenue line the pitch leads with, so
`DEMO-0001` must say so rather than let a judge assume it is measured.

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
**Highest value: wire `fetch()` for SMARD.** No credential needed. It turns the pitch from
"here is our model" into "here is Munich on a real day, with real prices, and here is what the
flexibility was worth" — same code, far stronger claim. **The five parser gaps in #50 must be
fixed as part of that work, not after it**: the parsers currently accept the synthetic fixture
shape only (thousands separators, DWD column names, invented station ids, and SMARD headers that
carry a resolution qualifier after the unit), so the first real file would parse to nothing,
silently.

Then, in order:
1. **#43** (p0, `src/ui`) — the operator-adjust form is dead in production: `reduction_event_input`
   is read at `src/ui/api.py:651` and produced nowhere in `src/`. The integration tier's strict
   `xfail` points straight at it. One of its fixes is cross-lane and needs a `src/service` route.
2. **`DEMO-0001`** — still no scenario. No longer blocked; write it **and actually run it**, and
   state plainly which figures are synthetic.
3. **#48** service follow-ups, **#44** market input-validation (filed unverified — verify before
   fixing), **#31** voice, then p2s #35 / #11 / #29.

## Working notes for whoever picks this up
- **Python is `/Users/mulaydm10/Dhruv/.venv-munich/bin/python` (3.12).** System `python3` is 3.14
  and pyarrow has no wheel for it.
- **Wait for the review bot before merging.** It posts a few minutes after a PR opens and has
  been right about real defects that both the falsification sweeps and my own probes missed —
  a NaN that authorised a dispatch, and a form with no producer.
- **`Closes #n` really does auto-close.** It closed #50 while scope remained.
- **A probe must reproduce the real environment.** A browser check of a canvas hit-test used a
  border-less canvas; the real CSS sets `border: 2px solid`, which was exactly where the
  remaining bug was. The probe passed and the page was still wrong.
- **Verify a cross-lane seam by building a scratch worktree holding both lanes** and running one
  lane's suite against the other's real code. `tests/integration/` now automates the known cases,
  but a new seam still needs this by hand first.

Human-only, still outstanding: read the two LOCKED files (#5 landed without your sign-off), decide
`Q-0003`/`Q-0004`, and act on **`Q-0008`** — no attendee ticket is held, and that one loses the
submission outright regardless of the build.

## Latest experiment
- (none yet — see `experiments/experiment_log.md`)

## Work-claims table
Claim a row before starting work on a surface; release it (delete the row, or mark Released) the
moment you stop — a stale claim blocks others worse than no claim at all.

| Claimed by | Surface | Claimed at | Status |
|---|---|---|---|
| devin-ai-integration[bot] (design) | governance + contracts + docs (`bot/join-real-lane-split`) | 2026-09-05 | Released — merged as `6518d017` |
| devin-ai-integration[bot] (design) | post-merge state + date correction (`design/…-post-merge-state`) | 2026-09-05 | Released on PR open |
