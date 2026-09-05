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
