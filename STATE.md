# STATE — live snapshot

**This file is overwritten every session, not appended to.** If `STATE.md` and `worklog.md`
disagree about what is true *now*, **STATE wins** — the worklog only explains how we got here.

Note for bus workers: this is the *project* snapshot. The bus's lane/claim state lives in
`docs/STATE.md` and is written only under the `claim/state` lock. Two different files, on purpose.

Last updated: 2026-09-20 late afternoon (mac worker; the design node is ACTIVE again and has been
shipping steadily since 2026-09-18 — #65, #68, #70, #72, #74, #76, #78, #79)

**Two things changed today that invalidate older notes: real data is loaded (see The data
situation) and `main` is pushed to GitHub at `ab2dade`.**

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
`main` carries the `src/ui` design pass; `pytest tests -q` is **689 passed, 1 xfailed** (verified
2026-09-20 afternoon, venv python — see Working notes). The count moved from 687 because
`tests/src/ui/test_static_modules.py` parametrises over `static/*.js`, and the pass added two
modules.

- **`src/ui` design pass merged to main directly, not via PR** (2026-09-20, on Dhruv's explicit
  instruction). Landing and control room: GSAP 3.15.0 + ScrollTrigger **vendored** under
  `static/vendor/` with sha384 hashes in the vendor README — not npm, because `ADR-0002` and
  `contracts/src/ui.md` both rule out a node toolchain. All CSS is appended and scoped
  (`body.landing` / `body.control`); nothing existing was rewritten. Motion is never load-bearing
  (every animation is a `gsap.from()`, so resting CSS is the finished frame) and no motion module
  touches the *value* of a figure.
  - It fixed one real defect: the control room rendered an em-dash for a figure that was still
    loading AND for a figure with no value, so for the length of the boot those two states were
    indistinguishable. Figures now resolve independently with an 8s failsafe back to the em-dash.
  - Unchanged on purpose, against the design skills' advice: no web font, nothing below the
    three-metre projector floor, no network call beyond the API, and the em-dash placeholders
    (here a data glyph, not prose styling).
- **`main` is pushed and in sync: `origin/main` = `ab2dade`** (2026-09-20, on Dhruv's explicit
  instruction to "merge with github"). For a few hours the `src/ui` design pass existed only as
  three unpushed local commits with no PR and no remote — one disk, no backup. It is now on
  GitHub. Note this was a direct push to `main`, not the bus PR flow.
- **A server is running on the tailnet** at <http://100.80.210.100:8777> (landing) and `/simulator`,
  bound to the Tailscale interface so the Omen (`100.120.107.76`) can reach it and the local LAN
  cannot. Verified from the Omen. It serves out of the main checkout, so whatever is checked out
  there is what it shows.
- **Nothing else is in flight.** Every PR that was open has been merged or closed. The only open PR is
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
**This changed materially on 2026-09-20: `fetch()` HAS NOW BEEN RUN. Real German data is loaded.**
Every earlier warning that `data/raw/` is empty is void.

All five sources were downloaded live and canonicalised on 2026-09-20. `/api/health` reports
`"status":"ok"`:

| table | rows | window |
|---|---|---|
| `sites` | 75,582 | BNetzA charge-point registry (53 MB raw CSV) |
| `grid_load` | 6,144 | 2026-07-14T22:00Z → 2026-09-16T21:45Z |
| `prices` | 6,144 | same |
| `carbon` | 6,144 | same |
| `weather` | 316,168 | 2025-03-19 → 2026-09-19, 15 DWD stations |
| `balancing` | **null** | never wired — still synthetic, see below |

6,144 = 64 days x 96 intervals exactly, so the 15-minute grid is intact with no holes. The values
are plausible and were checked rather than assumed: German load 35.5–64.8 GW; carbon 352.2 g/kWh
mean (**not** the 182.8 the old mixed-header defect produced); 74 negative-price intervals survived
rather than being scrubbed, which is correct — negative day-ahead prices are valid data.

A real 200-site scenario for 2026-09-10 has been built end-to-end and is cached on disk:
**baseline peak 1467.4 kW → optimised 1305.2 kW**, zero deadline misses, zero envelope violations.

**`data/` is gitignored, so a fresh checkout or a new worktree has none of it.** Rebuild with:

```sh
python3 -c "
from datetime import date
from src.data import api
S, E = date(2026,7,15), date(2026,9,16)
for src in ['charge_points','smard_load','epex_day_ahead','generation_mix','dwd_weather']:
    api.fetch(src, start=S, end=E); api.canonicalise(src)
"
```

**The forecast needs at least 10 days of history before the scenario day.** A window starting
2026-09-01 fails a 2026-09-10 scenario with a 503 that names the shortfall exactly; that is why the
window above starts in mid-July. The error messages in this lane are good — read them, they name
the fix.

**What is still NOT real, and must be said out loud to a judge:**
- `capacity_revenue_eur`, `net_eur`, `pool_firm_mw` and `peakers_displaced` all come back **`null`**
  on a real run, because the `balancing` table was never built and pooling degraded. **The revenue
  line the pitch leads with currently shows nothing at all.** That is the biggest open gap now.
- Forecast accuracy on real data is **32.0%** (WAPE 0.68). It does beat both baselines
  (seasonal-naive 1.17, climatology 0.82) and coverage is well calibrated at 95.8% against a 90%
  target — but 32% is low, off 28 days of history and synthetic sessions. Do not quote it as a
  strength without the comparison beside it.
- The landing page's disclosure line ("grid load, weather, electricity prices and registered
  charging locations are real public data") **was false this morning and is true now.** It became
  true by running `fetch()`, not by editing the page.

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
**`DEMO-0001` — write a demo scenario and actually run it.** `fetch()` is DONE, so the old top
item is closed. The engine now has real German data behind it and a served UI in front of it; what
is missing is a scripted path a person can walk a judge through. `CLAUDE.md` makes a broken or
absent demo outrank new features.

Then, in order:
1. **Confirm the new deadline with Dhruv** before sequencing anything else. Still unknown.
2. **Decide what to do about the null revenue line.** `capacity_revenue_eur` is `null` on every
   real run because `balancing` was never built (#51: no regelleistung.net account). Either build a
   documented synthetic balancing table and label it as such in the UI, or remove the revenue claim
   from the pitch. Leaving a headline figure blank in front of a judge is the worst of the three.
3. **#43 finding 1 — RE-VERIFIED 2026-09-20, the premise still holds.** The question was whether
   #79's served UI layer invalidated it. It does not: `src/service` serves only `ui.render_page()`
   for the two standalone pages (`landing`, `simulator`), and **`ui.render()` still has zero callers
   anywhere in `src/service`**. The five context-driven Jinja screens (`map`, `day`, `call`,
   `pooling`, `ledger`) are orphaned — all five 404 on the live app, confirmed by probe. So the
   `xfail` is still correctly justified and `reduction_event_input` is still produced by nothing.
   **Decide: route those five screens, or delete them.** They are dead weight either way, and the
   #66 work on `default_reduction_event()` is currently unreachable from the running app.
4. **`app-simulator.js:208` has a dead ternary**: `{ phase: app.dispatchResult ? 'optimised' :
   'optimised' }` — both branches identical, should be `: 'baseline'`. Because `setLoad()` branches
   on `phase` (`app-scene.js:199-203`), the 3D twin looks **identical before and after a dispatch**
   in the simulator: the transformer light can never go amber and the chargers never reach the
   brighter baseline intensity. `app-story.js:140/145` does it correctly, which confirms the intent.
   Small fix, judge-visible effect.
5. **#50 items 2b/2c** — DWD product schema and the invented `DWD-BER` station ids. #68 rewrote much
   of this lane and real DWD files have now been parsed successfully, so **re-verify what is still
   outstanding** rather than trusting the issue text.
6. **#44** market input-validation — filed explicitly **unverified**; verify each of the six
   findings before fixing. Then **#31** (`src/voice`, still a stub), then p2s **#35** / **#29**.
7. **Release the six stale claim refs** (below).

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
- **Running the app: bind to the Tailscale IP, not `0.0.0.0` and not `127.0.0.1`.**
  `uvicorn src.service.api:app --host 100.80.210.100 --port 8777` makes it reachable from the Omen
  over the tailnet while staying invisible to whatever local wifi the Mac is on. `127.0.0.1` is
  Mac-only; the Omen cannot see it.
- **`pkill -f "uvicorn src.service.api:app"` kills EVERY copy of this app on the machine, not your
  own.** Two agents were running servers on this Mac (8777 and 8766) and each restart by one killed
  the other's. If you need a server that survives someone else's cleanup, launch it through a
  wrapper that calls `uvicorn.run()` in-process so your command line carries no `uvicorn` token.
- **Two agents sharing one checkout is the real hazard, not the ports.** Jinja templates and
  `static/` are read per request, so a live server serves whatever is checked out *right now* — a
  second agent's uncommitted work-in-progress was being served to the Omen without anyone
  intending it, and a `git checkout` would have swapped files mid-request. Work in a worktree:
  `git worktree add ~/Dhruv/worktrees/munich-expo-hackathon/<branch> -b <branch>`.
- **The scenario cache is on disk** at `data/derived/service/<id>.json` and carries an inputs
  fingerprint, so it survives a restart and will not serve a result mixed across two generations of
  input. A pre-warmed scenario means the page loads instantly instead of running a multi-minute LP.
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
