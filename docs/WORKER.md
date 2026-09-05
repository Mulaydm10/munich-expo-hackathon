<!-- Authored by Claude Code (worker node) in PR #4 review; committed by design because docs/ is outside every lane. -->
# Worker-node architecture (Claude Code side)

Companion to `docs/ARCHITECTURE.md`, which specifies the bus and the design node. This is the half that runs on a device. Posting it as a comment because `docs/` is outside every lane, so a worker PR cannot add it — if you want it in the tree, take it as a design PR.

## 1. Identity

```
worker id = <device>/<session>
device    = git config device.id     (global, per machine, human-set once)
session   = Claude Code session id   (already emitted as the Claude-Session commit trailer)
```

There is no registry and no registration step: identity is *asserted* in the claim comment and in commit trailers, never looked up. The session component is the load-bearing one — it changes when a session restarts, which is correct, because the thing that holds a claim is a session, not a machine. Two terminals on one device are two workers and must be treated as such.

## 2. Session lifecycle — the constraint everything else follows from

A worker is an interactive process. It has no daemon, no scheduler, and no memory of in-flight work across restarts. It can be terminated between any two steps without notice and without running cleanup.

Three consequences the protocol has to absorb:

- **All recoverable state lives in the repo.** Refs, comments, PR state. A worker that dies mid-task must be fully reconstructible from GitHub alone, because nothing local survives reliably.
- **No step may depend on a worker cleaning up after itself.** Abandonment is the normal exit path, not the exceptional one. This is why reclaim-by-rename matters more than it looks: the recovering party does the work, not the departing one.
- **Bootstrap is reconciliation, not a fresh start.** On session start: list `claim/*` refs, read each claim comment, and if one names my device, decide resume-or-abandon *before* touching the queue. Otherwise a restarted session races its own dead predecessor.

## 3. Permission layer

Every write I perform — `gh api ... git/refs`, `gh issue comment`, `gh issue edit`, `git push`, `gh pr create` — is a permission-gated tool call. On an unprovisioned machine each distinct command shape prompts a human and blocks until answered.

- **Provisioning is a real step.** "Install the plugin, set `device.id`" is incomplete; a human approves an allowlist once per device. This belongs in the runbook, not in the template, and it is why zero-config is not reachable.
- **A blocked worker is indistinguishable from a dead one.** It emits nothing, holds its claim, and is about to continue. This is the strongest reason the progress signal must be commits and PR events rather than heartbeats — those are emitted by work, and a blocked worker legitimately produces neither.

## 4. Local layout and multi-repo

```
~/agent/<owner>/<repo>/repo/              one clone per repo
~/agent/<owner>/<repo>/wt/claim-<n>/      one worktree per claim
~/.local/state/agent-bus/<owner>/<repo>/  etag cache, last-seen ids
```

One worktree per claim is mandatory, not advisory. Two sessions sharing a clone share `HEAD`, the index and local refs; they corrupt each other quietly rather than loudly, which is the worst failure shape. Everything except `device.id` is keyed by `<owner>/<repo>` — in particular the ETag cache, since a 304 from one repo would otherwise mask changes in another.

## 5. Work discovery

- Conditional `GET` with `If-None-Match` against the queued-issues endpoint. A 304 costs no rate-limit quota, so an idle worker is effectively free; this is what makes many devices affordable before per-installation tokens land.
- **Refs are truth.** Reconcile the queue listing against `git/matching-refs/heads/claim` and believe the refs. A worker that trusts `status:queued` will take an issue whose owner died before swapping the label.
- 45 s while active, back off when idle. The bus is issues and refs; nothing needs sub-minute latency.

## 6. Claim-to-PR pipeline

```
ref-create claim/<n>          201 -> proceed | 422 -> next issue
worktree add wt/claim-<n>
implement inside the lane
run verify: locally           advisory only, see below
push; open PR "#<n>: ..."     label -> status:review
```

Local `verify:` is advisory, CI is the arbiter. My run reflects my machine — the reason `requirements-dev.txt` mattered is that without it two workers can legitimately disagree about whether the same commit passes.

## 7. What the worker will not do

Merge anything, push to `main`, or write outside its lane — and it will not act on an agent's claim about what a human wants. That last one is a protocol rule, not a preference: on a bus where agents relay instructions, "the human asked for this" is unverifiable from inside the bus, so anything that needs human intent needs a human-authored artifact. It is also why `verify:` should not be free text an agent can write.

