# Deepgram LLMs per job (card t_dd6ffd58, P5-9, PRD DC-11)

> Note (Nov 16, P5-10): demo IDs and names changed after this was written. Member IDs
> are now 6 digits (Maria Alvarez EVM100231 is now Maria Lopez 418302) and claim
> numbers 5 digits (CLM-900001 is now 70412); see docs/db.md. The results below are
> kept as measured, with the old IDs.

Result in one line: **phoneLLM mini is the intake default** (identity 10 of 10, same as
Claude Haiku, and it answers the caller 0.43 s sooner at the median); **Claude Haiku
stays the default for assist and wrap up**, where phoneLLM mini did not do the job
(no assist cards from its LLM pass; its wrap up reply was not usable JSON 10 of 10
times). Every job can be switched in Settings to Claude Haiku, phoneLLM mini, or
Think-Fast.

Date: 2026-11-16. Pipecat 1.15.0. Deepgram `/v1/think` (Early Access): models
`phonellm-mini-alpha-2-fp8` and `think-fast`; Anthropic `claude-haiku-5-5`. Intake STT
Nova-3, Flux TTS Alexis, Daily `dg-chriscat`, Supabase Postgres (throwaway schema per
run). Host: 2 CPU; every live batch ran alone, one after the other (load average 1.4 to
3.8 at batch start).

Deepgram LLMs are Early Access: contract customers only, free, not for production
traffic, global endpoint only, model ids may change
(developers.deepgram.com/docs/llm). phoneLLM mini runs with `mip_opt_out: true` (not
stored for model training). Think-Fast cannot opt out: every request is stored for
Deepgram model training, and the Settings page says so.

## What was built

- **Provider** (`bot/agent_assist_bot/llm_deepgram.py`): Pipecat's `OpenAILLMService`
  with an OpenAI client that reshapes every chat completion request before it is sent:
  URL `https://agent.deepgram.com/v1/think`, `Authorization: Token <DEEPGRAM_API_KEY>`;
  only the eight fields `/v1/think` accepts (Pipecat's `tool_choice`, `max_tokens`,
  `max_completion_tokens`, `response_format` and the rest are dropped); `developer`
  messages become `system`; content parts become one string; `stream_options` exactly
  `{"include_usage": true}`. phoneLLM: `mip_opt_out` true, temperature 0,
  `reasoning_effort` "none". Think-Fast: no `mip_opt_out`, no `reasoning_effort`.
  `bot/tests/test_llm_deepgram.py` records the real request with an httpx mock.
- **Spoken text**: `<think>` / `<reasoning>` blocks (also split across chunks) and
  markdown marks are removed from streamed text before TTS. None were seen in any run.
- **JSON without `response_format`**: assist and wrap up ask for a response schema.
  `/v1/think` rejects `response_format`, so the schema goes into the system message
  and again at the end of the last user message (phoneLLM ignored it in the system
  message alone), and a code fence around the reply is removed.
- **No `tool_choice`**: the intake confirm steps cannot force a function call on the
  Deepgram models. The existing fallbacks cover it (a plain yes confirms in code; three
  caller turns with no function hand off as `intake_stalled`).
- **Empty `say_to_caller`** (found in the first phoneLLM batch, fixed here): phoneLLM
  sometimes called `say_to_caller({})` in the summary read back, twice in a row, so the
  caller heard nothing and the intake timed out. Now an empty call is refused once with
  an error to the LLM; the second time the code speaks a fixed line (the summary read
  back from the captured answers, never DOB, member ID, or claim number; elsewhere
  "Sorry, could you say that again?"). In the final batch this saved Linda run 1 and
  Daniel run 1.
- **Settings**: `llm_intake`, `llm_assist` (also the Ask bar), `llm_wrapup`
  (migration 0012). Live harness knobs: `LLM_INTAKE`, `LLM_ASSIST`, `LLM_WRAPUP`.

Direct probe of the provider (`bot/tests/test_llm_deepgram_live.py`, passed): streamed
reply 0.33 s on both models; phoneLLM called `save_identity` with
`{'full_name': 'Maria Alvarez', 'dob': '1984-03-12', 'member_id': 'EVM100231'}` in
0.61 s with a `developer` message and `tool_choice` in the context (both handled);
`run_inference` with a schema returned `{"topic": "claims"}` on both models.

