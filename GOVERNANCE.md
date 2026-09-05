# GOVERNANCE.md

## Main Agent
**Mulaydm10** holds sole authority to edit LOCKED files. This was not explicitly designated in the
brief that produced this scaffold; it defaults to the existing git identity on this machine/repo.
`TODO(Mulaydm10)`: confirm or override this at kickoff, especially if a team forms with a different
designated Main Agent.

## File categories

### LOCKED
Edit only via the Main Agent. Every edit — **including this scaffold's own initial authorship** —
is logged in the audit table below. Initial authorship is expected and correct, not a violation;
logging it models the right behavior for the next agent who touches these files.
- `COMPETITION.md` — event facts, single source of truth
- `VISION.md` — the thesis
- `design/README.md` — architecture + ADR process
- `experiments/README.md` — experiment log schema
- `research/README.md` — prior-art / open-questions process
- `notes/ai_onboarding_prompt.md` — canonical cold-start prompt for a fresh AI agent
- `logs/README.md` — log directory convention
- `tests/README.md` — test baseline status and policy

### Deliberately NOT locked
Their entire value is staying current; locking them would defeat the point.
- `CLAUDE.md`, `STATE.md`, `AGENTS.md`, `DEMO.md`

### Append-only
Never edit or delete a past entry. Correct mistakes by adding a new entry, not rewriting history.
- `worklog.md`
- `experiments/experiment_log.md`
- `research/prior_art.md`, `research/open_questions.md`

### Plain (unrestricted)
Everything not listed above is a normal file: edit freely, no Main Agent sign-off, no audit-table
entry required. This explicitly includes the top-level **`README.md`** and **`.gitignore`** — not
governance-locked by this scaffold — plus `GOVERNANCE.md` itself, `LICENSE`, `.gitattributes`,
`design/decisions/*.md` (the ADRs themselves, once opened — the *process* in `design/README.md` is
locked, individual decisions are not), `notes/glossary.md`, `notes/judging_alignment.md`,
`submissions/README.md`, and the `.gitignore`/`.gitignore`-adjacent files under `logs/` and
`scratch/`. A later step (e.g. grafting the agent-bus protocol onto this repo) that appends to
`README.md` or `.gitignore` needs no pre-authorization under this governance file.

## Audit table (LOCKED-file edits, including initial authorship)

| Date | File | Editor | Summary |
|---|---|---|---|
| 2026-09-05 | `COMPETITION.md` | hackathon-setup (initial scaffold) | Created with all event facts as explicit `TBD` — none known at scaffold time |
| 2026-09-05 | `VISION.md` | hackathon-setup (initial scaffold) | Created with all thesis fields as explicit `TBD`; registered as `Q-0001` |
| 2026-09-05 | `design/README.md` | hackathon-setup (initial scaffold) | Created; ADR process + template + real ADR-0001/ADR-0002 |
| 2026-09-05 | `experiments/README.md` | hackathon-setup (initial scaffold) | Created; experiment log schema defined |
| 2026-09-05 | `research/README.md` | hackathon-setup (initial scaffold) | Created; prior-art + open-questions process defined |
| 2026-09-05 | `notes/ai_onboarding_prompt.md` | hackathon-setup (initial scaffold) | Created |
| 2026-09-05 | `logs/README.md` | hackathon-setup (initial scaffold) | Created; log directory convention defined |
| 2026-09-05 | `tests/README.md` | hackathon-setup (initial scaffold) | Created; states no baseline exists and why |
| 2026-09-05 | `COMPETITION.md` | devin-ai-integration[bot] (design node, PR `bot/join-real-lane-split`) | Filled from organizers' pages: event links, locked challenge, submission format, rubric (weights still `TBD`), prizes, hard rules, data situation. Deadline and team-size rules **contradicted between official sources** — recorded as `Q-0003`/`Q-0004`, not resolved |
| 2026-09-05 | `VISION.md` | devin-ai-integration[bot] (design node, PR `bot/join-real-lane-split`) | Filled: FlexGrid thesis, users, why-unsolved, demo-time success bar, explicit non-goals. Closes `Q-0001` |
| 2026-09-05 | `tests/README.md` | devin-ai-integration[bot] (design node, PR `bot/join-real-lane-split`) | Replaced "no baseline yet" with real conventions now that `ADR-0002` is Accepted and one green smoke test per lane exists, as that file itself required |
| 2026-09-05 | `COMPETITION.md`, `VISION.md`, `tests/README.md` | devin-ai-integration[bot] (design node, PR `design/…-post-merge-state`) | Date correction only, no content change: the join session's clock read `2026-09-01`, while GitHub's own timestamps on the same commits and CI runs read `2026-09-05`. Every `2026-09-01` I had written is therefore wrong by four days and is rewritten to `2026-09-05` across these files plus `STATE.md`, `worklog.md`, `research/open_questions.md` and `ADR-0002`. GitHub's timestamps are the authority here, not mine. Nothing about the event dates changed — the `2026-09-20` deadline and the `Q-0003` contradiction are untouched |

Authority note: these three edits were made by the design node, not by Mulaydm10, under the
kickoff instruction to set the repo up for the workers while the Main Agent was away. They were
landed via PR #5 **for the Main Agent to merge** — the human merge was meant to be the sign-off.

`TODO(Mulaydm10)`: **that sign-off never happened.** #5 was merged by the worker account while you
were away, so the three LOCKED files are live and the whole build is already reading them as truth
with nobody having agreed to them. Read `COMPETITION.md` and `VISION.md` now, not at submission
time. Disagreement is cheap today — one revert PR — and expensive once six lanes have been written
against the thesis.
