# Wrap up summary and reason code (P5-1, PRD AP-9)

When a call the agent took ends, the assist drafts the call summary and a reason
code. The agent edits the summary, keeps or changes the code, and saves. Both the
draft and what the agent saved go on the call record.

## Flow

1. The call ends (agent End or the caller hangs up). The bot's end of call
   (`bot.end_of_call`) reads the wrap up inputs from the assist before anything
   closes, starts the draft, closes the call's resources, and waits for the draft to
   reach the server before it exits (`run_bot` also drains it after the pipeline
   stops).
2. `agent_assist_bot/wrap_up.py` writes the draft and posts it to
   `POST /queue/calls/{session_id}/wrap-up-draft` (bot key, like the other bot
   routes).
3. The portal's wrap up view (`GET /calls/{session_id}/wrap-up`) says
   `draft_pending: true` until the draft lands (at most `DRAFT_WAIT_SECS`, 30 s,
   after the call ended) and the portal reads it again every second. When it lands,
   the summary editor fills with the draft and the suggested reason code is
   preselected. Anything the agent already typed or picked is kept.
4. Save (`POST /agents/{agent_id}/wrap-up {session_id, reason_code, notes, summary}`)
   stores the code, notes, and summary on the call and returns the agent to
   available.

## Inputs (`WrapUpInputs`)

| Input | From |
| --- | --- |
| Transcript | every final line after accept, caller and agent (`AssistSession`, max 400 lines; the prompt keeps the latest 6000 characters) |
| Intake | the intake answers, without date of birth and member ID |
| Accepted actions | each accepted next best step: action id, label, and record ids (simulated, DC-9) |
| Call plan | the pack's steps with done or not done |
| Pack | the pack the assist ended on (it can switch from the intake pack, P4-6) |

Date of birth and member ID never reach the LLM: the keys are left out, and their
values are redacted from the prompt and from the summary.

## Summary rules

- 3 to 5 sentences, facts only, third person, past tense.
- Mentions every accepted action and its reference numbers (CLM-, PA-, PAY- ids).
- No clinical advice: the summary goes through the assist guardrail
  (`assist.clinical_advice`).

The LLM reply is JSON against a schema whose `reason_code` is an enum of the pack's
codes. The engine then checks it:

| Reply | Result |
| --- | --- |
| more than 5 sentences | cut to 5 |
| an accepted action's reference missing | a "Reference numbers: ..." sentence is added |
| fewer than 3 sentences, clinical advice, not JSON, error, timeout (15 s), no LLM | `fallback_summary`: written from the same facts without an LLM |
| sentence with a date of birth or member ID | dropped |

`source` on the draft is `llm` or `fallback`.

## Reason code

Each pack lists its codes in `wrap_up.reason_codes`. A code may list `actions`: next
best step action ids that suggest it (docs/packs.md). The rule suggestion is the code
listing the last accepted action, else the pack's first code. The LLM may pick
another code from the pack (it sees the rule suggestion); a code outside the pack
falls back to the rule suggestion.

| Pack | Action | Suggested code |
| --- | --- | --- |
| claims_status | `request_retro_auth_review`, `start_network_appeal` | `appeal_started` |
| claims_status | `contact_provider_for_info`, `request_auth_clinical_notes` | `callback_needed` |
| claims_status | none | `claim_status_given` |
| account_payments | `submit_premium_refund` | `refund_submitted` |
| account_payments | none | `payment_explained` |
| general_question | none | `question_answered` |

The server reads the codes from the draft's pack, so the view and the Save check use
the same list the bot drafted against. Without a draft it uses the active pack
(`INTAKE_PACK`).

## Call record

Migration `0007_wrap_up_summary.sql` adds to `calls` (no new table; the `calls` RLS
from 0002 applies):

| Column | |
| --- | --- |
| `wrap_up_draft` jsonb | `{summary, reason_code, pack_id, source, drafted_at}` |
| `wrap_up_summary` text | the summary as the agent saved it |

`wrap_up_reason` and `wrap_up_notes` (0004) keep the saved code and notes. The queue
write-through (`QUEUE_PERSIST=1`) fills all four.

## Config

| Env | Default | |
| --- | --- | --- |
| `WRAP_UP_LLM_MODEL` | `ASSIST_LLM_MODEL`, then `LLM_MODEL`, then the factory default | wrap up model outside a call only; a call uses its `llm_wrapup` setting (P5-9) |

## Tests

- `bot/tests/test_wrap_up.py` (fake LLM): prompt inputs (transcript, intake, actions,
  plan, codes), no date of birth or member ID, the schema enum, the LLM's code and
  the rule fallback, sentence limits, added references, guardrail, bad replies,
  errors, no LLM, `suggest_reason_code` per pack, reference numbers, the fallback
  summary, and the reporter (posts once, survives a failed post).
- `bot/tests/test_assist_runtime.py`: wrap up inputs from a call (lines after accept,
  accepted action with its record, plan ticks). `test_bot.py`: draft starts before
  closing, waits before exit, nothing without an agent. `test_queue_hold.py`: the
  draft POST. `test_packs.py`: reason code `actions`.
- Server: `test_wrap_up_api.py` (draft route, view fields, pending, pack codes, save
  with summary, bot key), `test_queue_service.py`, `test_queue_store.py` (columns).
- Portal: `components/WrapUp.test.tsx` (editor, preselected code, late draft, edits
  kept), `CallFlow.test.tsx` (polling until the draft lands, Save with the summary).
- Live: `server/tests/test_wrap_up_summary_live.py`: a Maria Alvarez call through
  wrap up; prints the drafted summary and code.
