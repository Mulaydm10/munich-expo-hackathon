# Open questions

Append-only. Each question gets a `Q-####` ID, never reused. Close a question by adding a new entry
noting what resolved it — do not delete or edit the original.

### Q-0001 — What is this project? (thesis unfilled)
**Opened:** 2026-09-05
**Status:** Open
`VISION.md` is entirely `TBD` — no idea, target user, or demo-time success bar has been chosen yet.
Resolves when `VISION.md` is filled in.

### Q-0002 — What stack do we build on?
**Opened:** 2026-09-05
**Status:** Open
See `design/decisions/ADR-0002-stack-selection.md`. Resolves when that ADR's Status flips to
Accepted, with canonical commands + a green smoke test landed in the same change.

### Q-0001 — CLOSED 2026-09-05
`VISION.md` filled: FlexGrid — quantile forecasting → thermal/phase envelope → deadline-feasible
scheduling → portfolio pooling of Germany's real charge-point registry, sized so a flexibility
promise is never broken. Non-goals recorded there (no V2G, no live market participation, no OCPP,
no federated learning in v1).

### Q-0002 — CLOSED 2026-09-05
`ADR-0002` Accepted: Python 3.12 everywhere, FastAPI + Jinja2 + CDN front-end, no node toolchain.
Canonical commands landed in `CLAUDE.md`, `docs/verify.txt` rewritten to the nine real lanes, one
green smoke test per lane (`python3 -m pytest tests -q` → 10 passed).

### Q-0003 — When exactly is the submission deadline?
**Opened:** 2026-09-05
**Status:** Open — **blocking, and the answer can only shrink the build window**
Two official readings conflict: the on-site day-of schedule says "17:00 final submissions" on Sun
20 Sep 2026 (Europe/Berlin); Devpost states the window as 1–20 Sep 2026, which alone reads as end
of day. We plan against the earlier (17:00 CEST). Resolves when an organizer states one in
writing. `TODO(Mulaydm10)`: ask via contact@munichtechexpo.com or Devpost Discussions — no
organizer contact has been made from this repo, and none may be without explicit authorization.

### Q-0004 — Can a solo participant submit, or is a team of 2–6 required?
**Opened:** 2026-09-05
**Status:** Open — affects whether a team must be formed before submitting
Devpost states teams of 2–6 members; the rules page also allows individuals. Our registration is
solo and no team exists. Resolves on an organizer answer; if teams turn out to be mandatory, team
formation happens on-site Sun 20 Sep 10:00 per the schedule, which is a real risk to plan for.

### Q-0005 — Which sponsor credit offer is current (ElevenLabs: 3 months or 1)?
**Opened:** 2026-09-05
**Status:** Open — low impact
Registration pages advertise 3 months of ElevenLabs Creator; the linked ElevenLabs Hacker Guide
says 1 free month. Only matters for the `src/voice` lane's budget. Not blocking: that lane must
degrade to text with no key at all (`contracts/src/voice.md`).

### Q-0006 — Are there rules on AI-assisted code, dependency licences, or API limits?
**Opened:** 2026-09-05
**Status:** Open
No published rule found on any official page. This repo is largely agent-written, so the answer
matters for how the submission is worded. Until answered: disclose AI assistance voluntarily in the
submission, keep every dependency permissively licensed, and keep the "created during the hackathon
period" rule satisfiable by disclosing anything pre-existing.

### Q-0007 — Do remote participants receive the organizers' sample charging dataset?
**Opened:** 2026-09-05
**Status:** Open — deliberately **not** blocking
The challenge page says the anonymized sample sessions are handed out at the on-site briefing (Sun
20 Sep 13:00 CEST); nothing is downloadable. The build therefore treats it as an optional
validation set, never a dependency (`contracts/src/fleet.md`). If it arrives, the fleet lane
back-tests its synthetic sessions against it and reports the gap honestly.

### Q-0008 — Which participation mode, and is an attendee ticket held?
**Opened:** 2026-09-05
**Status:** Open — **human-only, and the only item here that can lose the submission outright**
Registration `HKP-2026-IC3JYX` exists, but participation mode is unset: the selector is on
https://munichtechexpo.com/apply/hackathon (online-only free / hybrid / on-site, the latter two
needing an attendee ticket), not in the flow we completed. No ticket is held.

This is not a fact awaiting an organizer's answer like `Q-0003`–`Q-0007`; it is an action awaiting
Mulaydm10, and it is tracked here rather than in `COMPETITION.md`'s facts table precisely because a
line in a facts table reads as settled. Two things make it worse than its neighbours: a wrong
deadline costs planning slack whereas no valid mode costs the submission, and tickets can sell out,
so the real deadline is unknown and earlier than 20 Sep.

Interacts with `Q-0004` (if teams of 2–6 turn out to be mandatory, team formation is an on-site
event at Sun 20 Sep 10:00 — which is unreachable on an online-only mode) and with `Q-0007` (the
organizers' sample dataset is handed out at the on-site briefing). Neither the build nor the demo
depends on the outcome, by design; the right to submit does.

`TODO(Mulaydm10)`: pick a mode and, if it is hybrid or on-site, buy the ticket. No agent can do
this, and no agent may contact the organizers about it without explicit authorization.
