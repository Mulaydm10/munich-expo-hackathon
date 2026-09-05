> **LOCKED governing file.** Do not edit in place. See `GOVERNANCE.md`.

# COMPETITION.md — event facts (single source of truth)

**Every other doc in this repo links here rather than restating a date, a rubric weight, or a
rule.** If you find a duplicated deadline, rubric row, or hard rule anywhere else in the repo,
delete it there and replace it with a link to the relevant section of this file. A stale copy of a
deadline is the single most expensive kind of drift in a timed event.

Filled in on 2026-09-05 from the organizers' own pages (event site + Devpost), read in a browser
while signed in. Two facts below are **contradicted between official sources** and are recorded as
contradictions, not resolved by choosing the nicer one — see `Q-0003` and `Q-0004` in
`research/open_questions.md`. Anything still `TBD` is genuinely unpublished. Do not fill a `TBD`
with a plausible value; a fabricated deadline or rubric weight is worse than a blank one.

## Event
- **Name:** MunichTech EXPO Hackathon 2026 (Autumn Edition)
- **Host / organizer:** MunichTech EXPO
- **Event links:**
  - Event site: https://munichtechexpo.com/
  - Hackathons overview: https://munichtechexpo.com/hackathons
  - Challenges: https://munichtechexpo.com/challenges
  - Rules: https://munichtechexpo.com/hackathons/rules · FAQ: https://munichtechexpo.com/hackathons/faq
  - Devpost: https://munichtech-expo.devpost.com/ · rules: `/rules` · dates: `/details/dates`
- **Tagline used by the organizers:** "Build What Europe Needs"
- **Selected challenge (locked in our registration):** *Mobility & Automotive: EV Charging Load
  Predictor* — https://munichtechexpo.com/challenges/mobility-ev-charging-2026
  Brief, as published: forecast charging demand and recommend charging scheduling or dynamic
  pricing. A sample anonymized charging-session dataset is stated to be provided **at the challenge
  briefing**, not for download (see Data, below).
- **Our registration:** participant Dhruv Ranjit Mulay, registration ID `HKP-2026-IC3JYX`,
  challenge locked. No team, no project draft, no submission created as of 2026-09-05.
  Participation mode is **unset** — the selector lives on https://munichtechexpo.com/apply/hackathon
  (online-only free / hybrid / on-site, the latter two requiring an attendee ticket), not in the
  registration flow. No ticket held. Tracked as an open action, `Q-0008`, not as a settled fact.

## Deadline
- **Working assumption (plan against this):** Sunday **20 September 2026, 17:00 Europe/Berlin
  (CEST, UTC+2)** — "final submissions" in the published day-of schedule for the on-site event.
- **Conflicting reading:** Devpost gives the hackathon window as **1–20 September 2026**, which read
  alone implies end of day on the 20th.
- **Status: UNRESOLVED (`Q-0003`).** The earlier of the two is treated as binding until an organizer
  answers. Never plan against the later one.
- On-site EXPO: 20–21 September 2026, codecentric AG, August-Everding-Straße 20, 81671 München.
  Day-of (Sun 20 Sep): 08:30 check-in · 10:00 hackathon check-in + team formation · 13:00 track
  briefings · 13:45 Build Sprint I · 15:15 lunch + mentor office hours · 15:45 Build Sprint II ·
  17:00 final submissions · 17:15 live demos + judging · 18:00 awards.

## Submission format
Devpost submission. Required:
- Working prototype / functional PoC — real functionality, not slides
- Project description: problem, solution, target users/industry, why it matters for Europe
- Technical details: architecture, frameworks, AI models / datasets / APIs used
- Public repo link (GitHub/GitLab) or live demo link
- Demo video, **2–3 minutes**
Encouraged: business/deployment considerations, ethics + regulatory + sustainability notes, next steps.

## Judging rubric
**Weights are not published.** Two criteria sets appear in official material; treat both as scored
and map every artifact to both in `notes/judging_alignment.md`.

| Criterion | Weight | Notes |
|---|---|---|
| Problem relevance & impact | `TBD` | Devpost criteria list |
| Technical excellence & feasibility | `TBD` | Devpost |
| Innovation & originality | `TBD` | Devpost |
| Practical applicability & scalability | `TBD` | Devpost |
| Presentation & communication | `TBD` | Devpost |
| Forecast quality | `TBD` | challenge page (EV challenge specific) |
| Practicality of the scheduling / pricing suggestion | `TBD` | challenge page |
| Technical execution | `TBD` | challenge page |
| Demo quality | `TBD` | challenge page |

Judge listed publicly: Francisco Duarte. Full panel not published.

## Prizes
All prizes listed on the official pages are **non-cash**: Grand Challenge Award, Best Applied AI
Solution, Best Industry & Enterprise Use Case, Best Sustainability & Societal Impact, Best Student /
Early Talent. Winners get an on-stage award, an EXPO showcase slot, press/social visibility, intros
to enterprises/investors/public sector, and fast-track to exhibiting/pitching/Talent Hub.
Cash figures (€3,000 / €2,000) reported by third parties were **not found on any official page** —
do not repeat them. Sponsor credit offers contradict each other (registration pages say 3 months of
ElevenLabs Creator; the linked ElevenLabs Hacker Guide says 1 free month) — `Q-0005`.

## Team roster
| Name | Role | Contact |
|---|---|---|
| Dhruv Ranjit Mulay | participant (solo as of 2026-09-05) | via registration `HKP-2026-IC3JYX` |

**Team-size rule is contradicted between official sources (`Q-0004`):** Devpost states teams of
**2–6 members**; the rules page also says individuals may take part. One project per team, one team
per participant. Organizers, judges, mentors and sponsors may not compete.

## Hard rules
Verbatim-relevant points from the published rules; the full text is at the links above.
- **All submitted work must be created during the hackathon period.** Pre-existing code "must be
  clearly identified and may only be used as a foundation, not as a complete solution." → this repo
  starts from an empty scaffold on purpose, and any prior work reused must be disclosed in the
  submission.
- Eligibility: 18+ / legal age of majority; open to all countries with the standard exceptions.
- One project per team; one team per participant.
- `TBD` — no published rule found on AI-generated-code disclosure, dependency licensing, or API
  rate limits. Ask the organizers rather than assuming (`Q-0006`); until answered, disclose AI
  assistance voluntarily in the submission and keep every dependency permissively licensed.

## Data
- No official dataset is downloadable: not on the challenge page, not on Devpost (`/details/resources`
  does not exist), not in Updates, not in the FAQ or rules, and not in the ElevenLabs Hacker Guide.
  The challenge page says the sample anonymized charging-session dataset is handed out **at the
  briefing** (on-site, Sun 20 Sep 13:00 CEST). Whether remote participants receive it is unanswered
  (`Q-0007`).
- **Consequence for the build:** the project must not depend on that file. It is built on public
  German data (see `contracts/src/data.md`) with synthesised sessions (`contracts/src/fleet.md`);
  if the organizers' file appears it becomes a validation set, never a dependency.