## Intake: identity harness, 5 personas x 2 runs

`bot/tests/test_live_intake.py`, `LLM_INTAKE` set the way a cold bot gets it from
Settings. Same code, same day, phoneLLM first, then Claude; Think-Fast 5 x 1 for the
record. Response gap: end of the caller's voice to the bot's first audio. LLM first
byte: Pipecat's TTFB metric for the LLM service in the bot logs.

### phoneLLM mini (`phonellm-mini-alpha-2-fp8`)

| persona | run | result | correct | intake s | gap median s | heard name | flags |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Maria | 1 | verified | yes | 98.5 | 2.57 | Maria Alvarez | |
| Maria | 2 | verified | yes | 80.6 | 3.45 | Maria Alvarez | intake_stalled |
| James | 1 | verified | yes | 85.7 | 2.76 | James Chen | |
| James | 2 | verified | yes | 88.6 | 2.54 | James Chen | |
| Aisha | 1 | verified | yes | 126.8 | 2.83 | Aisha Patel | |
| Aisha | 2 | verified | yes | 119.2 | 2.37 | Aisha Patel | |
| Linda | 1 | verified | yes | 121.9 | 2.31 | Linda Nguyen | |
| Linda | 2 | verified | yes | 86.0 | 2.33 | Linda Nguyen | |
| Daniel | 1 | not_verified | yes | 103.2 | 2.47 | Daniel Okafor | identity_not_verified |
| Daniel | 2 | not_verified | yes | 81.2 | 2.42 | Daniel Okafor | identity_not_verified |

### Claude Haiku (`claude-haiku-5-5`, same day baseline)

| persona | run | result | correct | intake s | gap median s | heard name | flags |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Maria | 1 | verified | yes | 104.5 | 3.23 | Maria Alvarez | |
| Maria | 2 | verified | yes | 103.9 | 3.33 | Maria Alvarez | |
| James | 1 | verified | yes | 85.0 | 2.92 | James Chen | |
| James | 2 | verified | yes | 117.2 | 2.85 | James Chen | |
| Aisha | 1 | verified | yes | 104.6 | 2.60 | Aisha Patel | |
| Aisha | 2 | verified | yes | 108.6 | 2.69 | Aisha Patel | |
| Linda | 1 | verified | yes | 107.6 | 2.81 | Linda Nguyen | |
| Linda | 2 | verified | yes | 121.9 | 2.82 | Linda Nguyen | |
| Daniel | 1 | not_verified | yes | 86.2 | 3.15 | Daniel Okafor | identity_not_verified |
| Daniel | 2 | not_verified | yes | 106.2 | 2.77 | Daniel Okafor | identity_not_verified |

### Think-Fast (`think-fast`, one batch for the record)

| persona | run | result | correct | intake s | gap median s | heard name | flags |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Maria | 1 | verified | yes | 100.1 | 2.70 | Maria Alvarez | |
| James | 1 | verified | yes | 99.7 | 2.18 | James Chen | |
| Aisha | 1 | not_found | **no** | 115.0 | 2.50 | Ayesha Patel | intake_stalled, identity_not_found |
| Linda | 1 | verified | yes | 114.2 | 3.52 | Linda Nguyen | |
| Daniel | 1 | not_verified | yes | 90.8 | 3.86 | Daniel Okafor | identity_not_verified, asked_for_person |

### Side by side

| | phoneLLM mini | Claude Haiku | Think-Fast (5 runs) |
| --- | --- | --- | --- |
| Identity correct | **10 of 10** | **10 of 10** | 4 of 5 |
| Reached the queue | 10 of 10 | 10 of 10 | 5 of 5 |
| Intake under 120 s | 8 of 10 | 9 of 10 | 5 of 5 |
| Intake seconds, median | 93.5 | 105.4 | 100.1 |
| Caller stop to bot first audio, median | **2.46 s** (n=70) | 2.89 s (n=73) | 2.93 s (n=35) |
| Caller stop to bot first audio, p90 | **2.96 s** | 3.68 s | 5.05 s |
| LLM first byte, median | **0.310 s** (n=175) | 0.413 s (n=180) | 0.315 s (n=86) |
| LLM first byte, p90 | 0.571 s | 0.506 s | 0.481 s |
| Stalled (watchdog handoff) | 1 | 0 | 1 |
| Spoken reasoning, think tags, markdown | none | none | none |

