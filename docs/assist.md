# Real time assist (P4-3)

PRD "Requirements: real time assist and data", steps 1 to 5; DC-6, DC-7, DC-9, DC-10;
AP-6, AP-7, AP-11, AP-12; guardrails. The portal side (P4-4) renders the messages
below.

After an agent accepts, the bot is listen only (P3-4). Every final transcript line,
caller or agent, goes to the assist engine, which sends ranked cards to the agent's
portal only. The bot never speaks to the caller in this mode: the engine has no audio
path and never queues pipeline frames.

## Code

| File | What |
| --- | --- |
| `bot/agent_assist_bot/assist.py` | `AssistEngine`: trigger path, ranking, hero, rate limit, call plan, guardrail, latency log. |
| `bot/agent_assist_bot/assist_llm.py` | `LLMProposer`: the assist LLM (`create_llm`, same provider) with a strict JSON response schema and the pack's connector tools. |
| `bot/agent_assist_bot/assist_contract.py` | Message contract v1 (below) and feedback parsing. |
| `bot/agent_assist_bot/assist_runtime.py` | `AssistSession`: starts on accept, feeds lines, delivers to the agent only, logs `assist_events`. |
| `db/migrations/0004_assist_events.sql` | `assist_events` table (RLS on). |

Wiring (`bot.build_worker`): `TranscriptRelay(on_final=FanOut(recorder.add,
assist.on_final))` in both listen branches; `register_handlers(assist=...)` starts it
on `agent_accepted`, forwards app messages (feedback) and participant joins (resync).
The listen branches' Deepgram streams boost every pack's fixed `keyterms` (deduped,
max 100), since the pack is not known when they open.

## How one card is made

1. **Trigger path (deterministic, no LLM).** The line is matched against the active
   pack's next best step rules (`pack.rules_triggered`). For each triggered rule the
   engine searches the rule's connector (one connector per rule in v1), keeps rows
   where `rule.applies(facts)`, and renders the best one (`rule.render`). The best
   step is offered at once, after a single connector round trip; then its record
   card (the pack's most specific `record` template matching the row), the pack's
   `alert` templates (if the line has a pack trigger), and any other triggered rules
   follow as supporting cards.
2. **LLM path (debounced 0.6 s, off the hot path).** `LLMProposer` gets the rolling
   window (last 12 lines, both roles labeled), the intake (date of birth removed),
   the pack's rules and templates, the ids already on screen, and the pack's
   connector tools. It may call those tools (the engine refuses anything else; the
   connector session scopes to the verified member) and returns only ids:
   `{"next_steps": [{"rule_id", "records": [{"connector", "id"}]}], "cards":
   [{"template_id", "ids"}]}`. The engine re-reads every named record itself, checks
   `rule.applies`, requires that one of the rule's trigger phrases was heard in the
   window, and renders the pack's templates. Nothing the model writes reaches the
   screen. Up to 3 rounds per pass. Model: `ASSIST_LLM_MODEL`, else `LLM_MODEL`, else
   the factory default.
3. **Pack switching.** A line whose triggers match another non fallback pack more
   than the active one switches packs (new `plan_update`).

### Ranking (PRD step 5)

Rank = rule `priority` x 10, +5 when the record id was named in the call (intake or
transcript), +3 when one of the pack's alert templates flags that record. One hero:

- A new next step becomes hero only if there is none or it ranks strictly higher.
  The old hero is then sent as a supporting card (`card_added`, kind `next_step`).
- When the hero is accepted or dismissed, the best supporting next step is promoted
  (`card_removed` reason `promoted`, then `hero` reason `accepted`/`dismissed`), or
  the hero clears (`card: null`, "Listening").
- Accepted and dismissed card ids are never suggested again in the call.

### Rate limit (DC-7)

At most `ASSIST_MAX_CARDS_PER_MIN` (default 4) new cards (hero or supporting) per
rolling 60 s. Extra cards wait in a queue, best first (next steps before others), and
an `assist_status` `rate_limited` with `pending` tells the portal. A timer sends them
when the window frees up. Promotions and resyncs do not count.

### Latency (DC-6)

For every card sent the engine logs `[assist] card <id> ... latency_ms=<n>` and writes
an `assist_events` `card_sent` row with `latency_ms` = send time minus the bot's
timestamp of the final transcript line that triggered it. Over 3000 ms is flagged
`OVER DC-6` in the log. The live test measures end to end: the caller's trigger word
in the played audio to the hero arriving on the agent's Daily client.

### Call plan (AP-12)

Steps tick when an **agent** line contains a `done_when.phrases` phrase (`by:
"transcript"`) or the agent accepts an action listed in `done_when.actions` (`by:
"action"`). Caller lines never tick. Ticks are sticky. The portal owns manual
tick/untick.

### Guardrail

No clinical advice: rendered headline, why, and script (and article/alert text) are
checked against medication, dosage, diagnosis, and treatment-advice patterns
(`clinical_advice`). A match blocks the card and logs a `guardrail` event. A test runs
every shipped rule through the check. Every card has `ai_suggested: true`, its trigger,
and its source.

## Message contract v1

### Bot to portal

One Daily app message per update, addressed to the agent's participant id only
(`DailyOutputTransportMessageUrgentFrame(participant_id=agent_id)`), never `*`. With
no agent in the room nothing is sent; when the agent joins (or rejoins) the full state
is sent again (`assist_status`, `plan_update`, `hero`, every supporting card).

```
{"label": "rtvi-ai", "type": "server-message",
 "data": {"type": "assist", "v": 1, "event": "<event>", "seq": 7, "ts": 1794800000.12, ...}}
```

`seq` increases by one per message in a call; `ts` is the bot's wall clock (epoch s).
The portal ignores messages with `v` other than 1.

| event | fields |
| --- | --- |
| `assist_status` | `state`: `listening`, `rate_limited` (with `pending`: n), `error` (with `message`), `stopped`; `pack_id`. |
| `plan_update` | `pack_id`, `pack_name`, `steps`: `[{id, label, done, by}]` (`by`: `transcript`, `action`, or null), `done`, `total`. Always the whole plan. |
| `hero` | `card` (a card, or null for "Listening"), `reason`: `new`, `higher_rank`, `accepted`, `dismissed`; `replaces`: previous hero id or null. |
| `card_added` | `card`: a supporting card. Re-adding an id replaces it in place. |
| `card_removed` | `card_id`, `reason`: `dismissed`, `accepted`, `promoted` (moved to hero). |

Card:

```
{
  "id": "nbs:retro_auth_review:CLM-900001",
  "kind": "next_step" | "record" | "article" | "alert",
  "title": "Next best step",
  "pack_id": "claims_status",
  "rank": 105,
  "ai_suggested": true,
  "trigger": {"phrase": "denied", "text": "So why did my MRI claim get denied?",
              "role": "caller", "line_id": "caller-1", "start": 28, "end": 34,
              "heard_at": 1794800787.43},
  "source": {"label": "Claims system, KB-CL-002 and KB-CL-009",
             "records": [{"connector": "claims", "id": "CLM-900001"}]},
  "rule_id": "retro_auth_review",        // next_step only
  "template_id": null,                   // record, article, alert
  "body": {...}
}
```

- `trigger.line_id` and `start`/`end` index the transcript line (P3-4 `line_id`), so
  the portal can underline the trigger words with `highlight()`. For LLM proposed
  cards with no phrase, `phrase`, `start`, and `end` are null.
- `body` for `next_step`: `headline`, `why`, `script` ("Say this"), `action`: `{id,
  label}` (simulated, DC-9).
- `body` for `record`, `article`, `alert`: `fields` (column order), `rows` (up to 5,
  only those fields, raw connector values: ISO dates, numbers), `summary` (filled
  template line or null).
- Card ids: `nbs:<rule>:<record>`, `card:<template>` (record cards), `kb:<article
  ids>`, `alert:<template>:<record ids>`.

### Portal to bot: feedback

RTVI client message from the agent's client (`client.sendClientMessage(
"assist_feedback", {...})` in the Pipecat client SDK), which arrives as a Daily app
message. Only messages whose Daily sender is the agent's participant id are used; the
caller's and the REST API's are ignored.

```
{"label": "rtvi-ai", "type": "client-message", "id": "...",
 "data": {"t": "assist_feedback",
          "d": {"v": 1, "card_id": "nbs:retro_auth_review:CLM-900001",
                "action": "accept" | "dismiss" | "pin" | "unpin" | "rate",
                "rating": "up" | "down"}}}        // rating with rate only
