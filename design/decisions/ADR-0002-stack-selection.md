# ADR-0002: Stack selection

**Status:** Accepted (2026-09-01)
**Date:** 2026-09-05 (opened) · 2026-09-01 (decided)
**Related:** Q-0002 (closed by this ADR), Q-0001 / `VISION.md`

## Context
The idea is now chosen (`VISION.md`): a data- and optimisation-heavy simulation of Germany's public
charge-point registry, with quantile forecasting, a physical site model, an LP scheduler, a market
simulation, and a map-based demo. Nine lanes are built in parallel by several agent sessions
(`docs/STATE.md`), and CI runs one verify command per lane from a design-owned file — so the runtime
is not just a preference, it is the thing every lane's green depends on.

Constraints that actually decide it:
- **One runtime, or canary tax.** `docs/setup.sh` and `requirements-dev.txt` are canary-gated: every
  toolchain added costs a pre- and post-merge canary and a slower `run` job for all nine lanes.
- **The heavy work is numerical**, not I/O concurrency: quantile regression, an LP per site, a
  thermal difference equation, correlation across thousands of series.
- **Demo-day risk.** A build step that can fail on conference wifi in front of judges is a real
  failure mode, and a `node_modules` install is exactly that.
- Deadline is hours of build time, not weeks (`COMPETITION.md`).

## Options considered
- **A — Python everywhere, front-end from CDN.** numpy/pandas/pyarrow/scipy/scikit-learn for the
  modelling, FastAPI + Jinja2 to serve, front-end libraries loaded from a pinned CDN URL with an
  integrity hash. One `pip install`, no bundler, no lockfile drift.
  *Cons:* front-end ergonomics are worse than a modern JS toolchain; large-array work in the browser
  needs care.
- **B — TypeScript/Node everywhere.** Excellent UI story, single language.
  *Cons:* the numerical stack does not exist at parity — quantile GBMs, LP solvers and the thermal
  model would all be hand-rolled or wrapped. Unacceptable risk on the parts that carry the thesis.
- **C — Polyglot: Python back end + Vite/React front end.** Best UI ergonomics.
  *Cons:* two toolchains in CI (canary tax ×2, slower `run` for every lane), a node install on the
  demo machine, and a cross-language contract at the one seam that must not break under time
  pressure. The UI gain does not pay for it in a one-day build.

## Decision
**Option A.** Python 3.12 for every lane, including the UI lane, which is Jinja2 templates plus
hand-written ES modules served by FastAPI. Front-end libraries (map + charts) come from a pinned CDN
with an integrity hash; nothing is bundled, transpiled or installed at demo time.

Dependency set is fixed in `requirements-dev.txt`; adding one is an `agent:devin` issue, not a
worker commit, because that file is what CI executes.

## Consequences
Landed **in the same change** as this ADR (as this ADR required of whoever resolved it):
1. `CLAUDE.md` "Canonical commands" filled with real setup/test/run commands.
2. A green smoke test per lane (`tests/src/<lane>/test_lane_surface.py`) and `docs/verify.txt`
   rewritten to the real nine lanes; `tests/README.md`'s "no baseline" language removed. Verified:
   `python3 -m pytest tests -q` → 10 passed.
3. `.gitignore` extended with `.venv/`, `data/` and Python artefacts, respecting the `!tests/**`
   negation guard.

Ongoing:
- No node toolchain enters this repo without a new ADR. "Just add Vite for the map" is the change
  this decision exists to refuse.
- If browser-side performance on the national map becomes the bottleneck, the answer is
  server-side aggregation in `src/service` (bin by zoom level), not a front-end build step.
- Python-side performance: vectorise, chunk by site, and decompose the national run into per-site
  problems (`contracts/src/sched.md`). If a hot loop still dominates, profile and rewrite that loop
  with numpy — do not reach for a second language.
