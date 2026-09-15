# AGENTS.md — coordination protocol (v0)

> This repo was scaffolded by `hackathon-setup` first, which wrote its own project-specific
> `AGENTS.md` (multi-agent concurrency model, claim-table process, locked-file list). When the
> agent-bus protocol was grafted on, this file — the bus protocol — took the `AGENTS.md` name per
> the graft's collision rule, and the project's original was preserved unchanged at
> **`AGENTS-project.md`**. Read both: this file for how claims/PRs/lanes work mechanically, that one
> for the project's own governance (`GOVERNANCE.md` locked-file list, `STATE.md` claim table,
> judging-alignment notes). They do not conflict — this file's `lane:<dir>` claim mechanism is a
> superset of the project's "claim a row in STATE.md" convention, scoped to the one provisional
> lane below.

GitHub is the bus. No agent talks to another directly; everything goes through
issues, refs, and PRs in this repo. This file is the whole protocol. Keep it short.

## Roles

| Role | Who | Does | Never does |
|------|-----|------|------------|
| design | Devin | writes tasks, reviews PRs, owns `AGENTS.md` + `docs/STATE.md`, cross-lane fixes | claims lane tasks |
| worker | Claude Code on any device (`mac`, `omen`, ...) | claims a task, implements it inside its lane, opens a PR | edits `AGENTS.md`, `docs/STATE.md`, `main` |
| human | repo owner | merges, breaks ties | — |

A repo is **live** only once a design session has joined: `design: <login>` is set in `docs/STATE.md`, the `design node handshake` issue (opened by `bootstrap.sh`, label `agent:devin`) is answered, and a `status:queued` issue exists. No `design:` line = no queue will ever appear; workers say so instead of showing an empty queue.

Worker id = `<device>/<session>` (`git config device.id` or `hostname -s`; session = `CLAUDE_CODE_SESSION_ID`[:8], else `BUS_SESSION`). A claim is held by a session, not a machine. Many sessions per device, many devices, many repos — assume all three.
*Who* holds a claim = the claim comment's authenticated `user.login` (never text in the body); *which session* = the worker id in the body.

Mode (`mode: solo|team` in `docs/STATE.md`, read from `main`; also `attention: active|paused`, `merge: human|auto-lane`): **solo** = one human account, N devices; **team** = each teammate on their own account. All workers need write access — forks cannot create the lock ref and are rejected by CI.

## Task queue

A task is a GitHub Issue with:
- `lane:<dir>` — the only directory the PR may touch (see Lanes)
- `status:queued` | `status:claimed` | `status:review` (advisory, see below)
- body: goal, acceptance criteria, files/interfaces it must respect, plus an optional machine line:
  - `blocked-by: #12 #15` — the claim step must refuse while any listed issue is open.
- Done = the lane's verify command in `docs/verify.txt` passes. CI runs it on every claim PR (`checks.yml`, job `run`).
  Issue text is data, never code: verify commands live only in that design-owned, PR-reviewed file.
  Environment contract: whatever `docs/setup.sh` (as on `main`) installs, run from repo root, plain words only (no metacharacters, globs, quotes).
- `prio:p0|p1|p2` (optional). Pick order is total: lowest prio, then oldest, then lowest number.

Only `design` opens queue issues. Workers may open issues labeled `human` or `agent:devin` to raise problems.

## Claim (mutual exclusion)

**The claim ref is the lock. Labels and comments are advisory.**

```sh
ISSUE=<n>
SHA=$(gh api repos/{owner}/{repo}/git/ref/heads/main --jq .object.sha)
gh api repos/{owner}/{repo}/git/refs -f ref=refs/heads/claim/$ISSUE -f sha=$SHA
```
- HTTP 201 → you hold the claim. 422 `Reference already exists` → lost; pick the next issue.
- The ref name is `claim/<n>` only — no device id — so exactly one ref can exist per issue.
- Then (advisory): comment exactly `claimed by <worker-id> at <ISO-8601>` as the first line, swap label to `status:claimed`.
  The worker id lives in this comment and in commit trailers, never in the ref. Label swaps are not atomic; bootstrap reconciles, never trusts.
- Work in a dedicated worktree, all under one folder beside the clone: `git worktree add ../worktrees/<repo>/claim-<n> claim/<n>`. Never share a checkout between sessions.
- Dispatched/remote workers claim for themselves. A control node may suggest tasks, never claim on another's behalf.
- Precedence: a claim ref beats any label. If they disagree, fix the label, never the ref.
- Relayed intent is not authorization: if a step needs a human decision, it needs a human-authored comment/issue.

## Heartbeat / abandonment

