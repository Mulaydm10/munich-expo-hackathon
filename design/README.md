> **LOCKED governing file.** Do not edit in place. See `GOVERNANCE.md`.

# design/ — architecture + ADR process

Architecture decisions live as ADRs in `design/decisions/`, one file per decision, numbered
`ADR-####` (zero-padded, monotonically increasing, never reused even if a decision is superseded
or rejected).

## Process
1. Copy `design/decisions/ADR-0000-template.md` to the next `ADR-####-<slug>.md`.
2. Fill in Status (`Proposed` / `Accepted` / `Rejected` / `Superseded by ADR-####`), Context,
   Decision, Consequences.
3. Reference the ADR's ID anywhere it's relevant (worklog entries, STATE.md, code comments) so
   `grep -rn 'ADR-####' .` recovers the full trace.
4. An ADR file itself is a **plain** file (not locked) — anyone can open, discuss, and land one.
   What's locked is this process description, not the individual decisions.
5. Never delete or renumber an ADR. Change its Status instead.

## Existing ADRs
- `ADR-0000-template.md` — the template, not a real decision
- `ADR-0001-scaffold-bootstrap.md` — documents this scaffold's own governance/ID/module choices
- `ADR-0002-stack-selection.md` — open; the stack was undecided at scaffold time (`Q-0002`)
