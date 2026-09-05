# ADR-0002: Stack selection

**Status:** Proposed (open)
**Date:** 2026-09-05
**Related:** Q-0002

## Context
No stack has been chosen. The idea/track isn't even chosen yet (`VISION.md` is `TBD`), so
committing to a language/runtime now would be a guess dressed up as a decision. Whoever picks the
idea will have a much better sense of what stack actually fits (data-heavy? real-time? front-end
heavy? agent/LLM orchestration?).

## Options considered
`TODO(Mulaydm10)`: list real candidates once the idea firms up. Placeholder shape:
- Option A — e.g. a Python-based stack — pros/cons once there's an idea to evaluate it against
- Option B — e.g. a TypeScript/Node-based stack — pros/cons
- Option C — polyglot / split front-end+back-end — pros/cons

## Decision criteria
`TODO(Mulaydm10)`, but at minimum weigh:
- Team's existing familiarity (fastest path to a working demo under deadline)
- What the demo actually needs to show (latency-sensitive? data pipeline? UI-heavy?)
- Anything the event's hard rules constrain (see `COMPETITION.md` once filled — e.g. required
  platform, disallowed dependencies)

## Decision
**Not yet decided.** Do not infer a default from this repo's absence of config files — that
absence is deliberate, not an oversight.

## Consequences
Whoever resolves this ADR (flips Status to Accepted, fills in the chosen stack) must, **in the same
change**:
1. Update `CLAUDE.md`'s "Canonical commands" section (currently `TODO(Mulaydm10)`) with real
   build/test/run commands.
2. Land a green smoke test in `tests/` and remove the "no baseline" language from
   `tests/README.md`.
3. Extend `.gitignore` with stack-specific build-artifact ignores (still respecting the
   `!tests/**` negation guard already in place).

Landing the stack choice without the smoke test in the same change is exactly the failure mode this
ADR exists to prevent — a scaffold whose test command fails on first invocation trains everyone to
bypass the tooling forever.
