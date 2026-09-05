# ADR-0001: Scaffold bootstrap — governance, ID scheme, and module choices

**Status:** Accepted
**Date:** 2026-09-05
**Related:** Q-0001 (thesis unfilled), Q-0002 (stack unfilled)

## Context
This repo needed to exist before the event's own facts (theme, rubric, deadline, judge list,
track) and before an idea/stack were chosen. It also needs to be gracefully grafted onto the
agent-bus multi-agent protocol afterward. Two goals govern the scaffold: a cold agent should be
able to reconstruct project state without asking a human, and a human under deadline pressure
should find the one thing they need in under 15 seconds.

## Options considered
- **Fabricate plausible event facts/thesis now, correct later** — rejected: a wrong assumed
  deadline/rubric/rule can disqualify a real submission, and a plausible fabrication is far more
  expensive to catch than an obvious blank.
- **Skip the event-facts/thesis files entirely until kickoff** — rejected: the read-order and
  ID-scheme machinery (COMPETITION.md, VISION.md as link targets) needs to exist from the start so
  every other doc has something concrete to point at, even if the content is `TBD`.
- **Commit to a stack now to "make progress"** — rejected per explicit instruction: stack is
  undecided, and picking one preemptively creates unwind cost later plus risks silently leaking a
  language/tooling commitment into READMEs or configs.

## Decision
Scaffold the full three-piece cold-start pattern (`CLAUDE.md`/`STATE.md`/`worklog.md`) plus the
hackathon layer (`COMPETITION.md`, `DEMO.md`, `AGENTS.md`, `notes/judging_alignment.md`), with every
event fact and thesis slot marked `TBD` and every stack commitment routed through an open ADR
(`ADR-0002`) plus `Q-0002`. Omitted `ideas/`, `data/`, `models/`, and `evals/` since no idea, ML/data
component, or agent/LLM subject matter was confirmed at scaffold time — those modules presume
pipelines this repo doesn't have yet and would be dead weight. Owner/Main Agent for locked-file
governance defaults to the existing git identity `Mulaydm10` (no explicit designation was given),
flagged for confirmation at kickoff.

## Consequences
- Easier: any agent picking this up cold has a consistent read order and ID scheme (`ADR-####`,
  `Q-####`, `EXP-####`, `DEMO-####`) from day one, even before the event is real.
- Easier: the later agent-bus graft can append to `README.md` and `.gitignore` without needing
  Main Agent sign-off — neither is governance-locked (see `GOVERNANCE.md`).
- Harder: nothing is demoable yet — `DEMO.md` has no real scenario, `tests/` has no green baseline.
  This is intentional and stated explicitly rather than faked.
- Follow-up: kickoff must fill `COMPETITION.md` + `VISION.md` first (they gate almost everything
  else), then resolve `ADR-0002` alongside a first smoke test.
