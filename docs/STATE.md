# STATE.md — current shape of the project (written by design only, via `claim/state`)

## Purpose
<one paragraph: what this repo is building, and the deadline>

mode: solo
attention: active
merge: human
<!-- design: <login>   set by design on join, via claim/state; absent = repo not live, workers report "no design node" -->
<!-- mode: solo | team.  attention: active | paused (workers' cross-repo pick order skips paused repos; design sessions do not wake).
     merge: human | auto-lane (auto-lane = you give up human code review of lane PRs for throughput; design sets auto-merge on green + approved claim PRs; refused unless main requires lane+run; design/* always human).
     CI reads these from the live tip of the base branch and workers from `main`, never from a PR head: a PR must not relax the enforcement it is judged by. -->

## Lanes

| lane | directory | purpose | contract |
|------|-----------|---------|----------|
| `lane:canary` | `canary/` | two standing issues: post-merge canary (permanent claim, draft PR) and pre-merge canary (transient claim per workflow PR) | — |
| `lane:src/00_scaffold` | `src/00_scaffold/` | <purpose> | `contracts/src/00_scaffold.md` |
<!-- bootstrap.sh appends one row per lane you pass it; design edits after that. A lane may be a nested path (`src/01_ingest`); no lane may be a prefix of another. -->

## Verify environment
`docs/setup.sh` (design-owned; CI runs the copy on `main`; changing it needs a canary like any workflow change). Default: `python3` + `requirements-dev.txt`. Workers run the same script once per worktree.
<!-- change both this line and requirements-dev.txt / docs/verify.txt if the project is not Python -->

## Decisions
- Lock = `claim/<n>` ref via git refs API (201/422). Labels advisory; refs beat labels.
- Lane + per-lane verify (`docs/verify.txt`) are CI jobs in one workflow (`checks.yml`: lane → resolve → run).
- Reclaim renames to `abandoned/…`; resume only on green CI + passing verify.
- Worker id = device/session; sessions hold claims, machines don't. Worktree per claim.
- Contracts in `contracts/<lane>.md`, design-owned.

## Known gaps
- Branch protection (`lane` + `run` required, PR-only `main`) needs a public repo or GitHub Pro/org. Without it checks are advisory — see docs/SETUP.md.
- One GitHub account for all workers (solo mode) = one API rate bucket; GitHub App with per-device tokens before ~20 nodes.
- Actions minutes are one pool per repo; check quota before a team event.

## Log
- 2026-09-05: repo created from agent-bus-template; bootstrap run (mode=solo).
