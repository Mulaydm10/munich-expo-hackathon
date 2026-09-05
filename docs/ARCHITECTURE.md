# Agent Bus — architecture

Multi-agent hackathon pipeline where **GitHub is the only shared medium**. Nodes never talk directly; they read and write issues, refs, and PRs. The protocol itself is `AGENTS.md`; this document explains the system around it.

## Nodes

| Node | Runtime | Lifecycle | Role |
|------|---------|-----------|------|
| Devin (design) | own VM, woken by GitHub events / Automation | stateless per wake | plans, cuts tasks, reviews PRs, owns protocol + state |
| Claude Code (worker) | any laptop/server, N sessions per device | interactive, dies without notice | claims one task, implements inside a lane, opens a PR |
| Human | GitHub UI / terminal | — | merges, breaks ties, provisions devices |

Both agent types are *stateless per session*: everything they need is in the repo on wake. That is what makes "add a device" and "add a teammate" the same operation.

## Data model (all on GitHub)

```
Issue  (task)      labels: lane:<dir>  status:*  prio:*     body: blocked-by:   (verify cmd per lane in docs/verify.txt)
Ref    claim/<n>   THE LOCK. created via git refs API (201 = held, 422 = lost). doubles as work branch
Ref    abandoned/<n>-<device>-<ts>   renamed claim; preserved evidence (device only: worker ids contain `/`)
Ref    claim/state                   lock for docs/STATE.md (design-only)  [proposed, not yet exercised]
PR     from claim/<n> → main         title "#<n>: …", no auto-close
Files  AGENTS.md  docs/STATE.md  docs/verify.txt  contracts/<lane>.md  .github/workflows/checks.yml
```

Truth ordering: **refs > CI checks > labels/comments**. Anything advisory is reconciled from refs, never the reverse.

## Flows

### Task lifecycle
```
design cuts issue (lane, prio) ─▶ status:queued
worker bootstrap: refs first, then queued issues, drop claimed/blocked, pick by (prio, age, number)
worker POST refs claim/<n> ─▶ 201 ─▶ comment "claimed by <dev>/<sess> at <ts>" ─▶ status:claimed
worker: worktree ▸ implement ▸ run verify ▸ push ▸ PR ─▶ status:review
CI (checks.yml): lane (paths ⊆ lane ∪ tests/lane ∪ root allowlist) → resolve (cmd from docs/verify.txt @ base-branch tip) → run (needs both; deps from base; contents: read only)
design reviews against contract + verify ─▶ human merges ─▶ design closes issue, updates STATE
```

### Abandonment
No commit / PR event / comment on a claim for 2 h → any worker renames `claim/<n>` → `abandoned/<n>-…`, then claims fresh. Resume the abandoned tip only if CI is green **and** verify passes on it; otherwise restart from `main`. A worker stuck on a permission prompt looks dead — hence the generous window and commits (not heartbeats) as the primary liveness signal. The 2 h figure is argued, not measured: no task has yet run long enough to exercise it.

**Review stage.** Force-push is forbidden once `status:review`, so a worker that dies with a PR open would otherwise wedge the task. After the same 2 h silence, design either closes the PR and renames the ref (task re-queued) or labels the PR `adoptable`; the adopter takes `claim/<n>` + PR as-is with force-push re-permitted.

### Cross-lane change
Worker never touches another lane. It comments on its issue + labels `agent:devin`. Design edits `contracts/<lane>.md` in a design PR, cuts follow-up issues into affected lanes, and comments on every open claim in those lanes so in-flight workers can adapt or restart.

### Design concurrency
Two Devin wakes can overlap. Anything design writes that is not a new issue/PR (STATE.md, Board) goes through the `claim/state` lock — same primitive as workers, no second mechanism.

## Enforcement map

**Mechanical** — holds regardless of who participates:

| Rule | Enforced by |
|------|-------------|
| one worker per task | `claim/<n>` ref uniqueness (server-side atomic) |
| verify never runs unreviewed config | `resolve` reads `docs/verify.txt` + `requirements-dev.txt` from the live base-branch tip (not `base.sha`, which goes stale); `run` has only `contents: read` and needs `lane` |
| checks report on every PR | `checks.yml` jobs always run (no job-level skip) |

**Mechanical only with branch protection** (public repo, or GitHub Pro/org for private — free private repos cannot enable it). Without it these are *reported* but not *blocked*, and the human must read checks before merging:

| Rule | Enforced by |
|------|-------------|
| PR stays in lane | `lane` job as required check |
| task is done | `run` job as required check |
| workers never write `main` / protocol files | protected `main` (PR-only) + `lane` |
| worker PRs use `claim/<n>` | `lane` fails other branches for non-design, non-owner authors |

**Advisory** — cheap to violate, harmless because refs are truth: priority order, heartbeat, Board, no-auto-close.

