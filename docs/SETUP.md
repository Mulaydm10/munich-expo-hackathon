# SETUP — checklist for a new repo

## 1. Repo
- [ ] Create from template, run `./bootstrap.sh --mode <solo|team> <lanes…>`.
- [ ] **Branch protection** needs a public repo or GitHub Pro/org. bootstrap tries and tells you. Without it, `lane`/`run` are advisory: the human reads the checks before merging. Team mode without protection is not recommended.
- [ ] Actions minutes: one pool per repo. Check `Settings → Billing` before a team event.
- [ ] Not Python? Edit `docs/setup.sh` (runtime install; CI runs the `main` copy) and `docs/verify.txt`. Design PR **with a canary**: those files are what CI executes, so they carry the same duty as `checks.yml`.
- [ ] Merge policy: `merge: human` (default) or `merge: auto-lane` in `docs/STATE.md`. **auto-lane means you are trading code review for throughput**: `lane` bounds which files change, `run` executes tests the same worker wrote — no human sees the product before it ships; the safety net is the `revert/<sha>` path in AGENTS.md. Requires `allow_auto_merge` on the repo *and* `lane`+`run` required on `main`; design refuses to set it otherwise (a free private repo would merge-on-mergeable, which is not the same thing). `design/*` PRs always wait for a person. Without protection, merge in batches at a cadence you set, not per PR.
- [ ] Several projects at once: one repo, one design session, one `STATE.md` each — no shared state. `attention: paused` in a repo's STATE.md tells workers' cross-repo pick order and the design Automation to skip it.

## 2. Design node (Devin)
- [ ] Repo connected in Devin's GitHub integration (the bot login must match repo variable `DESIGN_BOT`; default `devin-ai-integration[bot]`).
- [ ] Nothing wakes Devin until you ask. In the Devin session where you discuss the project, say `join <owner/repo>`: that session becomes the repo's design node and sets up (approval-gated) one Automation bound to itself — triggers `claim/*`/`revert/*` PR opened/synchronize, merged PR, issue comment, `agent:devin`, plus a 2-hourly sweep for stale claims and the Board. One Automation per repo, one session per repo; pause with `attention: paused`.
- [ ] Any Devin session already knows this protocol via the org knowledge note "Agent Bus protocol"; in a bus repo just say "act as design for <owner/repo>".
- [ ] Design never claims lane tasks, never holds the canary claim, never merges.
- [ ] You merge: only with a non-author review present, pinned to the reviewed sha — `gh pr merge <n> --merge --match-head-commit <sha>` (the review names the sha). Head moved → ask for a fresh review.

Live = design answered the `design node handshake` issue `bootstrap.sh` opened, `design:` is set in `docs/STATE.md`, and a `status:queued` issue exists. Until then workers report "no design node", not an empty queue.
Grafting onto an existing repo (scaffolded elsewhere): copy `AGENTS.md`, `docs/`, `.github/workflows/checks.yml`, `bootstrap.sh`, `canary/`, `contracts/` in one commit, keep the project's own `AGENTS.md` as `AGENTS-project.md`, then run `bootstrap.sh` with the *work* directories as lanes — nested paths are fine (`src/01_ingest src/02_train`), never one a prefix of another. Existing top-level dirs (`tasks/`, `runs/`, `design/`…) stay design-owned, not lanes.

## 3. Worker devices (Claude Code)
Per human account (one in solo mode, one per teammate in team mode):
- [ ] Write access to the repo. Forks cannot create the lock ref; CI rejects fork PRs.
- [ ] `gh auth login` as that account.

Per device (the plugin repo https://github.com/Mulaydm10/agent-bus-plugin is private: the account needs read access to it, or make it public):
```bash
git clone https://github.com/Mulaydm10/agent-bus-plugin && cd agent-bus-plugin
./bootstrap-device.sh --device <short-name>    # e.g. omen, mac; idempotent, re-run to update
```
It checks `git`/`gh`/`claude`/a hash tool, sets `git config --global device.id`, installs or updates `bus@agent-bus`, and prints the permissions block below (`--write-permissions` merges it, backing up `settings.json.bak`). Manual equivalent: `claude plugin marketplace add <url> && claude plugin install bus@agent-bus --scope user`.
- [ ] Permissions — a plugin cannot pre-seed these, and a worker blocked on a prompt looks dead to everyone else. `~/.claude/settings.json` is yours and shared with every other Claude Code use on the machine, so the plugin's device bootstrap *prints* this block and the path; paste it yourself (or opt in to `--write-permissions`, which backs up to `settings.json.bak` first):
```json
{ "permissions": { "allow": [
  "Bash(gh api:*)", "Bash(gh issue:*)", "Bash(gh pr:*)",
  "Bash(git fetch:*)", "Bash(git push:*)", "Bash(git worktree:*)"
] } }
```
- [ ] Register the clone: `/bus:join` in each project checkout (registry is `owner/repo<TAB>/local/path`; the worker needs a working tree, not just a name). `/bus:status` then shows the next task across all registered repos; `attention: paused` repos are skipped.
- [ ] `/bus:doctor` in a Claude Code session in the repo — every command shape must report `ok` **before** the machine claims anything.
- [ ] Skills: `/bus:claim`, `/bus:verify`, `/bus:pr`, `/bus:release`, `/bus:status`, `/bus:doctor`. The SessionStart hook reports state and never auto-claims.

## 4. Solo vs team

| | solo | team |
|---|---|---|
| accounts | one (yours) | one per teammate, all with write |
| claim identity | comment `user.login` = you; session in body | `user.login` = the teammate |
| reclaim window | 2 h (your own dead session) | 4 h across logins, comment first |
| non-`claim/` branches | owner may use any | humans only `design/*` (non-lane paths) |
| branch protection | optional | required |
| API rate limit | shared bucket → GitHub App later | per account |

Switching mid-event is a one-line design PR (`mode:` in `docs/STATE.md`); nothing on the worker side changes.

## 5. Two devices, two projects (solo)
Once per device: bootstrap-device.sh (`--device omen` / `--device mac`), paste permissions, `/bus:doctor`.
Per project: `gh repo create <p> --template Mulaydm10/agent-bus-template --private --clone && cd <p> && ./bootstrap.sh --mode solo <lanes…>`; connect it in Devin's GitHub integration; open a Devin session and say `join <owner/repo>`. Then on **each** device clone `<p>` and run `/bus:join` inside it.
From then on any Claude session on either device, in any directory, runs `/bus:status` (both queues) and `/bus:claim` (best task across both, `prio:` first); one issue → one `claim/<n>` ref, so omen and mac never collide even on one account. Pause a project with `attention: paused` in its `docs/STATE.md`.
Canary issues are not in the queue (`bootstrap.sh` creates them without `status:queued`; plugin ≥ v0.2.2 also skips `lane:canary` in pick order): a worker claims the standing canary deliberately, by number (`/bus:claim <n>`), as a one-off duty — see §6 step 2.

## 6. First hour
1. Design fills `docs/STATE.md` Purpose, one contract per lane, 5–10 queued issues with goal + acceptance.
2. A worker claims the *standing* canary issue and opens the permanent draft canary PR (the *pre-merge* canary issue stays unclaimed until a workflow PR needs it).
3. Workers start; human merges as PRs go green; design closes issues after merge.
