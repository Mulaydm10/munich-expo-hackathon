# STATE.md — current shape of the project (written by design only, via `claim/state`)

## Purpose
**FlexGrid** — turn Germany's already-installed EV charging infrastructure into dispatchable grid
flexibility, and quantify how much of it exists. Entry for the MunichTech EXPO 2026 challenge
*Mobility & Automotive: EV Charging Load Predictor* (see `VISION.md` for the thesis and
`COMPETITION.md` for event facts). Submission deadline is **not yet unambiguously confirmed** — two
conflicting official readings are recorded in `COMPETITION.md` and tracked as `Q-0003`; plan against
the earlier one (Sun 20 Sep 2026 17:00 Europe/Berlin).

mode: solo
attention: active
merge: human
design: devin-ai-integration[bot]
<!-- design: <login>   set by design on join, via claim/state; absent = repo not live, workers report "no design node" -->
<!-- mode: solo | team.  attention: active | paused (workers' cross-repo pick order skips paused repos; design sessions do not wake).
     merge: human | auto-lane (auto-lane = you give up human code review of lane PRs for throughput; design sets auto-merge on green + approved claim PRs; refused unless main requires lane+run; design/* always human).
     CI reads these from the live tip of the base branch and workers from `main`, never from a PR head: a PR must not relax the enforcement it is judged by. -->

## Lanes

| lane | directory | purpose | contract |
|------|-----------|---------|----------|
| `lane:canary` | `canary/` | two standing issues: post-merge canary (permanent claim, draft PR) and pre-merge canary (transient claim per workflow PR) | — |
| `lane:src/data` | `src/data/` | ingest public German data (charge-point registry, grid load, day-ahead prices, weather, balancing results) into one canonical on-disk shape | `contracts/src/data.md` |
| `lane:src/fleet` | `src/fleet/` | synthesise per-site charging sessions from registry sites + weather + behaviour params; the load the rest of the system predicts and shapes | `contracts/src/fleet.md` |
| `lane:src/forecast` | `src/forecast/` | probabilistic (quantile) load forecasting + calibration metrics; outputs a distribution per site-hour, never a point estimate | `contracts/src/forecast.md` |
| `lane:src/grid` | `src/grid/` | site physics: transformer thermal model + three-phase allocation → time-varying feasible power envelope | `contracts/src/grid.md` |
| `lane:src/market` | `src/market/` | bid sizing off forecast quantiles, portfolio pooling + spatial correlation, auction settlement, revenue / penalty / CO₂ accounting | `contracts/src/market.md` |
| `lane:src/sched` | `src/sched/` | the optimiser: charge every vehicle by its deadline, inside the envelope, holding the sold reduction floor, at least cost | `contracts/src/sched.md` |
| `lane:src/service` | `src/service/` | scenario service: run a date/portfolio through the pipeline and serve results as JSON | `contracts/src/service.md` |
| `lane:src/ui` | `src/ui/` | judge-facing map + dashboard, served by the API, no separate build step | `contracts/src/ui.md` |
| `lane:src/voice` | `src/voice/` | depot-operator voice layer (ElevenLabs agent) over the API's read/act surface | `contracts/src/voice.md` |
<!-- bootstrap.sh appends one row per lane you pass it; design edits after that. A lane may be a nested path (`src/01_ingest`); no lane may be a prefix of another. -->

Lane order is the data flow: `10 → 20 → 30 → 40/50/60 → 70 → 80/90`. Lanes talk only through the
on-disk shapes and function signatures in `contracts/`, so a downstream lane can be built against a
contract before its upstream lane produces real data (fixtures live in `tests/<lane>/`).

**Active now: all nine.** Six lanes have merged something onto `main` (`src/data` #20, `src/grid`
#21, `src/forecast` #22, `src/market` #24, `src/sched` #26, with `src/fleet` #19 in review), so the
pipeline exists end to end and the seam lanes are no longer PRs against stubs: `src/service` (#28),
`src/ui` (#30) and `src/voice` (#31) are cut. `src/voice` carries `blocked-by: #28` — it is written
against the service HTTP surface and cannot start before the routes answer.

Pick order is the protocol's (lowest prio, then oldest, then lowest number); the pinned Board (#16)
is regenerated from claim refs on every design wake and is the fastest way to see what is actually
free. Two things the Board says that are worth repeating here: a merged lane is not a finished lane
(`src/market` has #29 and #33 open against it, `src/sched` had eight defects deferred out of #26
into #27), and #33 — `pool` / `correlation_structure` / `diversification_curve` — is the one piece
of contracted API that does not exist anywhere while carrying the project's central claim.

## Verify environment
`docs/setup.sh` (design-owned; CI runs the copy on `main`; changing it needs a canary like any workflow change). Default: `python3` + `requirements-dev.txt`. Workers run the same script once per worktree.
<!-- change both this line and requirements-dev.txt / docs/verify.txt if the project is not Python -->

Stack is decided (`ADR-0002`, Accepted): Python 3.12, numpy/pandas/pyarrow/scipy/scikit-learn,
FastAPI + Jinja2, front-end libraries from CDN so there is no node build step in CI or on a demo
machine.

## Decisions
- Lock = `claim/<n>` ref via git refs API (201/422). Labels advisory; refs beat labels.
- Lane + per-lane verify (`docs/verify.txt`) are CI jobs in one workflow (`checks.yml`: lane → resolve → run).
- Reclaim renames to `abandoned/…`; resume only on green CI + passing verify.
- Worker id = device/session; sessions hold claims, machines don't. Worktree per claim.
- Contracts in `contracts/<lane>.md`, design-owned.
- One runtime (Python) for every lane including the UI: a second toolchain would mean changing
  `docs/setup.sh`, which is canary-gated, for no demo-day benefit.
- Every number a judge sees traces to a public dataset or to a named, checked-in parameter. No
  invented statistics — same rule as `COMPETITION.md`'s event facts.

## Known gaps
- Branch protection (`lane` + `run` required, PR-only `main`) needs a public repo or GitHub Pro/org. Without it checks are advisory — see docs/SETUP.md. Verified 403 on this repo: enforcement is the human.
- One GitHub account for all workers (solo mode) = one API rate bucket; GitHub App with per-device tokens before ~20 nodes.
- Actions minutes are one pool per repo; check quota before a team event.
- Submission deadline unresolved (`Q-0003`) and the team-size rule contradicts itself between
  Devpost and the rules page (`Q-0004`). Both need an organizer answer, not a guess.
- No official dataset in hand: the challenge page says a sample charging-session dataset is handed
  out at the on-site briefing. The build therefore does not depend on it (`src/fleet` synthesises
  load from the public registry); if the file appears it becomes a validation set, not a dependency.

## Log
- 2026-09-05: repo created from agent-bus-template; bootstrap run (mode=solo).
- 2026-09-05 (design join): Devin took the repo as design node. Replaced the provisional
  `src/00_scaffold` lane with the real nine-lane split above, resolved `ADR-0002`, filled
  `COMPETITION.md` / `VISION.md` from verified event pages, cut the first queued issues.
- 2026-09-05: #5 merged as `6518d017`. Both canaries green and checked at log level, not tick level
  — pre-merge #13 with `BASE_SHA=2bb4a6a` (the design tip, so it certified the new `verify.txt` and
  `requirements-dev.txt`), post-merge #4 `bc60ab7` with `BASE_SHA=6518d017` and a ~23 s install of
  the full ADR-0002 set. Gate #6 closed; eight lane issues claimable. Handshake #3 answered and
  closed. Board pinned as #16.
- 2026-09-05: **#5 was merged by the worker account, not the human.** `merge: human` says otherwise
  and `AGENTS.md` treats relayed intent as unauthorized, so this is logged as a protocol deviation,
  not a precedent. Consequence to carry: the three LOCKED files (`COMPETITION.md`, `VISION.md`,
  `tests/README.md`) are live with no human sign-off, and the human merge was supposed to *be* that
  sign-off. If the thesis is wrong, it is one revert PR.
