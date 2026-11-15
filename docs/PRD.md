# Healthcare Voice Agent Assist PRD

Nov 14, 2026 · @Christopher Catalano

## Summary

We will build a demo of a healthcare voice agent assist on Pipecat 1.15 and Daily. A bot answers the phone, asks intake questions, then hands the caller to a human agent picked from a queue. The agent works in a web portal that shows what the caller already said, then keeps feeding live help: claim history, payment history, account details, and knowledge base articles.

The v1 goal is to prove the full loop works on one Daily phone number with mock data, in a demo a customer can watch. What happens after the handoff stays open ended. New use cases plug in as packs, so the core does not change.

Decisions made so far:

- **Telephony:** all Daily. A Daily phone number sends the call into a Daily room, and the agent joins that room over WebRTC. A Twilio number in front of Daily stays a documented swap option.
- **Queue:** built in house.
- **Scope:** demo or proof of concept with mock data. No real patient data in v1.

## Open questions and working assumptions

These answers are still open, so the PRD uses the assumptions below. Change any row and the matching sections will follow.

| Open question | Working assumption | Why it matters |
| --- | --- | --- |
| Which data systems are in scope? | One mock Postgres database with members, claims, payments, and knowledge base articles | Sets what the connectors read and what cards can show |
| Who calls in? | Patients or health plan members calling a billing and claims line | Shapes intake questions and use case packs |
| How do callers prove who they are? | Full name, date of birth, and member ID, matched against the mock database | Drives the identity step and the verified badge |
| Where does it run? | Pipecat Cloud for the bot; portal and APIs on standard cloud hosting | Affects deploy steps and the phone webhook setup |
| Which LLM? | Any model Pipecat supports; picked during the first build spike | Affects latency and cost |
| Which languages? | English only | Affects speech models and scripts |
| How many agents and calls at once? | Up to 3 agents and 5 calls for the demo | Sizes the queue and test plan |
| Do we record audio? | No audio recording; transcripts and cards saved per call | Keeps the demo light on stored data |

## Users and roles

Four roles touch the system, but only the caller and the human agent are in the main demo.

| Role | What they need | Where they work |
| --- | --- | --- |
| Caller | Get help fast without repeating themselves | Phone |
| Human agent | See who is calling and why, then get the right facts during the call | Agent portal |
| Supervisor | See the queue and who is on calls | Agent portal, read only view (v1 stretch) |
| Builder | Add or change use case packs, intake questions, and connectors | Config files in the repo |

## End to end call flow

&#91;embedded content: call flow · 6 stages, 1 decision\]

A missed offer loops back to the queue. Once an agent accepts, the bot stops speaking and only feeds the agent cards until wrap up.

## Requirements: phone intake

The bot greets the caller, checks who they are, and learns why they called, all before the queue. Target: intake done in under 2 minutes for a typical caller.

| ID | Requirement | Priority |
| --- | --- | --- |
| IN-1 | A call to the Daily number fires a webhook that starts one Pipecat bot in a new Daily room. The caller hears a greeting within 2 seconds of the bot joining. | Must |
| IN-2 | The greeting tells the caller they are speaking with an AI assistant and that a person will join. | Must |
| IN-3 | The intake script is a Pipecat Flow with these steps: greeting, identity, reason for the call, use case questions, read back. | Must |
| IN-4 | Intake questions live in a use case pack config, so a builder can change them without code changes. | Must |
| IN-5 | Each answer is saved as a structured field on the call record as soon as it is captured. | Must |
| IN-6 | Identity is checked against the database through a function call. Result is verified, not verified, or not found. Callers who fail still reach an agent, flagged. | Must |
| IN-7 | The caller can ask for a person at any time. The bot skips ahead to the queue and passes what it has. | Must |
| IN-8 | The bot reads back a short summary and lets the caller fix it. | Should |
| IN-9 | The bot gives no medical advice. If the caller describes an emergency, it tells them to hang up and call 911, and flags the call. | Must |
| IN-10 | Pipecat Tone reads the caller's tone on each turn, and the reading is saved with the transcript. | Should |
| IN-11 | Speech to text and text to speech use Deepgram. Expressive conversation may be turned on for a warmer bot voice. | Should |

