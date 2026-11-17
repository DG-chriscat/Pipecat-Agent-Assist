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
    shows "You are ...", the DOB and member ID to give, and the caller's talk track.
  - **Caller talk track** (P5-11): the beats, see below.

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

## Caller talk tracks (P5-11)

Chris, Nov 17: no two person mode and **no agent script**. The agent stays unscripted
and works from the assist (that is the product). The talk track structures the test
for the human caller, and tells the tester what the agent's screen should show.
Screenshots: [screenshots/talk-tracks/](screenshots/talk-tracks/) (Maria's card with
the talk track open and the first ticks, and the caller page beats, light and dark).

Every persona has a `talk_track` in `demo_personas.yaml`: beats in call order.

- **Intake** (to the bot): one beat per bot question, in the bot's order (identity,
  confirm, reason, claim number, when and where, what went wrong, read back).
- **With the agent**: the trigger line first, then the rest of the conversation.
- **Closing**: how the caller ends the call, and the wrap up the agent should get.

Each beat has `say` (the main line), `if_asked` (up to three backup lines for likely
questions, for example "If the agent asks when or where: About a month ago, at
Riverside Imaging Center."), a `note` for the caller, an `agent_note` for the tester
(what the agent should do, never shown to the caller), and `expect`: what should
appear on the AGENT screen after the beat. Expect kinds: incoming call, identity,
verify caller, locked records, repeat caller, next best step (hero, by card id), card
(tile id), article (KB id), call plan (step id, ticked by the hero's action or the
agent's words), tone tip, Ask bar (the question to type and the record it answers
with), wrap up (reason code and the reference numbers in the summary).

**Optional feature beats** are marked `optional: true` with a `feature`: sound
frustrated (tone tip; Maria, Sarah), an Ask bar follow up (Maria, James, Sarah, Linda,
Mark, Aisha), and a verification moment (Linda and Aisha when intake heard the name
differently, Ben when the agent asks again for the date of birth). The old `script`
field is the non optional beats, derived in `demo_personas.py`.

**Two rules from the P5-4 runs**, on the talk track view and in every closing beat:

1. Say names plainly; never spell them to the bot. Linda's fix line is "No, my name is
   Linda Nguyen." Spelling to the human agent when asked is fine.
2. The agent presses **Accept on the hero before End**. Without it the wrap up says no
   logged actions and the reason code falls back (5 of 10 P5-4 runs ended `other`).

Linda's checkpoints are written for the fixed screen after Mark verified (t_3fb64b24):
the caller panel switches to the member record and loads the history, the claim 52976
tile appears, and the next hero is the out of network appeal.

### On the Demos page

**Caller talk track** on each card opens the beats grouped by phase with the "Before
you start" rules, the backup lines, the agent notes in the AI tint, and each expected
screen moment as a checkbox (kind chip plus text; Ask bar items show the question to
type, wrap up items the reason code and refs). "n of m seen" counts the ticks; Reset
ticks clears them. Ticks stay in the page. An open card spans the grid width.

### On the caller page

`/caller?persona=<key>` reads `GET /demo/personas/<key>/caller`, the caller's view:
name, DOB as said, member ID in groups, the case, and the beats with `say`, `if_asked`,
and the caller notes. No expect items, no agent notes, no audience notes (the endpoint
strips them; `test_demo_personas.py` checks it). Studio light and dark: the page
follows the system setting and has a theme button (saved in local storage).

### Checks

- `server/tests/test_talk_tracks.py` (no network): every expect item names a real rule
  that fires on a caller line and applies to this caller's seed record (headline
  rendered from the seed), a pack card template with rows for this caller (alert ids
  from the pack's duplicate detector), a KB article that exists and would show (the
  hero's source, or the general question matcher on the caller's line), a pack call
  plan step (ticked by the accepted hero's action or one of the step's phrases), an
  Ask bar question the pack answers with that record, a real tone tip headline, and a
  pack reason code whose actions include the hero's. Repeat caller items match the
  interaction history (2 or more calls in 30 days). Every record id, dollar amount,
  spoken member ID and claim number, "N weeks ago", and provider in any line matches
  the seed. Intake beats answer the bot's questions in order, the pack stays the
  persona's pack through every agent line, and no line to the bot spells a name.
  Mutation check: 14 deliberate wrong values (claim id, card, KB id, plan step, reason
  code, amount, Ask record, date, spoken claim number, a spelled name, a missing Accept
  note, a ref, a provider) each fail the suite.
- `server/tests/test_talk_track_live.py` (live): Maria's talk track end to end, every
  expect checkpoint asserted with timings. Results in the P5-11 PR.
- `docs/kb-review.md`: the knowledge article review (Part B, recommend only).

## Source of truth

`server/agent_assist_server/demo_personas.yaml`. `GET /demo/personas`
(`demo_personas.py`) returns `{phone_line, features: [{id, label}], personas}` and adds
`plan` (the plan name from the seed) and `member_id_groups`.
`server/tests/test_demo_personas.py` fails when a persona's name, DOB, member ID,
plan, or claim does not match `db/seed.py`, when a KB article does not exist, when a
seed demo persona has no card, or when a required case or feature is missing. Tom Reed
is checked to not be a member. `GET /demo/personas/<key>/caller` is the caller page's
view of one persona (P5-11).

## Code and tests

- Portal: `portal/src/components/Demos.tsx`, `TalkTrack.tsx`,
  `portal/src/api/demosClient.ts`; tests in `Demos.test.tsx` (cards, filters, copy, new
  tab link, talk track and checklist) and `App.test.tsx` (rail order, route, a ringing
  call returns to Calls).
- Caller page: `server/agent_assist_server/static/caller.html` reads `?persona=` and
  shows the caller's talk track (`test_browser_call.py`).
- Screenshots: `portal/scripts/shoot_demos.py` and `shoot_talk_tracks.py` (real server
  on the built portal).
