# Demos page (P5-10, PRD AP-23, DC-12)

One place to pick a demo caller (Chris, Nov 16). Rail icon **Demos** (Lucide Play),
above Architecture; route `/agent/demos`, behind the shared login like every page.
Screenshots: [screenshots/demos/](screenshots/demos/) (page light and dark, Maria's
card with the script open, light and dark).

## What it shows

- **Phone line** box: the Daily number +1 (612) 746-7133 and the app password hint
  ("ask Chris"; never the password itself).
- One card per demo caller (10): name, DOB, member ID in two groups of three in large
  Azeret Mono (418 302), plan, the case in one line, the pack it triggers, what to say
  to start, what the audience should see (the next best step headline and supporting
  cards), and feature chips.
- Filters: feature chips (toggle one) and a pack segmented control.
- Actions per card:
  - **Copy details**: name, DOB, and member ID to the clipboard.
  - **Start browser call**: opens `/caller?persona=<key>` in a new tab. The caller page
    shows "You are ...", the DOB and member ID to give, and the script.
  - **Show script**: the lines in order, first the answers for the intake bot, then
    the lines to say to the agent to trigger the cards, with notes.

| Card | Pack | Shows |
| --- | --- | --- |
| Maria Lopez (hero) | claims_status | MRI claim 70412 denied CO-197: retro authorization review; repeat caller; wrap up |
| James Carter | account_payments | duplicate premium draft alert and refund |
| Sarah Miller | claims_status | frustrated repeat caller: tone tip; claim 31845 pending info |
| Linda Nguyen | claims_status | hard name: close match, verification hero, record unlock; claim 52976 out of network appeal |
| Mark Hill | claims_status | two members named Mark Hill: the DOB decides |
| Aisha Patel | general_question | hard name: close match; EOB article |
| David Brooks | general_question | deductible and out of pocket maximum article |
| Ben Cole | claims_status | wrong DOB: verify caller hero |
| Tom Reed | claims_status | not a member: the record gate stays locked |
| Emily Stone | claims_status | emergency guardrail (chest pain, 911 line). Do not use in a customer demo without warning |

## Source of truth

`server/agent_assist_server/demo_personas.yaml`. `GET /demo/personas`
(`demo_personas.py`) returns `{phone_line, features: [{id, label}], personas}` and adds
`plan` (the plan name from the seed) and `member_id_groups`.
`server/tests/test_demo_personas.py` fails when a persona's name, DOB, member ID,
plan, or claim does not match `db/seed.py`, when a KB article does not exist, when a
seed demo persona has no card, or when a required case or feature is missing. Tom Reed
is checked to not be a member.

## Code and tests

- Portal: `portal/src/components/Demos.tsx`, `portal/src/api/demosClient.ts`; tests in
  `Demos.test.tsx` (cards, filters, copy, new tab link, script) and `App.test.tsx`
  (rail order, route, a ringing call returns to Calls).
- Caller page: `server/agent_assist_server/static/caller.html` reads `?persona=` and
  shows the script (`test_browser_call.py`).
- Screenshots: `portal/scripts/shoot_demos.py` (real server on the built portal).