## Requirements: queue and handoff

When intake ends, the call joins a first come, first served queue and rings the next free agent. The caller waits in the same Daily room, and the bot stays there the whole time.

| ID | Requirement | Priority |
| --- | --- | --- |
| QH-1 | Agents have five states: offline, available, ringing, on call, wrap up. Only available agents get offers. | Must |
| QH-2 | One queue in v1, served in arrival order. Skills based routing comes later. | Must |
| QH-3 | An offer rings one agent at a time, the one idle the longest. The agent has 20 seconds to accept. A missed offer sets that agent to not available and rings the next one. | Must |
| QH-4 | The queue service is the single source of truth for calls and agent states. It pushes changes to the portal over a websocket. | Must |
| QH-5 | While waiting, the caller hears hold audio and a short update from the bot about every 60 seconds. | Must |
| QH-6 | On accept, the server makes a Daily meeting token for that agent. The agent's browser joins the room. The bot says a short line, such as "Connecting you now," then stops speaking. | Must |
| QH-7 | The agent sees the intake summary the moment they accept, before the audio connects. | Must |
| QH-8 | After a set wait (default 5 minutes), the bot offers a callback, logs it, and ends the call politely. | Should |
| QH-9 | If the agent drops off, the caller goes back to the front of the queue and the bot speaks again. | Should |
| QH-10 | The demo handles 5 calls at once across 3 agents. | Must |

## Requirements: agent portal

The portal is one screen where agents go available, accept calls, talk to callers, and see live help. It is a React app built on the Pipecat client SDK, with Daily carrying the audio and RTVI messages carrying the updates.

In call layout ("Focus", locked Nov 15, 2026; see [DESIGN.md](DESIGN.md) and [ADR 0001](adr/0001-portal-layout-focus.md)), from left to right:

- **Caller panel:** name, verified badge, member ID and plan, why they called, key facts, calls in the last 30 days, a "before this call" history of the last 3 events, and a mood trend for this call.
- **Assist column:** one **next best step** in large type with the words to say and one action, then supporting cards (record, call plan, tone tip), and an Ask bar at the bottom.
- **Live transcript:** caller and agent lines labeled by speaker, with trigger words underlined.

A call island in the top bar shows the caller, verified status, a live waveform, mood, and the call timer. Mute, hold, and end call sit top right.

| ID | Requirement | Priority |
| --- | --- | --- |
| AP-1 | Simple sign in for the demo. Single sign on comes later. | Must |
| AP-2 | Status toggle for available and not available. | Must |
| AP-3 | Incoming call card with caller name, verified status, reason, and wait time, plus Accept and Decline buttons and a 20 second countdown. | Must |
| AP-4 | Call controls: mute, hold, and end call. | Must |
| AP-5 | The caller panel fills from intake data the moment the agent accepts. | Must |
| AP-6 | Assist cards come in five types: record (such as a claims table), knowledge base article, quick guide checklist, alert, and suggested next step. | Must |
| AP-7 | Each card shows its source record or article. Agents can pin, dismiss, and rate a card thumbs up or down. | Must |
| AP-8 | The agent can type a question to the assist, such as "Show the last 3 claims." | Should |
| AP-9 | At wrap up, the assist drafts a call summary and a reason code. The agent edits and saves. | Should |
| AP-10 | Transfer to another agent or line. | Later |
| AP-11 | Next best step: the assist shows one ranked suggestion at a time, with the trigger it heard, a one or two sentence reason, a script to say, one primary action, and its source. Accept, dismiss, and "show me why" are one click or one key. | Must |
| AP-12 | Call plan: the active pack's checklist shows progress, and steps tick themselves when the transcript or an accepted action shows they are done. The agent can tick or untick by hand. | Must |
| AP-13 | Caller history: the caller panel shows calls in the last 30 days (with a repeat caller flag) and the last 3 events across calls, claims, and referrals. | Must |
| AP-14 | Mood trend: tone readings for the caller draw a trend line for the whole call, and a tone tip card appears when mood drops two steps. | Should |
| AP-15 | Every screen follows DESIGN.md: Deepgram 2026 brand fonts, colors, and materials only. | Must |

