# AGENTS.md — multi-agent concurrency model

Vendor-neutral twin of `CLAUDE.md`. Same repo, same rules, framework-agnostic wording. Do not
duplicate the ID scheme or read order here — see `CLAUDE.md` for those; keep the two consistent.

## Who owns which surface
`TODO(Mulaydm10)`: fill in real ownership once the team and tracks are known (roster lives in
`COMPETITION.md`). Until then, assume no surface is exclusively owned — claim before you touch.

## How to claim work
1. Add a row to `STATE.md`'s **Work-claims table**: your name/agent-id, the surface (file, feature,
   demo scenario), a timestamp, status `Claimed`.
2. Work.
3. The moment you stop — done, blocked, or out of turn budget — release the claim: delete the row
   or mark it `Released`. A stale claim blocks other agents worse than having no claim at all.

## The one hard rule
**Any agent finishing a unit of work updates `STATE.md` before yielding.** "Finishing" includes
blocking and running out of turn budget, not just completing successfully. An agent that walks away
without updating `STATE.md` leaves the next agent (human or AI) reconstructing state from git diffs.

## Conflicting work
Never silently pick a winner between two agents' conflicting work on the same surface. Record the
conflict under `STATE.md`'s **Blocked** section, keep both versions discoverable (e.g. both branches
or both file variants named, not one overwritten), and let a human or the Main Agent (see
`GOVERNANCE.md`) resolve it.

## Locked files
See `GOVERNANCE.md` for the full breakdown. Short version: `COMPETITION.md`, `VISION.md`,
`design/README.md`, `experiments/README.md`, `research/README.md`, `notes/ai_onboarding_prompt.md`,
`logs/README.md`, and `tests/README.md` are LOCKED — edit only via the Main Agent, and log the edit
in `GOVERNANCE.md`'s audit table. This file, `CLAUDE.md`, `STATE.md`, and `DEMO.md` are deliberately
**not** locked because their entire value is staying current.
