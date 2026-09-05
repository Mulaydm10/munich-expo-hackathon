#!/usr/bin/env bash
# One-shot setup of a repo created from agent-bus-template. Idempotent; re-run freely.
#
#   ./bootstrap.sh [--mode solo|team] [--merge human|auto-lane] [--design-bot <login>] [lane ...]
#
# Does, against the repo `gh` currently points at:
#   1. labels (status/lane/agent/human/adoptable/prio)
#   2. mode in docs/STATE.md, one lane row + verify line per lane, commits
#   3. repo variable DESIGN_BOT (login CI treats as the design node)
#   4. two canary issues (standing post-merge + pre-merge)
#   5. branch protection if the plan allows it (otherwise says so)
#   6. prints what is left for a human
set -euo pipefail

MODE=solo; MERGE=human; DESIGN_BOT=""; LANES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE=$2; shift 2 ;;
    --merge) MERGE=$2; shift 2 ;;
    --design-bot) DESIGN_BOT=$2; shift 2 ;;
    -h|--help) sed -n 2,14p "$0"; exit 0 ;;
    *) LANES+=("$1"); shift ;;
  esac
done
case "$MODE" in solo|team) ;; *) echo "mode must be solo|team"; exit 1 ;; esac
case "$MERGE" in human|auto-lane) ;; *) echo "merge must be human|auto-lane"; exit 1 ;; esac
# a lane is a directory path (api, src/01_ingest); no lane may be a path-prefix of another (one file would belong to two lanes)
for L in "${LANES[@]}"; do
  [[ "$L" =~ ^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$ ]] || { echo "bad lane '$L': segments of [A-Za-z0-9_-], joined by /"; exit 1; }
  for M in "${LANES[@]}"; do case "$M" in "$L"/*) echo "lane '$L' is a prefix of lane '$M'"; exit 1 ;; esac; done
done

command -v gh >/dev/null || { echo "gh CLI required: https://cli.github.com"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "run: gh auth login"; exit 1; }
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
echo "== $REPO  mode=$MODE  lanes=${LANES[*]:-none}"

label() { gh label create "$1" --color "$2" --description "${3:-}" --force >/dev/null && echo "  label $1"; }
echo "-- labels"
label status:queued  FBCA04 "task available; claim = create ref claim/<n>"
label status:claimed 0E8A16 "advisory; the claim/<n> ref is the lock"
label status:review  1D76DB "PR open, awaiting design review + human merge"
label agent:devin    5319E7 "needs the design node"
label agent:claude   D93F0B "worker-side"
label human          BFDADC "needs a person"
label adoptable      8250df "PR whose author is gone; next claimer may adopt branch + force-push"
label prio:p0        B60205 "pick order: lowest prio first, then oldest"
label prio:p1        E99695 "pick order"
label prio:p2        F9D0C4 "pick order"
label lane:canary    999999 "standing canary lane"
for L in "${LANES[@]}"; do label "lane:$L" C2E0C6 "may touch $L/ and tests/$L/ only"; done

echo "-- files"
sed -i.bak -E "s/^mode: .*/mode: $MODE/; s/^merge: .*/merge: $MERGE/" docs/STATE.md && rm docs/STATE.md.bak
for L in "${LANES[@]}"; do
  mkdir -p "$L" "tests/$L"; touch "$L/__init__.py" "tests/$L/__init__.py"
  grep -q "^| \`lane:$L\`" docs/STATE.md ||
    sed -i.bak -E "/^\| \`lane:canary\`/a\\
| \`lane:$L\` | \`$L/\` | <purpose> | \`contracts/$L.md\` |" docs/STATE.md
  grep -q "^$L:" docs/verify.txt || echo "$L: python3 -m pytest tests/$L -q" >> docs/verify.txt
  mkdir -p "$(dirname "contracts/$L.md")"
  [ -f "contracts/$L.md" ] || printf '# %s — contract\n\nExposes:\n- <function / route / file format>\n' "$L" > "contracts/$L.md"
  echo "  lane $L"
done
rm -f docs/STATE.md.bak
sed -i.bak -E "s/^- <date>: repo created.*/- $(date -u +%F): repo created from agent-bus-template; bootstrap run (mode=$MODE)./" docs/STATE.md && rm -f docs/STATE.md.bak
if ! git diff --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
  git add -A && git commit -qm "bootstrap: mode=$MODE, lanes: ${LANES[*]:-none}" && git push -q && echo "  committed + pushed"
fi

echo "-- design bot"
if [ -n "$DESIGN_BOT" ]; then
  gh variable set DESIGN_BOT --body "$DESIGN_BOT" && echo "  DESIGN_BOT=$DESIGN_BOT"
else
  echo "  DESIGN_BOT not set; CI defaults to devin-ai-integration[bot]"
fi

echo "-- canary issues (two: standing post-merge, transient pre-merge — two locks; not status:queued: claimed by number, never by pick order)"
canary_issue() { # title body-file
  if gh issue list --label lane:canary --state open --search "$1 in:title" --json number --jq 'length' | grep -q '^0$'; then
    gh issue create -t "$1" -l "lane:canary,agent:claude" -F "$2" >/dev/null && echo "  created: $1"
  else echo "  exists: $1"; fi
}
cat > /tmp/canary-standing.md <<'MD'
Standing issue. Never closes. **Post-merge canary** — claimed once by a worker and held permanently (the claim ref is the PR branch).

**Goal:** a worker-authored draft PR against `main` that predates every workflow merge, so it catches base drift a fresh PR structurally cannot.

**Worker, never design** (`lane` classifies by author; only a worker PR certifies the worker path).
1. Claim as usual; open a draft PR from `claim/<this>` titled `#<this>: standing canary — do not merge`. Never merge, never close.
2. After each `.github/workflows/` merge: `git commit --allow-empty -m "canary: post-merge for #<workflow PR>" && git push`.
3. Expect `lane=success resolve=success run=success`. Anything else = the merge regressed the worker path; open an `agent:devin` issue.