## Requirements: real time assist and data

After handoff, the same bot switches to listen only. It transcribes both people, decides what would help, pulls data through connectors, and sends cards to the agent's screen. It never speaks to the caller in this mode.

How one card is made:

1. Each person's audio track in the Daily room is transcribed on its own, so every line is labeled caller or agent without diarization.
2. The assist LLM reads a rolling window of the transcript plus the intake data.
3. When a trigger fires, it calls a connector tool for the right data system.
4. The result is shaped into a card and sent as an RTVI message to the agent's portal only.
5. The assist ranks candidate suggestions and promotes the best one to the next best step. The rest become supporting cards. A new hero replaces the old one only when it ranks higher or the old one is accepted or dismissed.

### Use case packs keep the design open ended

A use case pack bundles everything one kind of call needs: intake questions, triggers, allowed connectors, card templates, and quick guides. Adding a use case means adding a pack, not changing the core.

| Pack | Example trigger | Connectors | Cards | v1 |
| --- | --- | --- | --- | --- |
| Claim status | "Where is my claim?" | Members, Claims, Referrals, Authorizations, Interactions | Claims table, call plan for denied claims, next best step (for example retro authorization review) | Yes |
| Account and payments | "I was charged twice" | Members, Payments | Payment history, alert on duplicate charge | Yes |
| General question | Any question not covered above | Knowledge base | Article card | Yes |
| Benefits check | "Is this covered?" | Plans, Benefits | Coverage summary | Later |
| Appointment help | "I need to reschedule" | Scheduling | Open slots | Later |

### Connector requirements

| ID | Requirement | Priority |
| --- | --- | --- |
| DC-1 | Every connector uses one standard shape: search, and get by ID. Each is exposed as an LLM tool through a function call or an MCP server. | Must |
| DC-2 | Connectors are read only in v1. | Must |
| DC-3 | v1 connectors read one mock Postgres database seeded with test members, plans, claims (with denial codes), payments, referrals, prior authorizations, past interactions, and articles. | Must |
| DC-4 | A connector call times out after 2 seconds, and results are cached for the rest of the call. | Should |
| DC-5 | Every connector call is logged with the call ID, the tool, and the result size. | Must |
| DC-6 | A card shows on screen within 3 seconds of the words that triggered it. | Should |
| DC-7 | Assist limits itself to a set number of new cards per minute, so the feed stays readable. | Should |
| DC-8 | An Interactions connector returns a member's past calls (date, duration, queue, outcome, notes) for caller history and repeat caller detection. | Must |
| DC-9 | Next best step actions in v1 are simulated: accepting logs the action on the call record and ticks the call plan. Nothing writes back to source systems. | Must |
| DC-10 | Each pack defines its next best step rules: trigger phrases, required facts, script template, and action. | Must |

### Guardrails

- Every card is labeled as AI suggested and links to its source.
- The assist gives no clinical advice, only facts from records and approved articles.
- Tone readings from the caller's track drive the tone badge and a frustration alert. This is the RCS perception data in action.

## Architecture

&#91;embedded content: system architecture · 7 parts\]

One bot per call does intake, then switches to listen only assist in the same room. The caller and agent never change rooms, so the handoff is just the agent joining.