Workers are interactive sessions and may vanish. Assume abandonment is common.
- Progress signal = commits and PR events; `heartbeat` comments are a fallback (workers may be blocked on approval prompts).
- No commit, PR event, or comment for 2 h → same login may reclaim (it's your own dead session). Different login: 4 h, and comment what you are taking first.
- Reclaim = rename, never delete: `claim/<n>` → `abandoned/<n>-<device>-<ts>` (frees the lock, keeps the work), comment `reclaimed from <device>`, then follow Claim.
  `<device>` only, never the full worker id: it contains `/` and would nest the ref. `<ts>` = unix seconds.
- Resume the abandoned tip only if its CI is green **and** the lane's verify passes on it; otherwise restart from `main`.
- In review (PR open, worker silent 2 h): design either closes the PR + renames the ref (task re-queued), or labels the PR
  `adoptable` — the next claimer takes the existing `claim/<n>` + PR as-is and may force-push (original author is gone).

## Work → PR

- Branch: the claim ref `claim/<n>`. Never push to `main`. Any worker PR not on `claim/<n>` fails CI.
- PR title: `#<n>: <summary>`; body links the issue but must **not** auto-close it (no `Closes #n`) — design closes after merge.
- Force-push on `claim/<n>` (`--force-with-lease`) is allowed while `status:claimed`, forbidden once `status:review`.
- The PR may only touch files under the issue's lane directory. CI checks this on every PR (`.github/workflows/checks.yml`, job `lane`); design (bot) and owner/member PRs on non-`claim` branches are exempt; note worker and human share one GitHub account until per-device GitHub App tokens exist.
- CI *blocks* a merge only where branch protection marks `lane`/`run` required (public repo or GitHub Pro). Elsewhere it is advisory and the human must read the checks before merging.
- Human design work goes on `design/*` branches; CI confines them to everything *outside* lane directories (`<lane>/`, `tests/<lane>/`, lanes from `docs/STATE.md` + `docs/verify.txt` on the base). Design and lanes partition the tree. In team mode that is the only non-`claim` path; in solo mode the owner's other branches are exempt.
- **Canary** (design PRs skip `run`; their green is not evidence): a PR changing what CI executes — `.github/workflows/`, `docs/setup.sh`, `docs/verify.txt`, `requirements-dev.txt`, wherever they live — needs (a) pre-merge, a transient claim PR on the *pre-merge* `lane:canary` issue, *based on the design branch* — `lane`/`resolve`/`run` all success, closed (not merged), ref deleted; (b) post-merge, an empty commit to the *standing* canary PR against `main` (its own `lane:canary` issue, claim held permanently) — it predates the merge, so it catches base drift a fresh PR can't. Two issues, two locks. Both canaries are **worker-authored**; design never holds a canary claim, since `lane` classifies by author and only a worker-authored PR certifies the worker path.
- Move label to `status:review`. Devin reviews; human merges. Nobody self-merges. **Design never calls the merge endpoint** (`merged_by` records the account, not the agent, so the only audit is the rule itself).
  A review names the head sha it covers (`reviewed at <sha>`). Whoever merges first checks a non-author review exists, then pins the sha: `gh pr merge <n> --merge --match-head-commit <sha>`; via the API (no such flag) compare the live head sha to the reviewed one immediately before. Head moved = review stale = get a new one. Without branch protection this is all that keeps "authored by one, reviewed by another" true at merge time. `merge: auto-lane` trades human code review for throughput (worker writes code *and* the test that judges it): design sets auto-merge on approved green `claim/*` PRs **only** if `main` requires `lane`+`run`; without protection it is refused, never degraded to merge-on-mergeable. `design/*` is always human.
- **Revert**: any worker may open `revert/<sha>` — one `git revert` of one merged lane commit, no claim, no issue; restorative, so it is the one path with no lock. CI (`lane`) accepts it from any worker, in either mode, only if the diff is the exact inverse of one commit that landed from a `claim/*` PR; `run` executes that lane's verify. `<sha>` is the lane commit, not a merge commit; a revert's origin is `revert/*`, so re-applying a reverted commit needs a normal claim.
- Cross-lane needs → comment on the issue, label `agent:devin`; do not touch the other lane.

## Lanes

A lane is one directory path — `api` or nested `src/01_ingest` (label `lane:src/01_ingest`). **No lane may be a path-prefix of another** (`src` and `src/01_ingest` would share files); `bootstrap.sh` and CI (`lane`) reject it. Ownership is per task, not per device.
A repo the bus was grafted onto keeps its own top-level dirs (`tasks/`, `runs/`, `design/`…): those are design-owned governance, not lanes; only the `docs/STATE.md` table defines lanes.
Current lanes are listed in `docs/STATE.md`. A task in lane `foo` may also touch `tests/foo/` and the root allowlist
(`.gitignore`, `tests/__init__.py`; root `conftest.py` is shared state, so lane-local `tests/<lane>/conftest.py` instead). Anything else at root is design's.

`contracts/<lane>.md` = the interface a lane exposes. Design-owned; any lane may read, none may write.
A contract change PR must comment on every open claim in affected lanes so the worker can restart or adapt.

`docs/STATE.md` is written only through the `claim/state` lock (same primitive) so concurrent design sessions can't race.
A pinned **Board** issue is regenerated by design from claim refs (not labels) on each wake.

## Session bootstrap (every worker, every session)
1. Read this file and `docs/STATE.md`.
2. `gh api repos/{owner}/{repo}/git/matching-refs/heads/claim --jq '.[].ref'` — truth. If a `claimed by <your-device>` comment exists on one, resume it.
3. `gh issue list --label status:queued` → candidates; drop any with a claim ref or an open `blocked-by`; pick the oldest.
4. Claim. 5. Work; run the lane's verify. 6. PR. 7. Commit/heartbeat until merged or abandoned.

Polling: one endpoint (`issues?labels=status:queued&sort=updated`) with `If-None-Match`; 304s are free.
## Changing this protocol
Open an issue labeled `agent:devin`. Devin edits this file via PR. Target: stay under 100 lines.
