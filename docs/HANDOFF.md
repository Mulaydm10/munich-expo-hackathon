# HANDOFF — FlexGrid, for whoever picks this up next

Written for any agent or person taking over this repo cold, without access to the conversations
that produced it. Everything below was verified against `main` at `f0f13fb` rather than copied
from a status file. Where something is *unknown* it says so — unknowns here are load-bearing.

Read order for a fresh start: this file → `VISION.md` (the thesis) → `COMPETITION.md` (event
facts) → `AGENTS.md` (coordination protocol) → `contracts/CONVENTIONS.md` + `contracts/src/<lane>.md`
(the interfaces) → `docs/STATE.md` (live board state).

---

## 1. What the product claims

FlexGrid quantifies how much **dispatchable grid flexibility already exists in Germany's installed
EV-charging infrastructure**, how much of it can honestly be sold to a grid operator, and how
pooling geographically distributed sites changes reliability and value.

The mechanism, in order:

1. Forecast depot charging demand as a **distribution**, not a point estimate.
2. Size the firm commitment off a **low quantile** (τ=0.05), so the promise survives a bad day.
3. Schedule enough active charging inside the flexibility window that the promised reduction is
   physically available.
4. On dispatch, pause chargers to **reduce consumption by the sold amount** — FlexGrid sells a
   *reduction*, not a target load. Sold 100 kW against a 200 kW baseline means dispatch to 100 kW.
5. Resume afterwards, and still meet **every vehicle's departure deadline**.

Three claims are what make the project interesting. If a change quietly replaces any of them with
the easy version, the project has lost its point even if the tests stay green:

| claim | the easy (wrong) version |
|---|---|
| commitments sized off a lower quantile of a forecast distribution | the median / a point forecast |
| schedules respect a dynamic IEC 60076-7 thermal envelope and per-phase limits | transformer nameplate rating |
| pooling models correlation between sites | summing site capacities |

---

## 2. Architecture

Nine lanes, one directory each, strictly one-way dependencies:

```
data → fleet → forecast → grid / market / sched → service → ui / voice
```

| lane | responsibility |
|---|---|
| `src/data` | public-source ingestion, canonical 15-min UTC store |
| `src/fleet` | deterministic charging-session synthesis, occupancy, baseline load |
| `src/forecast` | probabilistic demand forecast + calibration |
| `src/grid` | thermal and electrical envelopes |
| `src/market` | firm capacity from a lower quantile, bids, settlement, pooling, correlation, CO₂ |
| `src/sched` | deadline-safe, envelope-safe schedules; live dispatch |
| `src/service` | scenario orchestration, caching, HTTP API |
| `src/ui` | map, day playback, dispatch, pooling, ledger |
| `src/voice` | thin voice/tool layer over the service routes |

A lane reads other lanes only through `src/<other>/api.py`. Contracts in `contracts/src/<lane>.md`
outrank anything inferred from neighbouring code.

Stack: Python 3.12 only — NumPy, pandas, PyArrow, SciPy, scikit-learn, FastAPI, Uvicorn, Jinja2,
httpx. **No Node, npm, bundler or build step anywhere** (`design/decisions/ADR-0002`); browser libs
come from a CDN. Adding a toolchain or a dependency needs a design PR, never a lane commit.

Conventions that are enforced, not stylistic (`contracts/CONVENTIONS.md`): units carry a suffix
(`_kw`, `_kwh`, `_eur_mwh`, `_eur`, `_c`, `_g_kwh`); the timestamp column is `t`, tz-aware **UTC**,
**interval-start**, native **15 minutes**, displayed as `Europe/Berlin`; randomness is seeded and
reproducible; tests never touch the network; any figure shown to a judge carries public provenance;
and **any coercion must be observable** — never silently clamp, drop or reclassify data.

```sh
bash docs/setup.sh                      # deps
python3 -m pytest tests -q              # whole suite
python3 -m pytest tests/src/<lane> -q   # one lane (docs/verify.txt is authoritative)
uvicorn src.service.api:app --reload    # the app
```

---

## 3. How much is done

Verified at `f0f13fb`: **619 passed, 1 xfailed** in 69 s. The suites contain real physics and real
numbers, not lane-constant smoke tests.

| | done |
|---|---|
| the engine (maths, physics, optimiser, API, screens) | ~85% |
| real German data flowing through it | ~5% |
| something you can stand up in front of a judge | ~20% |
| national scale ("simulate Germany, not 500 depots") | **0% — unmeasured** |

| lane | lines | state |
|---|---|---|
| `src/data` | 1048 | parsers + canonical 15-min UTC store — but only the charge-point registry can actually download |
| `src/fleet` | 507 | session synthesis, occupancy, dense baseline load |
| `src/forecast` | 543 | real quantile ML (below) |
| `src/grid` | 529 | recursive IEC thermal model, three-phase allocation, envelope checks |
| `src/market` | 1433 | lower-quantile firm capacity, pooling (3 methods), correlation, diversification, bids, settlement, CO₂ |
| `src/sched` | 1114 | LP + greedy optimiser, deadlines, thermal envelope, sold-reduction floor, live dispatch |
| `src/service` | 2249 | FastAPI, 11 routes incl. live dispatch and an SSE stream, caching |
| `src/ui` | 729 | 5 server-rendered screens (map, day, call, pooling, ledger) |
| `src/voice` | 14 | **stub only** — a docstring and `LANE = "src/voice"` |