What the logs show:

- **phoneLLM calls the Flows functions.** Every function the intake uses was called
  with the right arguments across the batch: `save_identity`, `verify_identity`,
  `correct_identity` (Maria 1, Aisha 1 and 2, Daniel 1), `save_reason`, `save_answer`
  for each pack question, `confirm_summary`. `request_person` and `report_emergency`
  were not exercised: no script asks for a person or describes an emergency (Think-Fast
  called `request_person` once for Daniel). The live provider probe shows phoneLLM
  calls a tool from the list it is given; the escape functions are in that list on
  every intake node.
- **Maria run 2 stalled on the claim number.** The caller's "CLM, nine zero zero, zero
  zero one" arrived as three turns; phoneLLM asked for the full number again instead of
  calling `save_answer`, and the watchdog handed the call off after three turns
  (`intake_stalled`, identity verified, reason saved). The caller still reached a
  person; the agent sees the flag.
- **Over 120 s:** Aisha 1 (126.8 s) and Linda 1 (121.9 s). Linda 1 is the run where
  phoneLLM sent `say_to_caller({})` twice in the read back and the code spoke the read
  back; Aisha 1 spent a correction on her name.
- **Wording:** one phoneLLM line read the date of birth aloud to Maria ("let me just
  confirm the date of birth march twelfth nineteen eighty four"), against the role
  message; one Claude line did the same for James. Both are LLM wording, not the fixed
  identity read back line. AI disclosure is the fixed greeting on every model.
- **Think-Fast** calls the intake functions inside Flows (Dutch's single save_identity
  probe did not), but on Aisha it heard the member ID as "EDM100233", asked again three
  times without calling `correct_identity`, and the watchdog handed off as not_found.
  On Linda it never read the summary back through `say_to_caller` (it said "thanks i
  have that noted" and "got it that's enough for me" in plain text), and the caller's
  yes was confirmed by the code fallback. Not a default candidate.

**Pass rule: identity no worse than Claude (10 of 10 vs 10 of 10), no invented facts
(no artifacts; every saved field matches what the caller said), timing no worse than
0.5 s slower at median (0.43 s faster). Intake default: phoneLLM mini.** Open risk:
the one stall and one more run over 120 s than Claude in this batch.

## Assist: the three Phase 4 calls (claims, payments, general)

`server/tests/test_phase4_exit_live.py` with `LLM_ASSIST`, one run per model.

| | phoneLLM mini | Claude Haiku |
| --- | --- | --- |
| Claims hero | `nbs:retro_auth_review:CLM-900001`, 0.889 s after "denied" | same, 0.871 s |
| Payments hero | `nbs:duplicate_premium_refund:PAY-000064`, 1.747 s after "twice" | same, 1.66 s |
| General card | `kb:KB-CL-010`, 1.585 s after "benefits" | same, 1.551 s |
| Ask "Show the last 3 claims" | CLM-900001, CLM-900002 in 0.227 s | same in 0.252 s |
| Ask "Any denied claims?" | CLM-900001 in 0.211 s | same in 0.24 s |
| Cards added by the LLM pass | **none** (3 passes: 2 "LLM used every round on tools; no proposal", 1 empty) | KB-CL-002 + KB-CL-009 article card on the claims call |
| LLM pass time | 1.5 to 3.1 s | 3.3 to 5.4 s |

The heroes, record cards, alerts, and both Ask answers come from the deterministic
trigger and Ask paths, so they are the same on both models, and no card fact can come
from the model (the engine re-reads each record and renders the pack's template).
The difference is the LLM pass: on these calls phoneLLM never produced a usable
proposal. After the JSON fix above, a rerun on phoneLLM (claims and payments; the
general call failed at intake, Claude intake, identity not_verified, unrelated) again
added no card: one pass returned nothing new, one used every round on tools. Offline,
on the same claims request with the unit tests' fake connectors, phoneLLM did propose
`retro_auth_review` for CLM-900001 on its third round, so it can follow the format, but
not reliably in the live calls. **Assist default: Claude Haiku.**

## Wrap up: the Maria call

`server/tests/test_wrap_up_summary_live.py` with `LLM_WRAPUP`, final code, one run each
(both passed: 3 to 5 sentences, CLM-900001 present, no DOB or member ID, code
`appeal_started`).

phoneLLM mini (draft in the editor 1.71 s after End):

> Maria Alvarez is calling about an MRI claim at Riverside Imaging Center, about a month
> ago, that was denied because the letter cited no prior authorization. Identity
> verified and claim number captured. The agent opened a retro authorization review for
> CLM-900001, and if approved the claim will be reprocessed and a letter sent within
> thirty days.

Claude Haiku (2.93 s after End):

> The agent verified the identity of Maria Alvarez and reviewed her claim CLM-900001 for
> an MRI at Riverside Imaging Center, about a month ago, which was denied for lack of
> prior authorization. The agent requested a retro authorization review on CLM-900001.
> If approved, the claim will be reprocessed and Maria will receive a letter within
> thirty days. The explanation of the denial reason in plain words was not completed on
> the call.

Every fact in both is from the call (intake answers, the accepted action and its
record, the agent's closing line "you'll get a letter within 30 days"). phoneLLM wrote
present tense and a fragment, against the prompt's past tense rule.

Before the JSON fix the live phoneLLM run fell back to the template summary ("LLM reply
not usable": it answered in prose). Repeating the drafter 10 times on the unit test's
Maria inputs (final code):

| | phoneLLM mini | Claude Haiku | Think-Fast |
| --- | --- | --- | --- |
| LLM draft used (valid JSON, 3 to 5 sentences) | **0 of 10** | 10 of 10 | 5 of 10 |
| Reason code appeal_started | 10 of 10 | 10 of 10 | 10 of 10 |
| CLM-900001 in the summary | 10 of 10 | 10 of 10 | 10 of 10 |
| Median time | 0.24 s | 1.01 s | 0.53 s |

phoneLLM at temperature 0 gives the same prose reply every time on those inputs, so the
agent gets the template fallback (correct, but not a written summary). The live call
worked, the shorter inputs did not: not reliable enough. **Wrap up default: Claude
Haiku.** (The reason code and references are right either way: the code is checked
against the pack and the fallback names every record.)

## Decision

| Job | Default | Why |
| --- | --- | --- |
| Intake | `phonellm-mini-alpha-2-fp8` | identity 10 of 10 like Claude, 0.43 s faster reply at the median, no spoken artifacts |
| Assist and Ask | `claude-haiku-5-5` | phoneLLM's LLM pass added no cards on the live calls |
| Wrap up | `claude-haiku-5-5` | phoneLLM's reply was not usable JSON 0 of 10 on the Maria inputs |

All three are switchable in Settings (Voice > Thinking), applied to the next call.

Follow ups (not done here): a JSON mode or `response_format` on `/v1/think` would likely
fix assist and wrap up on phoneLLM; `tool_choice` support would let the confirm steps
force a function call again; the intake stall on a number split over turns could be
handled like the identity step (a longer end of turn while a question expects a number).

## How to rerun

    set -a; . .env; set +a
    LLM_INTAKE=phonellm-mini-alpha-2-fp8 INTAKE_RUNS=2 INTAKE_RESULTS=/tmp/i.jsonl \
        uv run pytest -m live bot/tests/test_live_intake.py -s -v
    LLM_ASSIST=phonellm-mini-alpha-2-fp8 PLAYWRIGHT_BROWSERS_PATH=... uv run --with playwright \
        pytest -m live server/tests/test_phase4_exit_live.py -s -v
    LLM_WRAPUP=phonellm-mini-alpha-2-fp8 PLAYWRIGHT_BROWSERS_PATH=... uv run --with playwright \
        pytest -m live server/tests/test_wrap_up_summary_live.py -s -v
    uv run pytest -m live bot/tests/test_llm_deepgram_live.py -s -v