**Acceptance:** `python3 -m pytest tests/canary -q` (`docs/verify.txt`).

prio:p2
MD
cat > /tmp/canary-premerge.md <<'MD'
Standing issue. Never closes. **Pre-merge canary** — transient claim, one per workflow PR.

**Goal:** before a PR touching `.github/workflows/` merges, prove `lane`, `resolve`, `run` all *execute* (not skip) on the new workflow.

**Worker, never design.**
1. `gh api repos/{owner}/{repo}/git/refs -f ref=refs/heads/claim/<this> -f sha=<tip of the design branch>`. 422 → another canary is in flight; wait for it to close.
2. Any change under `canary/`, push, open a PR **with base = the design branch**, title `#<this>: canary for #<workflow PR>`.
3. Wait for `lane=success resolve=success run=success`. Anything skipped = not validated.
4. Close the canary PR (do not merge), delete `claim/<this>` — the one exception to rename-on-release.
5. Human merges the workflow PR; the standing-canary holder then pushes an empty commit.

**Acceptance:** `python3 -m pytest tests/canary -q` (`docs/verify.txt`).

prio:p2
MD
canary_issue "canary: standing post-merge canary (permanent claim)" /tmp/canary-standing.md
canary_issue "canary: pre-merge canary (transient claim, one per workflow PR)" /tmp/canary-premerge.md

echo "-- branch protection"
if gh api -X PUT "repos/$REPO/branches/main/protection" --input - >/dev/null 2>/tmp/bp.err <<'EOF'
{"required_status_checks":{"strict":false,"contexts":["lane","run"]},
 "enforce_admins":false,
 "required_pull_request_reviews":null,
 "restrictions":null,
 "allow_force_pushes":false,"allow_deletions":false}
EOF
then echo "  main: lane + run required, PR-only"
else
  echo "  NOT available on this plan/visibility (private repo on free plan). Checks are advisory: read them before merging."
  [ "$MODE" = team ] && echo "  team mode without protection is not recommended — make the repo public or use an org/Pro plan."
fi

echo "-- design handshake (repo is not live until design answers it and cuts the first queued issue)"
if gh issue list --label agent:devin --state open --search "design node handshake in:title" --json number --jq 'length' | grep -q '^0$'; then
  { printf 'Repo: %s\nmode: %s  merge: %s\nlanes: %s\nverify (docs/verify.txt):\n' "$REPO" "$MODE" "$MERGE" "${LANES[*]:-none}"
    sed 's/^/    /' docs/verify.txt
    printf 'setup: docs/setup.sh\nbranch protection: %s\n\nDesign: on join, set `design: <login>` in docs/STATE.md (via claim/state), answer here (confirm or fix the lane split), cut the first status:queued issue, then close this. Until then workers see "no design node" and nothing is queued.\n' "$( [ -n "$(gh api "repos/$REPO/branches/main/protection" --jq .url 2>/dev/null)" ] && echo on || echo off )"
  } > /tmp/handshake.md
  gh issue create -t "design node handshake: $REPO" -l "agent:devin" -F /tmp/handshake.md >/dev/null && echo "  created"
else echo "  exists"; fi

cat <<EOF

== left for a human
- Fill docs/STATE.md Purpose + lane purposes; fill contracts/<lane>.md; cut queue issues (lane:<x>, status:queued, goal + acceptance).
- Devin: connect this repo in the Devin GitHub integration, then in a Devin session say "join $REPO". Live = design answered the handshake issue, `design:` is set in docs/STATE.md, and a status:queued issue exists.
- Each worker device: gh auth login (own account in team mode); install the worker plugin + permissions per docs/SETUP.md section 3; run /bus:doctor before claiming.
- Not Python? Edit docs/setup.sh + docs/verify.txt (design PR + canary: they are what CI executes). Runner has node/go/java preinstalled.
- merge=auto-lane requires branch protection (see above); without it design cannot set auto-merge and the human merges in batches.
EOF