**Unenforced but load-bearing** — nothing mechanical backs these; if an incident happens, it starts here:
- *nobody self-merges* — without branch protection this is the only thing between an agent and `main`.
- *`claim/state`* — a direct edit to `docs/STATE.md` is a lost update to the file that describes the system.
- *worktree-per-claim* — two sessions in one checkout corrupt each other quietly, invisible to the repo.
- *a human merges design PRs* — `design/*` may edit `.github/`, i.e. the checks that constrain everyone. This is the trust root: with branch protection it is a reviewed change; without it, every other rule is only as firm as this one.
- *canary with every workflow change* — "workflow" means what CI executes, not a path: `docs/setup.sh` and `docs/verify.txt` are read from the base branch and run by `run`, so they are workflow-equivalent in power despite living under `docs/`, and carry the same canary duty. — a job that can skip is not evidence until something has made it not skip. Four workflow PRs in a row were green with `run=skipped`. Two shapes of gap, two canaries:
  1. **Pre-merge**: a claim PR on the standing `lane:canary` issue, **based on the design branch** (GitHub runs the workflow from the PR's merge commit, so it exercises the *new* checks). Merge the workflow PR only after `lane`/`resolve`/`run` = success. Close it on merge — GitHub retargets a PR to `main` when its base branch is deleted, so left open it silently tests something else.
  2. **Post-merge**: one permanent canary claim PR against `main`; push an empty commit to it after every workflow merge. It predates the merge, so it has the base-drift condition a fresh PR can never have (the stale `base.sha` bug hit an in-flight PR twice on the prototype and no pre-merge canary could have shown it). Red here = the workflow broke in-flight work.
  A pre-merge canary proves the workflow runs; only a canary that predates the merge proves it didn't break what was already open.
  Both canaries are **worker-authored** (the role table's "design never claims lane tasks" has no exception). `lane` branches on author: `DESIGN_BOT` and, in solo mode, OWNER/MEMBER take a different path than a worker; a design-authored canary would certify design's path and go green for a reason unrelated to what it proves — the same failure shape one level up.
  Lane scaffolding (`canary/`, `tests/canary/`) is outside the `design/*` allowlist, so design creates lanes on a plain `DESIGN_BOT` branch — that is what the bot's any-branch exemption is for.

Status of this repo: private, free plan → the middle tier is advisory here. The template setup checklist must say so.

## Deployment modes (team mode not yet exercised)

| | solo | team |
|---|---|---|
| humans / GitHub accounts | one | several, each their own, all with **write access** (forks cannot create the lock ref; CI rejects fork PRs) |
| workers | N devices × M sessions, all one login | each teammate's own Claude Code(s), own login |
| claim identity | *who* = comment `user.login` (authenticated); *which session* = `<device>/<session>` in the body. Same in both modes, so flipping mid-hackathon rewrites nothing | |
| reclaim window | same login: 2 h. Different login: 4 h + a comment naming what is taken (a record, not a handshake — the holder may be blocked and unable to reply) | |
| non-`claim/` branches | `DESIGN_BOT` any branch; owner any branch; `design/*` confined to non-lane paths | `DESIGN_BOT` any branch; humans only `design/*`, confined to non-lane paths (lanes from STATE.md/verify.txt on base) |
| branch protection | optional (advisory checks) | required: `lane` + `run`, PR-only `main` |
| rate limits | API: shared bucket → GitHub App later | API: per-account, solved. Actions minutes: still one pool per repo — check quota before the event |
| provisioning | tool allowlist per device | per human per device; an unprovisioned worker looks dead |
| Board | advisory | load-bearing: the only shared view of who holds what |

`mode: solo|team` lives in `docs/STATE.md`. CI reads it from the **base branch tip** and workers from `main`, for the same reason `docs/verify.txt` is read from base: a PR must not be able to relax the enforcement it is judged by. Flipping it is a design PR.

## Deliverables (what gets reused per hackathon)

1. **Template repo** (not yet extracted; this repo is the prototype) — `AGENTS.md`, `checks.yml`, label set, issue template with `lane/blocked-by` fields, `docs/verify.txt`, `docs/STATE.md` + `docs/ARCHITECTURE.md` skeletons, `contracts/`. Setup checklist: "Use this template", fill STATE.md lanes + verify env, set repo var `DESIGN_BOT` if not Devin, **enable branch protection on `main` with `lane` + `run` required** (or record that checks are advisory).
2. **Devin playbook + Automation** — trigger: PR opened/synchronized, issue labeled `agent:devin`, or schedule. Steps: read AGENTS/STATE → review PR (lane, contract, verify) → comment → handle `agent:devin` issues (cut tasks, edit contracts) → regenerate Board → update STATE via `claim/state`.
3. **Claude Code plugin** (owned by the human) — bootstrap, claim, worktree, verify, PR, heartbeat; keyed per `owner/repo` (ETag cache, watcher). Provisioning: `git config device.id` + one-time tool allowlist per device. Worker-side design: `docs/WORKER.md`.

Exercised so far: lock (201/422), lane check, one task end to end (#3 → PR #5). Proposed only: 2 h reclaim, review-stage adoption, `claim/state`, Board, contracts.

## Scaling limits and the planned fix

| Symptom | Threshold | Fix |
|---------|-----------|-----|
| all workers share the human's token: one rate bucket, no per-device audit, CI can't tell worker from human | ~10–20 active nodes | GitHub App; per-installation tokens per device |
| polling cost | many idle nodes | single endpoint + `If-None-Match` (304s are free); later a webhook relay |
| design bottleneck on cross-lane changes | many lanes | contracts written up front; lanes sized so cross-lane is rare |
| queue starvation on `blocked-by` chains | deep DAGs | keep DAG shallow; design cuts tasks only when unblocked |

## Non-goals
No database, no message queue, no custom orchestrator, no live agent-to-agent chat. If a need appears that GitHub cannot express, the answer is a new file convention in the repo, not a new service.
