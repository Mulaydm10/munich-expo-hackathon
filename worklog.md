# Worklog

**Append-only.** Never edit or delete a past entry — if something was wrong, add a new entry that
corrects it. Entries are dated **and timestamped**: hackathon history moves hourly, not daily.
If this file and `STATE.md` disagree about current truth, **STATE.md wins**; this file only
explains how we got here.

---

### 2026-09-05 19:05 — initial scaffold

- Scaffolded the repo directly into `/Users/mulaydm10/Dhruv/munich-expo-hackathon` (hackathon-setup
  agent). Event facts (theme, rubric, deadline, judges, track) were not known at scaffold time and
  were left as explicit `TBD` in `COMPETITION.md` — not invented.
- Stack left undecided per instruction: no `pyproject.toml`/`package.json`/language `src/` tree.
  Opened `design/decisions/ADR-0002-stack-selection.md` (Status: Proposed) and registered `Q-0002`.
  No test baseline exists yet; `tests/README.md` says so explicitly.
- Repo will later be grafted onto the agent-bus multi-agent protocol. Kept `.gitignore` free of
  blanket data-fixture-swallowing patterns (no bare `*.csv`/`*.json`/`*.parquet`/`data/`) and added
  an explicit `!tests/**` negation guard as a proactive safeguard for that graft step.
- No git init / commit performed — human handles version control.

### 2026-09-01 — design node joined; kickoff blockers resolved

Devin joined `Mulaydm10/munich-expo-hackathon` as the bus **design node** (`AGENTS.md`,
`.devin/playbooks/design.md`). Took the `claim/state` lock, set `design:` in `docs/STATE.md`.
Everything below is on `bot/join-real-lane-split` for the human to merge — design does not merge.

- **Thesis chosen** (`Q-0001` closed): FlexGrid — EV charging as dispatchable grid flexibility.
  Quantile forecast → thermal/per-phase envelope → deadline-feasible schedule → portfolio pooling
  over Germany's real public charge-point registry. Non-goals written down explicitly (no V2G, no
  live market participation, no OCPP, no federated learning in v1) so scope creep has a rule to
  break rather than a vibe to argue with.
- **Event facts filled** from the organizers' own pages. Two official sources contradict each other
  and were left contradictory on purpose: the deadline (`Q-0003`; planning against the earlier
  reading, Sun 20 Sep 17:00 CEST) and whether a solo participant may submit (`Q-0004`). Rubric
  weights are unpublished and stay `TBD`. Third-party cash-prize figures were found on no official
  page and are not repeated. No organizer contact made — that needs the human.
- **`ADR-0002` Accepted** (`Q-0002` closed): Python 3.12 for every lane, FastAPI + Jinja2 + CDN
  front-end, no node toolchain — one runtime because `requirements-dev.txt`/`docs/setup.sh` are
  canary-gated, and because a bundler is a demo-day failure mode. Landed with it, as that ADR
  demanded: canonical commands in `CLAUDE.md`, real `.gitignore` (with the `!tests/**` guard now
  actually guarding `data/`), and a green smoke test per lane — `python3 -m pytest tests -q` →
  10 passed, verified, not assumed.
- **Architecture: nine lanes**, replacing the `src/00_scaffold` placeholder.
  `data → fleet → forecast → grid / market / sched → service → ui / voice`. Lane dirs are plain
  Python identifiers (an earlier `src/10_data` naming was wrong — not importable). Every lane has a
  design-owned contract (`contracts/src/<lane>.md`) stating its public API and the guarantees its
  tests must assert, plus one cross-lane `contracts/CONVENTIONS.md` fixing units, the 15-minute
  tz-aware time grid, the on-disk layout, determinism and the "no bare numbers across a boundary"
  rule. `docs/verify.txt` maps all nine lanes.
- **No dataset dependency**: the organizers' sample sessions only exist at the on-site briefing
  (`Q-0007`), so the build stands on public German data (registry, SMARD, EPEX, DWD) with
  synthesised sessions. If the file appears it becomes a validation set, never an input.
- Because this change touches `docs/verify.txt` and `requirements-dev.txt`, `AGENTS.md` requires a
  worker-authored pre-merge canary (based on this design branch) and a post-merge canary commit.
  Requested from Claude on the canary issues; design must not author either.
- Environment: no `.pre-commit-config.yaml` and no `.husky/` in this repo — nothing to install.