```

- `accept` on a next step logs the simulated action, ticks plan steps listing it, and
  promotes the next hero. Nothing is written to source systems (DC-9).
- `dismiss` removes the card (hero: promote or clear).
- `pin`, `unpin`, `rate` are logged; pinning is portal state.

## Call record: `assist_events`

| column | |
| --- | --- |
| `event_id` | identity, order |
| `call_id` | `calls.call_id`, cascade delete |
| `kind` | `card_sent`, `accept`, `dismiss`, `pin`, `unpin`, `rate`, `guardrail` |
| `card_id` | card id (null for some guardrail rows) |
| `data` | jsonb. `card_sent`: kind, rank, pack_id, trigger_phrase, trigger_line_id, latency_ms. `accept`: kind, pack_id, action_id, simulated. `rate`: rating. |
| `created_at` | |

RLS is enabled with no policies (same lock as 0002); `seed` truncates it.

## Config

| Env | Default | |
| --- | --- | --- |
| `ASSIST_MAX_CARDS_PER_MIN` | 4 | DC-7 |
| `ASSIST_LLM_MODEL` | `LLM_MODEL`, then the factory default | assist model |
| `DATABASE_URL` / `DATABASE_SCHEMA` | | connectors and `assist_events`; without a database the assist does not start |

## Tests

- `bot/tests/test_assist.py`: real packs, fake connectors and LLM. Trigger path,
  hero before supporting lookups, ranking and replacement, accept/dismiss/pin/rate,
  rate limit and flush, latency logging, plan ticks (agent only), pack switch,
  guardrail (patterns, blocked rule, shipped rules), LLM grounding (records re-read,
  text ignored, pack tools only, trigger must be heard), debounce, window, resync,
  no speech path.
- `bot/tests/test_assist_contract.py`: message shapes, feedback parsing and sender
  check. `test_assist_llm.py`: schema strictness, tool rounds, bad replies.
  `test_assist_runtime.py`: accept start, member scope, agent only delivery, resync on
  join, feedback from the agent only, event logging. `test_bot.py`: wiring and
  keyterms. `test_calls_db.py`: `add_assist_event` and RLS on Postgres.
- Live: `server/tests/test_assist_live.py` (Maria Alvarez through intake and accept;
  the agent client gets the retro authorization hero; timings printed).
