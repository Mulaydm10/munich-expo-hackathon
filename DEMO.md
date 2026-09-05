# DEMO.md — judge-facing demo script

Not a feature list. This is the exact click-by-click / command-by-command happy path a team member
runs in front of judges. **This file stays runnable at all times once any scenario exists in it** —
fixing a broken demo outranks building a new feature. Retire scenarios by marking them
`superseded by DEMO-####`; never delete a scenario entry outright.

## Why there is no scenario yet
No demo scenario exists yet because there is no build yet — the idea, stack, and task breakdown are
all still open (see `VISION.md`, `design/decisions/ADR-0002-stack-selection.md`). Do not write a
placeholder scenario that pretends to demo something that doesn't exist; that's worse than an empty
section because the next agent will try to run it.

`TODO(Mulaydm10)`: once there is a first walking-skeleton build, add `DEMO-0001` here using the
template below, and verify it actually runs before marking Status: Ready.

---

## Template (copy this per scenario)

### DEMO-0001 — `<one-line name>`
**Status:** `<Draft | Ready | Known-broken | superseded by DEMO-####>`

**Preconditions:**
- `TODO`: what must be true/running before starting (services up, env vars set, seed data loaded)

**Steps:**
1. `TODO`
2. `TODO`

**Expected output per step:**
1. `TODO`
2. `TODO`

**Known-broken edges to avoid:**
- `TODO`: anything a judge might click/type that breaks the happy path — steer around it live

**Reset procedure:**
`TODO`: the exact steps to get back to a clean, demoable state after a failed run-through, mid-event,
fast. This is the part of the script teams need most and skip most — do not leave it as an
afterthought once written.
