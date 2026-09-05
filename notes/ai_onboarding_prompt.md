> **LOCKED governing file.** Do not edit in place. See `GOVERNANCE.md`.

# AI onboarding prompt

Canonical prompt to paste into a fresh AI agent session (any vendor) to bring it up to speed on
this repo cold. Keep this in sync with `CLAUDE.md`'s read order if that changes.

---

You're joining the "Munich Expo Hackathon" repo mid-event (or before kickoff — check). Read, in
order: `CLAUDE.md` (or `AGENTS.md` if you're not Claude Code) → `STATE.md` → `VISION.md` →
`COMPETITION.md` → `GOVERNANCE.md` → `AGENTS.md` → the tail of `worklog.md` →
`experiments/experiment_log.md`.

Rules that matter immediately:
- If `COMPETITION.md` or `VISION.md` still say `TBD`, that's real — don't invent a deadline, rubric,
  or thesis to fill the gap. Say so and ask, or wait for kickoff.
- `STATE.md` overrides `worklog.md` if they disagree about what's true right now.
- Before touching a shared surface, claim a row in `STATE.md`'s work-claims table; release it the
  moment you stop, even if you didn't finish.
- Before you yield (done, blocked, or out of turn budget): update `STATE.md`, append to
  `worklog.md`, log any LOCKED-file edit in `GOVERNANCE.md`, release your claim.
- `DEMO.md` must stay runnable once any scenario exists in it — fixing a broken demo outranks a new
  feature.

Tell me what you're picking up and I'll point you at the right open item in `STATE.md`.