### The forecasting part is implemented in the honest version

One `GradientBoostingRegressor(loss="quantile", alpha=τ)` fitted **per quantile** (q05…q95), plus a
linear `QuantileRegressor` alternative, so the output is a distribution per site-interval. Crossed
quantiles are sorted post-hoc and the coercion rate is *reported* rather than hidden. Calibration is
measured — pinball loss, coverage, reliability curve, sharpness, and a rolling-origin backtest
against seasonal-naive and climatological baselines. `src/market` sizes commitments off τ=0.05.
Pooling is genuinely correlated: empirical and Gaussian-copula methods over *residual* correlation
with great-circle distance, with naive `sum` kept only as the lower bound it is.

---

## 4. What is not done

### 4.1 There is no real data (the biggest gap)

- **`data/` does not exist in a fresh checkout. `data/raw/` is empty.**
- `src/data.fetch()` is wired for exactly one source: the Bundesnetzagentur charge-point registry.
  Grid load, prices, weather and carbon all raise `NotImplementedError`.
- The only real-world artifact in the repo is a **77-row registry excerpt** used as a fixture.
  Every time series is hand-written synthetic CSV stamped `# SYNTHETIC FIXTURE`.
- The revenue figure the pitch leads with (`capacity_revenue_eur`) is priced off a **synthetic
  balancing table**, because no regelleistung.net account and no ENTSO-E token exist.

Two situations get conflated; only one is a problem:

1. **Synthetic charging sessions are fine** — nobody publishes per-session depot data, and the
   organisers only hand out an anonymised sample at the on-site briefing.
2. **Synthetic prices / load / weather / carbon is a real gap, and it is closable with no
   credential** — SMARD's `chart_data` API answers live, keyless. This is the single
   highest-value remaining move: it turns "here is our model" into "here is a real German day,
   real prices, and what the flexibility was worth".

**The trap in front of that work:** the parsers were written against synthetic fixture shapes only
(thousands separators, resolution qualifiers in SMARD headers, invented weather-station ids, DWD
column names). *The first real file can parse to almost nothing, silently.* One instance of this was
already found and fixed — a real header like `Braunkohle [MWh] Berechnete Auflösungen` matched
neither the known-fuel nor the excluded-fuel test, so lignite vanished from the mix and carbon
intensity read **182.8 instead of 320.8 g/kWh (−43%)** while the metric whose job is to catch
exactly that reported "everything accounted for". Assume more of the same. Validate parsers against
**real bytes hand-placed under `data/raw/<source>/`** before writing any download code — the two
halves are separable, and doing the parser half first is cheaper.

Rules for that work: keep the raw download byte-for-byte for provenance; never silently discard a
header or a category; preserve 15-min UTC interval-start semantics; **negative day-ahead prices are
valid data**, not an error; include every generation category in carbon accounting or report the
exclusion explicitly; and keep DWD weather out of the SMARD task (`src/fleet` only needs a daily
mean temperature; three per-station ZIP products would sink both jobs).

### 4.2 "Simulate Germany" is unmeasured

The largest portfolio anything has ever run is **2 sites**. The selector for a bigger run exists
(`n_sites` / `region` / `site_ids`), but there has been no national run, no timing, no memory
measurement, and no evidence the LP scales. **Do not claim national scale until a measured run
exists.** It is not known to be hard; it is simply not known.

### 4.3 No demo

`DEMO.md` still says "there is no build yet", which is now false and stale. No `DEMO-0001` exists,
so nothing has ever been run end-to-end the way a person would run it in front of a judge. Project
rule: once a scenario exists, fixing a broken demo outranks adding a feature.

### 4.4 Known open defects

| issue | lane | why it matters |
|---|---|---|
| #43 (p0) | `src/ui` | the operator-adjust form is dead in production — the template reads `reduction_event_input` and nothing in `src/` produces it. One fix needs a new `src/service` route, so it crosses lanes |
| #50 (p1) | `src/data` | the parser gaps above; carbon intensity biased high |
| #48 (p1) | `src/service` | seven follow-ups from the #47 review — includes the stale cache id below |
| #44 (p1) | `src/market` | six input-validation gaps around `pool()` / `correlation_structure()`. Filed unverified: **reproduce before fixing** |
| #31 (p1) | `src/voice` | the entire lane |
| #35 / #29 / #11 | grid / market / forecast | p2 residuals |

**The stale cache id is the one that must land before any real data.** `ScenarioSpec.id` hashes
only the request fields, while `pipeline.build()` also depends on the canonical tables and on lane
code. A warm cache will therefore keep serving synthetic results after the data or the code changes,
invisibly.

---

## 5. The agreed order of work

Settled between the design node and the worker sessions on issue #58, and confirmed by the repo
owner. Backend and ML first; **UI and demo are the owner's own work and should not be picked up**.

