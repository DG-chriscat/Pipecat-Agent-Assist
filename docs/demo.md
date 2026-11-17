# Demo readiness (P5-4)

Scripted demo calls scored against the PRD success measures ("Milestones and success
measures"), plus the demo script. Personas and IDs are the P5-10 demo data (Demos page,
[demos.md](demos.md)). Mock data only.

## How the runs were made

`server/tests/test_demo_runs_live.py` (live, about 3 minutes per run) places one real
call per run through the server, a bot process, and the built portal:

1. A Daily caller (Deepgram Aura-2 Arcas voice, synthesized at test time) answers the
   intake bot as the persona, reading the persona's lines plainly. Intake time is bot
   joined to the call waiting in the queue.
2. Agent Ana's portal (headless Chromium, 1600x1000) goes Available. The caller keeps
   saying hello while the agent presses Enter. **Accept to hearing the caller** is Enter
   to the caller's audio first playing in the page (an analyser on the remote audio
   element).
3. The caller says a trigger line. **Trigger to card** is the end of the trigger word
   (Deepgram word timings on the played audio) to the expected card in the DOM.
4. Verify hero runs (IN-13): the agent presses Mark verified (Linda) or Could not verify
   (Ben) and the test checks the records unlock or stay locked.
5. End: the drafted summary lands in wrap up, Save and go available, "Call saved".

**Full loop** means all five steps passed. Run 1 first changes the greeting in Settings
and checks the bot speaks the new one.