| Layer | Choice | Notes |
| --- | --- | --- |
| Framework | Pipecat 1.15, pinned | Flows is built in; expressive conversation is alpha |
| Phone and media | Daily phone number and Daily rooms | Daily numbers are US and Canada only |
| Speech | Deepgram speech to text and text to speech | Swap points stay open |
| LLM | Any Pipecat supported model | Picked in the spike |
| Tone | Pipecat Tone reader | Feeds tone badges and alerts |
| Portal | React with the Pipecat client SDK | Audio over WebRTC, cards over RTVI |
| Queue | In house service with Redis or Postgres | Pushes state over a websocket |
| Data | Mock Postgres behind connectors | Real systems later, same connector shape |

### Where RCS fits, and where it does not

RCS 0.1 is an alpha that carries two things inline with the words: how the caller sounded (perception) and how the bot should speak (expression). We use perception for tone badges and frustration alerts, and expression, optionally, for a warmer intake voice.

RCS does not fetch data or update the screen. Connectors handle data, and RTVI messages carry cards to the portal. RCS also has no speaker labels yet; that is on its roadmap. We get speaker labels by transcribing each person's Daily track on its own.

## Out of scope for v1

v1 proves the loop, so anything needed only for real patients or scale waits.

- Real patient data, BAAs, and HIPAA controls. These are required before any pilot.
- Writing back to source systems, such as updating a claim or taking a payment.
- Skills based routing, multiple queues, and transfers between agents.
- Outbound calls and callbacks that actually dial out.
- Languages other than English.
- Audio recording and long term call analytics.
- Twilio as the phone number provider. This stays a documented option that sends calls into the same Daily room over SIP.

Later use cases already fit the pack model: benefits checks, prior authorization status, appointment scheduling, pharmacy refills, and provider calls checking a patient's eligibility.

## Risks and mitigations

The biggest unknown is whether one bot can transcribe each person's track on its own inside the Daily room. The first build spike tests that before anything else.

| Risk | What could happen | Mitigation |
| --- | --- | --- |
| Per person transcription is harder than expected | Transcript lines lose caller and agent labels | Spike in week one. Fallback: a second listen only bot, or a speech model with diarization. |
| RCS 0.1 and expressive conversation are alpha | Formats change and break tone features | Pin the Pipecat version. Keep tone features behind a switch. |
| Too many or wrong cards | Agents ignore the feed | Triggers per pack, a card rate limit, and thumbs up or down feedback. |
| Slow LLM or connector calls | Cards arrive after the moment passes | 2 second connector timeout, caching, and a fast model for the assist. |
| Real patient data slips into the demo | Compliance exposure | Mock data only. List every vendor needing a BAA before a pilot, including Daily, Deepgram, the LLM, and hosting. |
| Daily numbers cover only the US and Canada | Cannot demo for callers elsewhere | Fine for v1. Twilio over SIP covers other regions later. |
| Use case scope keeps growing | The demo slips | Lock v1 to three packs. New ideas go to the later list. |

## Milestones and success measures

The build runs in five phases, each with a clear exit check. Dates come once the team and the open questions are settled.

1. **Spike:** one call into a Daily room, a bot that talks, and a browser that joins. Exit when the bot transcribes caller and browser tracks separately.
2. **Intake and queue:** Flow based intake, identity check, the queue service, and agent states. Exit when a call waits in the queue and rings an agent.
3. **Portal and handoff:** accept, join the room, caller panel, and live transcript. Exit when a full call goes from ring to wrap up.
4. **Assist and connectors:** three packs, the mock database, and cards on screen. Exit when each pack's trigger shows its card.
5. **Demo polish:** tone badges, wrap up summary, and test runs with scripted callers. Exit when the success measures below are met.

Proposed success measures for the demo:

| Measure | Target |
| --- | --- |
| Scripted demo calls that finish the full loop | 9 of 10 |
| Typical intake time | Under 2 minutes |
| Time from Accept to hearing the caller | Under 2 seconds |
| Time from trigger words to card on screen | Under 3 seconds |
| Identity checks correct on scripted calls | 10 of 10 |
| Cards rated useful by demo agents | 70% or more |