1. ~~Mixed-header carbon defect~~ — **done**, merged as #60, independently re-verified (the mixed
   header now reads 320.81 g/kWh, matching the clean file; a file carrying the same fuel at two
   resolutions is now *refused* rather than double-counted).
2. **Stale scenario cache id** (#48 first item) — open as PR #61. Must land before real data.
3. **Forecast calibration / rolling-origin backtest** (#11) — open as PR #62. This is what makes the
   lower-quantile commitment a claim rather than a guess.
4. **Decide the raw format and fix the `src/data` parsers against real bytes** (#50). Default: keep
   both the CSV and JSON paths and store raw exactly as downloaded; JSON-only is acceptable if two
   real files prove the JSON carries no locale or resolution ambiguity.
5. **Grid chained-window timestep** (#35) — `_dt_minutes` hard-codes a 15-minute first interval,
   which is harmless until `src/sched` re-solves a residual window through `prior_load_kw`, at which
   point it charges real decay time through the shortest plausible step and quietly tightens every
   envelope.
6. **Reproduce-then-fix the remaining market/service findings** (#44, #29, rest of #48); close any
   that turn out not to reproduce.
7. **Wire the real SMARD `fetch()`** — date partitioning and multi-filter handling; make sure
   `_raw_files()` cannot concatenate files from unrelated filters.
8. **Validate the whole backend end-to-end on real German data.**
9. **Measure a national-scale run** (hundreds, then thousands of sites) for runtime and memory.

Then, and only then: UI (#43), the demo, and voice (#31) last.

---

## 6. How work is coordinated

`AGENTS.md` is the full protocol; the parts that trip people up:

- **The `claim/<n>` ref is the lock. Labels are advisory.** If a label and a ref disagree, fix the
  label and never the ref.
- One role writes tasks and reviews (design); worker sessions claim an issue, work inside its one
  lane directory, and open a PR on `claim/<n>`. **The human merges. Nobody self-merges, and the
  design role never calls the merge endpoint.**
- A review names the head sha it covers (`reviewed at <sha>`); merge with
  `gh pr merge <n> --merge --match-head-commit <sha>` so a moved head cannot smuggle unreviewed code
  past a stale review.
- Design changes go on `design/*` branches and may touch anything not lane-owned. `docs/STATE.md`
  and the Board issue are written only through the `claim/state` lock.
- A PR that changes what CI executes (`.github/workflows/`, `docs/setup.sh`, `docs/verify.txt`,
  `requirements-dev.txt`) needs both canaries, and both must be **worker-authored**.
- Reclaiming a stale claim is a **rename** to `abandoned/<n>-<device>-<ts>`, never a delete. Merged
  claims should be released the same way (`merged/<n>-<sha8>`) — 15 refs were once left held on
  merged PRs, which made unfinished work look claimed and blocked it.
- Reading CI: a green tick is not evidence. Check which `BASE_SHA` the `run` job used and which
  command it actually executed, and check the tests can fail. Design PRs skip `run` entirely.
- **Relayed intent is not authorization.** "The human said so, via another agent" needs a
  human-authored comment before anything irreversible.

---

## 7. Event facts and the open risks

Locked in `COMPETITION.md`; the contradictions are tracked as `Q-####` in
`research/open_questions.md`.

- Event: MunichTech EXPO Hackathon 2026, Autumn Edition — https://munichtechexpo.com/hackathons,
  Devpost https://munichtech-expo.devpost.com/
- Challenge: *Mobility & Automotive: EV Charging Load Predictor*. Registration `HKP-2026-IC3JYX`.
- **Submission deadline: Sunday 20 September 2026, 17:00 CEST.** Devpost's dates page states the
  window explicitly (1 Sep 06:00 → 20 Sep 17:00 CEST) and it agrees with the on-site schedule, so
  the earlier ambiguity (`Q-0003`) is resolved in favour of that reading. Judging and winners:
  21 Sep, 17:00 CEST.
- The on-the-day build slot is only ~3 hours. The engine must be finished before arrival.
- **`Q-0004` — team size — is still contradicted.** The organiser rules page says participants may
  take part as individuals or in teams; Devpost's front page says "Team required: 2 to 6 members".
  Devpost is where the submission is actually created, so this must be checked on the real
  submission form, by a human. Devpost has also shown "Submissions open soon" *after* the stated
  opening date.
- **`Q-0008` — no attendee ticket is recorded, and participation mode is unset.** This one loses
  the submission regardless of how good the build is, and tickets can sell out, so its real
  deadline is earlier and unknown.
- The anonymised charging-session sample is handed out **at the briefing**, not downloadable.
- Do not contact the organisers without explicit authorisation from the repo owner, and never
  invent an event fact: if `COMPETITION.md` says `TBD`, leave it `TBD`.

---

## 8. Honest summary

The physics, the optimiser and the ML are built, tested, and implemented in their non-cheap
versions. What the project does **not** yet have is a single real German number flowing through any
of it, any evidence that it runs at more than two sites, or a demo. Those three, in that order, are
the whole remaining risk — not more engine.