Where: locally on this branch (main after #58), not on deepgram-vitals.fly.dev. The
runs need a throwaway database schema per call (never `public`), and the harness starts
and watches the bot process itself; prod's `/calls/browser` and `/caller` sit behind the
app password. Prod runs the same build and the same defaults; see Follow ups for the
one check left for prod.

```bash
cd portal && npm run build && cd ..
set -a; . ./.env; set +a
PLAYWRIGHT_BROWSERS_PATH=... DEMO_RESULTS=/tmp/demo.jsonl DEMO_SCREENSHOT_DIR=/tmp/shots \
  uv run --with playwright pytest -m live server/tests/test_demo_runs_live.py -s -v
uv run python bot/tests/harness/demo_table.py /tmp/demo.jsonl   # the table below
```

`DEMO_RUNS=4,5` picks runs. The scoring is `bot/tests/harness/demo.py` (unit tested in
`bot/tests/test_harness_demo.py`).

### Settings in effect (defaults; bot log `[settings]` line, every run)

| Step | Model |
| --- | --- |
| Intake speech to text | Deepgram Nova-3 general (`stt_intake=nova-3-general`). P5-7 kept Nova-3 as the intake default; Flux is selectable but missed Linda's last name. |
| Intake LLM | Deepgram phoneLLM mini (`llm_intake=phonellm-mini-alpha-2-fp8`) |
| Intake voice | Deepgram Flux, Alexis (`tts=flux/flux-alexis-en`, speed 1, read backs at 0.9x) |
| Assist speech to text | Deepgram Nova-3 general (`stt_assist=nova-3-general`) |
| Assist LLM and Ask bar | Claude Haiku 5.5 (`llm_assist=claude-haiku-5-5`) |
| Wrap up summary | Claude Haiku 5.5 (`llm_wrapup=claude-haiku-5-5`) |

## Results (Nov 17, 00:41 to 01:10 UTC, one batch of 10)

| Run | Persona | Pack | Identity (want / got) | Intake (s) | Accept to caller (s) | Trigger to card (s) | Card | Wrap up saved | Full loop | Cards useful |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Maria Lopez (greeting changed in Settings first) | `claims_status` | verified or close_match / verified pass | 86.69 pass | 2.42 fail | 0.74 pass | `nbs:retro_auth_review:70412` | yes | pass |  |
| 2 | James Carter | `account_payments` | verified or close_match / verified pass | 87.34 pass | 1.83 pass | 1.63 pass | `nbs:duplicate_premium_refund:PAY-000067` | yes | pass |  |
| 3 | Aisha Patel | `general_question` | verified or close_match / verified pass | 120.60 fail | 1.93 pass | 1.45 pass | `kb:KB-CL-010` | yes | pass |  |
| 4 | Linda Nguyen (says "Linda Yuen"), Mark verified | `claims_status` | close_match or not_verified / not_verified pass | 88.47 pass | 1.69 pass | 0.53 pass | locked tile, then 52976 after unlock | yes | pass |  |
| 5 | Ben Cole (wrong DOB), Could not verify | `claims_status` | not_verified / not_verified pass | 110.76 pass | 1.68 pass | 2.38 pass | locked tile (stays locked) | yes | pass |  |
| 6 | Sarah Miller | `claims_status` | verified or close_match / verified pass | 101.32 pass | 2.62 fail | 0.96 pass | `nbs:provider_info_follow_up:31845` | yes | pass |  |
| 7 | Linda Nguyen | `claims_status` | verified or close_match / verified pass | 133.62 fail | 1.98 pass | 0.57 pass | `nbs:out_of_network_appeal:52976` | yes | pass |  |
| 8 | Maria Lopez | `claims_status` | verified or close_match / verified pass | 114.12 pass | 1.55 pass | 0.95 pass | `nbs:retro_auth_review:70412` | yes | pass |  |
| 9 | James Carter | `account_payments` | verified or close_match / verified pass | 102.46 pass | 2.15 fail | 1.87 pass | `nbs:duplicate_premium_refund:PAY-000067` | yes | pass |  |
| 10 | Aisha Patel | `general_question` | verified or close_match / verified pass | 113.22 pass | 2.24 fail | 1.54 pass | `kb:KB-CL-010` | yes | pass |  |

"Cards useful" is for Chris's demo agents to fill in (target 70% or more).

### Against the targets

| Measure | Target | Result | Verdict |
| --- | --- | --- | --- |
| Scripted calls that finish the full loop | 9 of 10 | 10 of 10 | met |
| Typical intake time | under 2 minutes | median 106.6 s; 7 of 10 under 120 s (120.6, 133.6 over; max 133.6 s) | met for typical, 3 runs over |
| Accept to hearing the caller | under 2 s | median 1.96 s; 5 of 10 under 2 s; max 2.62 s | borderline: median just under, half the runs over |
| Trigger words to card on screen | under 3 s | median 1.20 s, max 2.38 s; 10 of 10 | met |
| Identity checks correct | 10 of 10 | 10 of 10 (8 verified, Linda "Yuen" not_verified and Ben not_verified as expected) | met |
| Cards rated useful | 70% | column left for the demo agents | open |

Other timings from the same runs:

- Intake bot response time (caller line end to the bot's next audio, 71 turns): median
  2.27 s, p90 4.39 s; two long gaps (8.2 s run 1, 7.1 s run 7).
- Accept breakdown (portal performance marks, median): Daily joined 1.1 s after Enter,
  caller track subscribed 1.6 s, caller audio playing 1.96 s. The join is most of it.
- Wrap up draft in the editor 2.5 to 3.7 s after End (median 2.7 s).
- Greeting changed in Settings, then heard on the call (run 1): "welcome to the deepgram
  vitals demo line you're speaking with an ai assistant ...".

### Earlier batches (same day, same build except the harness fixes)

| Batch | Runs that started a call | Full loop | Identity correct | What went wrong |
| --- | --- | --- | --- | --- |
| 1 (00:01) | 7 of 10 | 7 of 7 | 5 of 7 | 3 runs never started: the harness's own Deepgram TTS call returned HTTP 400. 2 identity misses when the caller spelled the name mid sentence (below). |
| 2 (00:24) | 5 of 10 | 4 of 5 | 5 of 5 | 5 runs never started (harness TTS HTTP 400 after 4 tries). 1 run: the caller's pickup line ended before the agent's audio connected, so the probe heard nothing. |
| 3 (00:41) | 10 of 10 | 10 of 10 | 10 of 10 | the table above |

Fixes between batches, test side only: the harness TTS retries a non-200 and reports the
reply body (`bot/tests/harness/tts.py`), the caller keeps saying hello until the agent
hears them (a real caller does), and the caller lines say the name plainly, as the
Demos page script does, instead of spelling the surname mid sentence.

Spelled names: in batch 1, "Aisha Patel, that's P A T E L" was heard "Hal, the CTO"
and the fix "Ayesha. AISHA, Atel"; Linda's fix "No, my name is Linda, L I N D A, Nguyen."
(the harness fix line, same wording as the Demos page's) was heard "... Mayan". Both
ended not_verified. Plain names
were right in every batch. The demo script below says not to spell.

## Demo script (one page)

**Cast.** Agent: one of Chris's demo agents at the portal on the projector, headset on,
signed in as themselves. Caller: a second person with a phone, out of view, reading from
the Demos page script on their own laptop. Presenter: narrates.

**Before the audience arrives (10 min).**
1. Sign in. Rail > Demos: the phone line is +1 (612) 746-7133 (password: ask Chris).
2. Place one throwaway call and hang up after the greeting: it warms the bot pool so the
   first real call gets a preloaded bot (about 3 s to the greeting instead of about 12 s).
3. Settings: check Voice is Flux Alexis, intake LLM phoneLLM mini, assist Claude Haiku.
4. Agent goes Available on Calls.

**1. Settings (1 min).** Presenter opens Settings, edits the greeting (it must still say
AI and that a person will join, IN-2), presses Preview to hear it in Flux Alexis, and
Saves. Optional: Delivery > Speed 1.1x; read backs still slow to 0.9x.
Audience sees: the saved greeting is what the next caller hears.

**2. Maria Lopez, the hero call (4 min).** Caller dials and follows Maria's card:
- To the bot: "My name is Maria Lopez. My date of birth is March twelfth, nineteen
  eighty four. My member ID is four one eight, three zero two." Then "Yes, that's
  right." Then the reason, "Yes, the claim number is seven zero, four one two.", when,
  and what went wrong, as on the card. Say the name plainly; do not spell it.
- Audience sees: the new greeting, the bot asking one thing at a time and reading the ID
  back slowly, then the call ringing in the portal (about 1.5 minutes in).
- Agent presses Enter. The caller says "Hello?" until the agent answers (about 2 s).
- To the agent: "So why did my MRI claim get denied?"
- Audience sees, about 1 s after "denied": hero "Start a retro authorization review for
  70412", the denied claim card, the call plan, and the transcript with the trigger word
  marked. Agent presses the hero's action button (Request retro auth review): the hero
  shows Done, logged as a simulated action. Agent reads the script line, then End.
- Wrap up: the drafted summary and reason code appear about 3 s after End. Agent edits
  a word and presses Save and go available.

**3. Linda Nguyen, the hard name (3 min).** Caller follows Linda's card and says the
name naturally ("Linda Nguyen"). If the bot reads it back wrong, answer once: "No, my
name is Linda Nguyen." (not spelled). Intake moves on after one fix.
- Audience sees: the Verify caller hero ("Heard Linda Yuen, on file Linda Nguyen",
  member ID and DOB matched) and locked record tiles.
- Agent asks the caller to spell the last name, then presses Mark verified: the caller
  panel switches to the member record (Linda Nguyen, Evergreen Medicare Advantage Plus,
  masked ID with Reveal, DOB), "Verified by agent" with no "Not verified" text, caller
  history loads ("Skin biopsy claim denied, CO-242"), the record tiles open (claim
  52976 with its denial), and the hero becomes "Explain the network denial and offer
  an appeal for 52976" if the caller already said why they called. Works when intake
  ended close_match ("Yuen") or not_verified ("Ewan", "Yuan"): member ID and DOB
  matched either way ([screenshots/linda-unlock/](screenshots/linda-unlock/)).
- To the agent: "They said the doctor was out of network. Can I appeal that?"
  Audience sees: "Explain the network denial and offer an appeal for 52976".
- End and Save.

**4. If there is time (2 min each).** James Carter ("I looked at my bank account and I
was charged twice for my premium."): duplicate charge alert and refund hero. Aisha Patel
("So what is an explanation of benefits, exactly? Is it a bill?"): EOB article card.
Ben Cole: gives May seventh instead of May 17; Could not verify keeps records locked.
Do not use Emily Stone (emergency line) without warning the room.

**Fallback if the phone line fails.** On the caller's laptop: Demos > the persona's card
> Start browser call. It opens `/caller?persona=<key>` in a new tab with the same script;
press Start call, allow the microphone, use a headset. Everything after the ring is the
same. If audio fails entirely, walk through the screenshots in
[screenshots/p5-4/](screenshots/p5-4/) (from these runs) and the Architecture page.

## Screenshots (from batch 3, light)

- [maria-hero-light.png](screenshots/p5-4/maria-hero-light.png): run 1, hero 0.74 s after "denied".
- [linda-verify-light.png](screenshots/p5-4/linda-verify-light.png): run 4, Verify caller hero, records locked.
- [linda-unlocked-light.png](screenshots/p5-4/linda-unlocked-light.png): run 4 after Mark verified
  (shows the bug fixed in t_3fb64b24: panel still on the heard name and not verified; see
  [screenshots/linda-unlock/](screenshots/linda-unlock/) for the fixed screen).
- [james-refund-light.png](screenshots/p5-4/james-refund-light.png): run 2, refund hero and duplicate charge alert.

No UI changed in this card; these are evidence from the runs, not new screens.

## Follow ups (nothing fixed here)

1. **Accept to hearing the caller is borderline (median 1.96 s, 5 of 10 over 2 s).**
   About 1.1 s is the Daily join after Enter. Joining the room (muted, no subscriptions)
   while the call rings would take most of that off.
2. **Intake over 2 minutes in 3 of 10 runs.** Bot response p90 4.4 s with 7 to 8 s
   gaps on phoneLLM mini. The P5-9 open item (1 watchdog stall, 2 of 10 over 120 s)
   still shows.
3. **The Demos page fix line for Linda spells the first name** ("Linda, L I N D A,
   Nguyen"). Spelled names inside a sentence went wrong in batch 1. Change the line to
   the plain name.
4. **Wrap up summaries are bookkeeping heavy.** Every draft says the agent "took no
   logged actions" or lists unticked plan steps (the runs never press Accept). Reason
   code was `other` in 5 of 10. Same as the P5-1 open item.
5. **Harness only:** Deepgram `/v1/speak` returned HTTP 400 for test caller lines in
   batches 1 and 2 and not in a probe of 18 calls afterwards. The harness now retries
   and logs the reply body.
6. **Prod check:** one phone call on deepgram-vitals.fly.dev before the demo, reading
   `webhook_received -> bot_launched mode=warm -> greeting_first_audio` and the
   `[settings]` line in the Fly logs (Dutch owns Fly logs).
