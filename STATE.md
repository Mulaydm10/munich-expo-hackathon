# STATE — live snapshot

**This file is overwritten every session, not appended to.** If `STATE.md` and `worklog.md`
disagree about what is true *now*, **STATE wins** — the worklog only explains how we got here.

Note for bus workers: this is the *project* snapshot. The bus's lane/claim state lives in
`docs/STATE.md` and is written only under the `claim/state` lock. Two different files, on purpose.

Last updated: 2026-09-20 early morning (mac worker; the design node is ACTIVE again and has been
shipping steadily since 2026-09-18 — #65, #68, #70, #72, #74, #76, #78, #79)

## Deadline + time remaining
**UNKNOWN — the deadline has MOVED and the new one is not recorded anywhere yet.**

On 2026-09-20 Dhruv said the deadline changed and that there is no longer time pressure. He did not
state the new date, and nobody has asked him for it. So:

- `COMPETITION.md` still says **Sun 20 Sep 2026, 17:00 CEST**, and #65 landed the note that Devpost
  stated that explicitly. **Both are now STALE.** Do not plan against either.
- **Ask Dhruv for the new date before doing anything that depends on it**, and do not infer one
  from a Devpost page, a countdown or this file. `CLAUDE.md`'s hard rule applies: never invent an
  event fact — a wrong assumed deadline is exactly the class of error that loses a submission.
- `Q-0003` is therefore **re-opened in substance**, whatever its recorded status says.

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
`main` is `264ad1e`; `pytest tests -q` is **687 passed, 1 xfailed** (verified 2026-09-20, venv
python — see Working notes).

- **Nothing is in flight.** Every PR that was open has been merged or closed. The only open PR is
  **#4** (`claim/1`), the standing canary — **never merge it**; the protocol depends on it staying
  open.
- **CI CANNOT RUN AT ALL, repo-wide.** Every GitHub Actions job since 2026-09-18 fails in 3–4s with
  zero steps executed. The annotation is explicit: *"The job was not started because recent account
  payments have failed or your spending limit needs to be increased."* This is an account billing
  problem, not a code or workflow problem, and it is fixed only at
  <https://github.com/settings/billing>. **Until it is fixed, every merge is unverified by CI** —
  run `pytest tests -q` locally and say so in the PR.

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
- **2026-09-18 session:** the three green open PRs were merged to `main` — #61 (`claim/48`, seven
  `src/service` follow-ups), #62 (`claim/11`, forecast rolling-origin) and #64
  (`design/worktree-layout`). Each merged pinned to its reviewed head sha. Suite went 619 -> 642.
- **Dependencies refreshed inside the declared ranges** (numpy 2.5.3, scikit-learn 1.9.1,
  uvicorn 0.53.0); suite unchanged at 642. `requirements-dev.txt` itself was **not** edited — it is
  canary-gated. pytest 9, pandas 3 and pyarrow 25 are all **outside** its ranges and would each need
  a design PR plus pre- and post-merge canaries; none was attempted two days out.
- **2026-09-19/20 session: eleven PRs merged, all pinned with `--match-head-commit`.** #61, #62,
  #64 (earlier), then **#65** (`docs/HANDOFF.md`), **#68** (live SMARD + DWD fetch, +10.7k lines),
  **#70** (envelope grouping: `_build_envelope` was O(sites x rows) and 10k sites never finished),
  **#72** (demand-charge peak term in the schedule LP), **#74** (portfolio-coordinated scheduling
  + portfolio forecast), **#76**, **#78** (portfolio forecast split to sites by slot share) and
  **#79** (cinematic landing + simulator UI on live data). Suite 619 -> **687**.
- **#76 was merged while knowingly red** and briefly left `main` with 2 failing service tests
  (`test_dispatch`, `test_warnings`). #74's own version of the same energy clamp fixed them; `main`
  has been green since. Recorded because a merged PR here is not evidence of a green one.
- **#66 merged** — #43 findings 2, 3 and 4 (`src/ui`). #67 was **closed as superseded**: #68 had
  already carried the same `_decimal_comma_to_float` fix, verified patch-equivalent with
  `git cherry`.
- **No PR in this run had a qualifying human reviewer.** The protocol says nobody self-merges and
  an automated reviewer does not qualify; these were merged on Dhruv's explicit instruction. Do not
  read "merged" as "reviewed".
