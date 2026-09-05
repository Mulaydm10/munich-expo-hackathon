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
