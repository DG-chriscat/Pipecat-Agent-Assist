# Intake flow

PRD IN-3 to IN-9. Code: `bot/agent_assist_bot/intake.py` (flow), `calls.py` (call
record + identity check), `packs.py` + `packs/*.yaml` (use case packs).

## Steps

A Pipecat Flow (`pipecat.flows`, shipped inside pipecat-ai 1.15; the standalone
`pipecat-ai-flows` package is deprecated and pinned to pipecat-ai < 1.5):

1. **greeting**: fixed `tts_say` line, no LLM call (IN-1, IN-2).
2. **identity**: full name, date of birth, member ID, then `save_identity` (saved, not
   yet checked). Entering this node turns the identity STT tuning on (see below).
3. **identity_confirm**: fixed `tts_say` read back, "Thanks. I have Maria Lopez,
   member ID four one eight, three zero two. Is that right?" (never the DOB).
   `verify_identity` runs the check (IN-6) and turns the tuning off. The caller gets
   one fix (`MAX_IDENTITY_FIXES = 1`): a `correct_identity`, or one free-form re-ask via
   `say_to_caller`. After that the bot checks what it has and moves on, with no second
   read back (2 minute target). Asking for a person or an emergency
   during the read back still checks what was saved, so the agent sees a result.
   In the identity node, a `save_identity` missing a field is refused once; the second
   one saves what was given and checks it, giving `incomplete`.
4. **reason**: `save_reason`.
5. **one node per pack question**: `save_answer`, in pack order.
6. **readback**: the bot reads a short summary (no claim number, member ID, or DOB);
   `correct_field` fixes a field and stays; `confirm_summary` ends intake (IN-8).
7. **handoff**: "passing you to a person" line; no more questions.

On every intake node the LLM also has:

- `request_person` (IN-7): flags `asked_for_person`, skips straight to handoff
  with whatever was captured.
- `report_emergency` (IN-9): says the fixed "hang up and call 911" line, flags
  `emergency`, saves the caller's words, then hands off. The system prompt forbids
  medical advice. In the identity and identity_confirm steps it also needs explicit
  emergency words (chest pain, can't breathe, bleeding, overdose, suicide, 911,
  ambulance, and similar: `intake._EMERGENCY_WORDS`) in the caller's last turns or the
  LLM's description; otherwise it is refused and intake continues. Live, the LLM called
  it on a plain name, date of birth, and member ID answer (PR #48, identity v2 harness).

## Call record

The bot creates its `calls` row (keyed by `--session-id`) when the caller joins and
writes each answer into `calls.intake` the moment its function call runs (IN-5):
`full_name`, `dob`, `member_id`, `reason`, one key per pack question, `summary`, and
`emergency` when reported. `identity_result` is `verified`, `close_match`,
`not_verified`, `not_found`, or `incomplete`; anything but verified adds the flag
`identity_close_match`, `identity_not_verified`, `identity_not_found`, or
`identity_incomplete` and the caller continues (IN-6). Every caller who is not
verified gets the agent's verification step on handoff (IN-13, `docs/assist.md`).

Identity rules (`calls.match_identity`): member ID found and name + DOB match it is
verified; member ID and DOB exact and the name close by spelling is close_match;
member ID found but otherwise different is not_verified; unknown ID but name + DOB
match exactly one member is not_verified; anything else is not_found. Any of the
three still missing after the one fix is incomplete. Name alone never identifies
(two seed members are named Mark Hill).

Close by spelling (`name_part_close`): the name has a first and last part; one part
matches exactly and the other is within 3 edits (Levenshtein) with similarity at
least 0.5. From the STT spike: "Linda Yuen" for Linda Nguyen, "Ayesha Patel" for Aisha
Patel, "Daniel Lokafor" are close; "Linda Wayne", "Daniel Luca Ford" are not. A close
match never unlocks records by itself; the agent decides.

## ID formats (P5-10, Nov 16)

Demo data is set up so speech to text hears it right:

- Member IDs are 6 digits, said in two groups of three ("four one eight, three zero
  two" for 418302). No leading 0, no digit twice in a row, and no group shared by two
  members. `calls.normalize_member_id` keeps the digits, written or spoken ("418 302",
  "four one eight, three oh two", "four eighteen, three oh two" all give 418302);
  `is_valid_member_id` checks there are exactly 6. A `save_identity` with another digit
  count is refused once ("member_id must be 6 digits"), like a missing field.
- Claim numbers are 5 digits, said as two and three ("seven zero, four one two" for
  70412). `intake.normalize_claim_number` saves a `claim_number` answer as the digits
  when it holds exactly 5; anything else ("unknown") is kept as said.
- The read back says the member ID in groups (`speak_member_id`), and the slow read
  back pacing (P5-8) spots bare 5 and 6 digit IDs as well as spelled out digits.
- Pack keyterms no longer carry ID prefixes (EVM, CLM, PAY are gone); plan, provider,
  and denial terms stay.

At the end the row is `ready_for_queue` with `intake.summary`, and the bot calls
`HandoffHook.on_intake_complete(IntakeHandoff)`. The queue (P2-4) implements the
hook; the default `NoopHandoff` only logs. Store and hook errors are logged and never
drop the caller.

Env: `DATABASE_URL` (in memory when unset), optional `DATABASE_SCHEMA`,
`INTAKE_PACK` (default `claims_status`).

## Identity STT tuning

`bot/agent_assist_bot/stt_tuning.py`, measured in `docs/spikes/identity.md`. While the
identity and identity_confirm nodes are active, the intake STT (not the listen only
transcript branches) gets Deepgram `numerals=true`, `smart_format=true`,
`endpointing=800` ms, and the intake VAD `stop_secs` goes from 0.2 s to 0.8 s, so a
pause between digit groups does not end the caller's turn. Everything returns to the
defaults when the check runs or intake ends. The Deepgram change is an
`STTUpdateSettingsFrame(service=<intake stt>)`; Pipecat 1.15 reconnects the Deepgram
stream between turns and buffers audio meanwhile.

The intake STT also carries the active pack's fixed `keyterms` (P4-2, Nova 3 keyterm
prompting) for the whole call.

## Packs

`packs/<id>.yaml` holds the wording for identity, reason, and read back, plus the use
case questions (IN-4). Each question needs a snake_case `key` (not one of
`full_name`, `dob`, `member_id`, `reason`, `summary`), the `ask` text, and a
`description` the LLM uses to fill the answer. Edit the YAML; no code changes. The
loader validates on start (`load_pack`). The assist sections of a pack (triggers,
connectors, cards, call plan, next best steps, keyterms) are in docs/packs.md.

## Tests

- `bot/tests/test_packs.py`, `test_identity.py`, `test_intake.py`, `test_bot.py`: no
  network.
- `bot/tests/test_calls_db.py`: needs `DATABASE_URL`; throwaway schema per test.
- `bot/tests/test_live_intake.py` (`-m live`): real bot process, S1 harness caller
  playing Deepgram TTS lines as each of the 5 demo personas (docs/db.md), throwaway
  schema. At the identity read back the scripted caller says yes or corrects what the
  bot got wrong. Needs all keys plus `DATABASE_URL`. `INTAKE_RUNS=2` runs each persona
  twice, `INTAKE_PERSONAS=maria_lopez,...` picks personas, `INTAKE_RESULTS=<file>`
  appends one JSON line per run.

```
uv run pytest -m live bot/tests/test_live_intake.py -s -v
INTAKE_RUNS=2 INTAKE_RESULTS=/tmp/identity.jsonl uv run pytest -m live bot/tests/test_live_intake.py -s -v
```
