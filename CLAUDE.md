# Munich Expo Hackathon — cold-start entry point

This is the auto-loaded entry point for any Claude Code session opening this repo cold.
If you are not Claude Code, read `AGENTS.md` instead (same rules, vendor-neutral).

## What this is
A fixed-deadline build-and-demo hackathon repo, scaffolded before the event's own facts
(theme, rubric, deadline, judges, track) were known. It is **not** an ML competition
(no leaderboard/submission-score loop) and **not** open-ended research (no fixed deadline
absent) — see `COMPETITION.md` once it's filled for what kind of event this actually is.

## Read order (cold start)
1. `CLAUDE.md` (this file) — or `AGENTS.md` if you're not Claude Code
2. `STATE.md` — live snapshot, most-current-truth
3. `VISION.md` — the thesis (currently unfilled, see below)
4. `COMPETITION.md` — event facts (currently unfilled, see below)
5. `GOVERNANCE.md` — who may edit what
6. `AGENTS.md` — multi-agent concurrency model
7. `worklog.md` (tail) — recent history
8. `experiments/experiment_log.md` — recent experiments

## Governance, in one paragraph
Files are either **LOCKED** (event facts, thesis, ADR process, experiment/research/logs/tests
READMEs, AI onboarding prompt — edit only via the Main Agent, see `GOVERNANCE.md`), **deliberately
not locked** (`CLAUDE.md`, `STATE.md`, `AGENTS.md`, `DEMO.md` — their value is staying current), or
**append-only** (`worklog.md`, experiment/research logs — never edit history, only add to it).
Everything else (this repo's `README.md`, `.gitignore`, `.gitattributes`, `LICENSE`, ADR files
themselves, `notes/glossary.md`, `notes/judging_alignment.md`, `submissions/README.md`) is plain —
edit freely, no sign-off required. Every LOCKED-file edit (including first authorship) is logged in
`GOVERNANCE.md`'s audit table.

## Stable ID scheme
`grep -rn '<ID>' .` must recover a thing's full trace across every log, table, and directory name.
- `ADR-####` — architecture/process decisions, in `design/decisions/`
- `Q-####` — open questions, in `research/open_questions.md`
- `EXP-####` — experiments/spikes, in `experiments/experiment_log.md`, folders in `logs/`
- `DEMO-####` — demo scenarios, in `DEMO.md`

## Canonical commands
**Stack is not yet decided.** No build/test/run commands exist yet — do not invent or fake one.
See `design/decisions/ADR-0002-stack-selection.md` (open, `Q-0002`) for the decision in progress.
`TODO(Mulaydm10)`: whoever resolves that ADR must land the canonical commands here **and** a green
smoke test in `tests/` **in the same change** — not one without the other.

## Hard rules
- Never invent event facts (deadline, rubric, theme, judges). If `COMPETITION.md` says `TBD`,
  leave it `TBD` — a wrong assumed rule can disqualify a submission.
- Never commit to a stack outside the open ADR while `Q-0002` is unresolved.
- `DEMO.md` must stay runnable at all times once any scenario exists in it: fixing a broken demo
  outranks adding a new feature.
- Any agent finishing a unit of work (including blocking or running out of turn budget) updates
  `STATE.md` before yielding.

## End-of-session checklist
1. Overwrite `STATE.md` with the current snapshot.
2. Append a dated + timestamped entry to `worklog.md`.
3. Append any new `EXP-####` row to `experiments/experiment_log.md`.
4. Log any LOCKED-file edit in `GOVERNANCE.md`'s audit table.
5. Release any work claim you held in `STATE.md`'s claims table.
6. If a doc you touched is still full of `TBD` / `TODO(Mulaydm10)`, say so out loud in your
   summary rather than quietly filling it with a guess.