- **`ReductionEvent` is finally specified** in `contracts/src/sched.md` (#52). It had been a
  documented GUESS that three lanes were built on.

## The data situation — read before writing any demo
**This changed materially on 2026-09-18 and the old warning no longer applies verbatim.**

`fetch()` is now **wired for all five sources** — `charge_points`, `smard_load`, `epex_day_ahead`,
`generation_mix`, `dwd_weather` (`_FETCH_WIRED`, #68). The parsers were rewritten against real
export shapes, and `tests/src/data/fixtures/` now holds genuine `smard_real_*.csv` and
`dwd_real_*` files alongside the synthetic ones.

**But `data/raw/` is still EMPTY — nobody has actually run `fetch()`.** So at this instant every
number the app can show is still fixture-derived. The difference from before is that the gap is now
one command wide rather than a body of unwritten code:

```sh
python3 -c "from datetime import date; from src.data import api; api.fetch('smard_load', start=date(...), end=date(...))"
```

**Run it, then re-check the demo figures.** Until someone does, do not describe any figure as
measured German grid data.

Still true, and unchanged:
- **Charging sessions are synthetic by necessity.** Nobody publishes them; the organizers hand out
  a sample only at the on-site briefing (`Q-0007`). Standard practice, not a gap.
- `capacity_revenue_eur` is priced off a **synthetic balancing table** (decided #51: no
  regelleistung.net account, no ENTSO-E token). That is the revenue line the pitch leads with, so
  any demo script must say so rather than let a judge assume it is measured.

## Blocked

- **`Q-0003` (exact deadline) is live again** — Dhruv says the date moved and has not said to what.
  Ask him; do not infer it. `Q-0004` (solo vs 2–6 team) still needs an organizer answer. No
  organizer contact may be made without the Main Agent's explicit authorization.
- Organizers' sample charging dataset is only handed out at the on-site briefing (`Q-0007`). Nothing
  in the build depends on it, by design.
- **`Q-0008`: participation mode is unset and no attendee ticket is held.** Human-only, and unlike
  the other open questions it is an action rather than an answer — tickets can sell out, so its
  real deadline is unknown. Nothing in the build depends on it; the right to submit does. (The
  "earlier than the 20th" framing this bullet used to carry is void — see Deadline above.)

## Next intended step
**Run `fetch()` and put real German data through the engine.** The code is wired (#68); nobody has
executed it, so `data/raw/` is empty. This is a command and a verification pass, not a build — and
it is still the single highest-value move, because it turns "here is our model" into "here is
Munich on a real day, with real prices".

Then, in order:
1. **Confirm the new deadline with Dhruv** before sequencing anything else.
2. **`DEMO-0001`** — still no scenario written. Write it **and actually run it**, stating plainly
   which figures are synthetic. `CLAUDE.md` makes a broken demo outrank new features.
3. **#43 finding 1** (the repo's one `xfail`) — `reduction_event_input` is read at `src/ui/api.py`
   and produced nowhere in `src/`. **This is not a bug fix.**
   `tests/integration/test_no_dead_context_keys.py` records that `src/service` never called
   `src.ui.render()` from any HTTP route, so there was no HTML glue to hang the route on. #79 has
   since added a served UI layer, so **re-check whether that premise still holds** before either
   fixing it or re-justifying the `xfail`.
4. **#50 items 2b/2c** — DWD product schema and the invented `DWD-BER` station ids. #68 rewrote
   much of this lane, so **re-verify what is still outstanding** rather than trusting the issue text.
5. **#44** market input-validation — filed explicitly **unverified**; verify each of the six
   findings before fixing. Then **#31** (`src/voice`, still a stub), then p2s **#35** / **#29**.
6. **Release the six stale claim refs** (below).

## Working notes for whoever picks this up
- **CI is dead until the GitHub bill is paid** (see In flight). Verify locally; a PR with no green
  tick right now means nothing was run, not that something failed.
- **Six claim refs are still held on merged PRs**: `claim/50`, `claim/69`, `claim/71`, `claim/73`,
  `claim/75`, `claim/77` — all fully contained in `main`. Release them by RENAME to
  `merged/<n>-<sha8>`; `AGENTS.md` renames claim refs and never deletes them. Leave `claim/1`
  (canary) and `claim/state` (lock) alone. **Never delete the `origin/merged/*` refs** — they are
  protocol state the bus rebuilds from, not clutter. A previous session nearly deleted them as
  tidy-up.
- **Claim worktrees live under `~/Dhruv/worktrees/munich-expo-hackathon/`** (#64). The thirteen
  stale ones from the 05–07 Sep build were removed on 2026-09-18; only `claim-1` (the standing
  canary) remains, which is correct. Merged local branches were deleted too — `claim/1` and
  `fix/43-...` (PR #66) are the only non-`main` locals left.
- **`merged/<n>-<sha8>` refs on origin are protocol state, not clutter — do not delete them.**
  `AGENTS.md` renames claim refs, never deletes them, and `merged/*` is the released form. The two
  refs still held on merged PRs (`claim/11`, `claim/48`) were released properly this session as
  `merged/11-dd690097` and `merged/48-6d8d6dde`. Only `claim/1` (canary) and `claim/state` (the
  state lock) remain as live `claim/*` refs, which is the correct steady state.
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
