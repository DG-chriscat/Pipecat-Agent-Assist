# Intake flow

PRD IN-3 to IN-9. Code: `bot/agent_assist_bot/intake.py` (flow), `calls.py` (call
record + identity check), `packs.py` + `packs/*.yaml` (use case packs).

## Steps

A Pipecat Flow (`pipecat.flows`, shipped inside pipecat-ai 1.15; the standalone
`pipecat-ai-flows` package is deprecated and pinned to pipecat-ai < 1.5):

1. **greeting**: fixed `tts_say` line, no LLM call (IN-1, IN-2).
2. **identity**: full name, date of birth, member ID, then `verify_identity` (IN-6).
3. **reason**: `save_reason`.
4. **one node per pack question**: `save_answer`, in pack order.
5. **readback**: the bot reads a short summary; `correct_field` fixes a field and
   stays; `confirm_summary` ends intake (IN-8).
6. **handoff**: "passing you to a person" line; no more questions.

On every intake node the LLM also has:

- `request_person` (IN-7): flags `asked_for_person`, skips straight to handoff
  with whatever was captured.
- `report_emergency` (IN-9): says the fixed "hang up and call 911" line, flags
  `emergency`, saves the caller's words, then hands off. The system prompt forbids
  medical advice.

## Call record

The bot creates its `calls` row (keyed by `--session-id`) when the caller joins and
writes each answer into `calls.intake` the moment its function call runs (IN-5):
`full_name`, `dob`, `member_id`, `reason`, one key per pack question, `summary`, and
`emergency` when reported. `identity_result` is `verified`, `not_verified`, or
`not_found`; failed checks add the flag `identity_not_verified` or
`identity_not_found` and the caller continues (IN-6).

Identity rules (`calls.match_identity`): member ID found and name + DOB match it is
verified; member ID found but name or DOB differ is not_verified; unknown ID but
name + DOB match exactly one member is not_verified; anything else is not_found.
Name alone never identifies (two seed members are named James Chen).

At the end the row is `ready_for_queue` with `intake.summary`, and the bot calls
`HandoffHook.on_intake_complete(IntakeHandoff)`. The queue (P2-4) implements the
hook; the default `NoopHandoff` only logs. Store and hook errors are logged and never
drop the caller.

Env: `DATABASE_URL` (in memory when unset), optional `DATABASE_SCHEMA`,
`INTAKE_PACK` (default `claims_status`).

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
  playing Deepgram TTS lines as persona Maria Alvarez, throwaway schema. Needs all
  keys plus `DATABASE_URL`.

```
uv run pytest -m live bot/tests/test_live_intake.py -s -v
```
