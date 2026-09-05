# Munich Expo Hackathon

Fixed-deadline build-and-demo hackathon repo. Event theme, rubric, deadline, and judge list were
not confirmed at scaffold time — see `COMPETITION.md` for the single source of truth on event facts
(currently all `TBD`) and `VISION.md` for the project thesis (currently all `TBD`).

This `README.md` is a **plain file**, not governance-locked — see `GOVERNANCE.md` for the full
category breakdown. It's safe to append to (e.g. a later graft of tooling onto this repo) without
Main Agent sign-off.

## Read order for a cold agent
1. `CLAUDE.md` (or `AGENTS.md` if you're not Claude Code)
2. `STATE.md` — live snapshot; wins over the worklog if they disagree about current truth
3. `VISION.md` — the thesis
4. `COMPETITION.md` — event facts
5. `GOVERNANCE.md` — who may edit what
6. `AGENTS.md` — multi-agent concurrency model
7. `worklog.md` (tail) — recent history
8. `experiments/experiment_log.md` — recent experiments

## Status
Stack: **undecided** (see `design/decisions/ADR-0002-stack-selection.md`, open, `Q-0002`).
No test baseline exists yet — see `tests/README.md`, and do not fake one.

## End-of-session checklist
- Overwrite `STATE.md` with the current snapshot
- Append a dated + timestamped `worklog.md` entry
- Append any new `EXP-####` row to `experiments/experiment_log.md`
- Log any LOCKED-file edit in `GOVERNANCE.md`'s audit table
- Release any work claim you held in `STATE.md`'s claims table

If a doc is still full of `TBD` / `TODO(Mulaydm10)`, say so in your summary rather than inventing a
value to fill it.

## License
MIT — see `LICENSE`.

## This project runs on the agent-bus protocol
Grafted from [`agent-bus-template`](https://github.com/Mulaydm10/agent-bus-template) — see
`AGENTS.md` for the coordination protocol (design node reviews, worker claims via `claim/<n>` git
refs, `docs/verify.txt` CI gate). The project's own pre-graft `AGENTS.md` (concurrency model, claim
table, locked-file list) is preserved unchanged at `AGENTS-project.md`.

Lanes are currently **provisional**: the event brief (theme, rubric, deadline, stack) was not known
at graft time, so there is exactly one placeholder lane, `src/00_scaffold`, and no queue issues have
been cut against it. Design (Devin) is expected to replace it with the real task split once the
brief lands — see `docs/STATE.md` and the `design node handshake` issue.
