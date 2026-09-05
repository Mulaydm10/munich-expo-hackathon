> **LOCKED governing file.** Do not edit in place. See `GOVERNANCE.md`.

# research/ — prior art + open questions

## prior_art.md
Append-only log of things that already exist and are relevant (competing products, papers,
open-source projects) — feeds `VISION.md`'s "why it isn't already solved" section and
`notes/judging_alignment.md` novelty considerations. Never edit or delete a past entry.

## open_questions.md
Append-only log of open questions, each with a `Q-####` ID (zero-padded, monotonically increasing,
never reused). A question is closed by adding a new entry that says which decision/ADR/fact
resolved it — not by deleting the original entry.

Current open questions registered at scaffold time:
- **Q-0001** — the project thesis (`VISION.md`) is entirely unfilled; no idea has been chosen yet.
- **Q-0002** — the stack is undecided; see `design/decisions/ADR-0002-stack-selection.md`.
