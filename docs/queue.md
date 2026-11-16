# Queue service

PRD QH-1 to QH-5, QH-10 (P2-4). Code: `server/agent_assist_server/queue_service.py`
(state machine), `queue_api.py` (REST, websocket, bot notifier), `queue_store.py`
(Postgres write-through). Bot side: `bot/agent_assist_bot/handoff.py` and `hold.py`.

## State: in memory, Postgres write-through

The queue service holds all queue and agent state in memory in the server process. It
is the single source of truth (QH-4). Every change is an event; subscribers are the
websocket clients, the bot notifier, and (with `QUEUE_PERSIST=1`) a write-through to
the `calls` and `agents` tables.

Why: the demo is one server process with at most 5 calls and 3 agents (QH-10), offers
need 20 s timers, and Supabase sits a network hop away. Keeping state in memory keeps
the offer path fast and the code small. The tables are a copy for the portal, reports,
and debugging; writes run in order on a background task and a DB error is logged and
dropped, never blocking a call. A server restart drops the live queue (callers on hold
stay in their rooms with their bots; agents sign in again). Moving state into Postgres
is the path if we ever run more than one server process.

Migration `0003_queue_agents.sql` adds `agents` (RLS enabled, like every table) and
`calls.agent_id`. The call upsert merges the intake summary into `calls.intake`, merges
the handoff flags into `calls.flags`, and only overwrites identity_result/member_id with
non-null values, so fields the bot already wrote during intake are kept.

## Agent states (QH-1)

`offline`, `available`, `ringing`, `on_call`, `wrap_up`. Only `available` agents get
offers. Agents set by hand only:

| From | To (by hand) |
| --- | --- |
| offline | available |
| available | offline |
| ringing | offline (same as decline) |
| on_call | wrap_up (ends the call) |
| wrap_up | available, offline |

`ringing` and `on_call` are set by the service (offer, accept). If the caller hangs up,
a ringing agent goes back to `available` and an on-call agent goes to `wrap_up`.

## Wrap up (P3-5)

End (agent) or a caller hang up moves the agent to `wrap_up`. The call records who
ended it (`ended_by`: `agent` or `caller`), and the agent keeps `call_id` through wrap
up so the portal knows which call to save. The portal shows the wrap up view
(docs/DESIGN.md "Wrap up"): `GET /calls/{session_id}/wrap-up?agent_id=` returns
`caller_name`, `reason`, `identity_result`, `duration_secs`, `ended_by`,
`summary_draft` (null until AP-9 in Phase 4), `reason_codes` (from the active pack's
`wrap_up.reason_codes`), and `wrap_up` (once saved). Only the agent on the call can
read it.

`POST /agents/{agent_id}/wrap-up {session_id, reason_code, notes}` saves the reason
code (must be one of the pack's, else 422) and notes (max 2000 characters) on the
call (`wrap_up: {reason_code, notes, saved_at}`, a `call_updated` event), then sets the
agent `available`, which may ring them at once. Not in wrap up for that call: 409.
With `QUEUE_PERSIST=1` the write-through stores `calls.ended_by`, `wrap_up_reason`,
`wrap_up_notes`, and `wrapped_up_at` (migration `0004_wrap_up.sql`). The manual
`wrap_up -> available` toggle still works as an escape hatch (nothing saved).

The portal sends `wrap_up` before it leaves the room, so the bot never sees an agent
leave a call the queue still has as live. When the agent ends the call the server
also tells the bot (`call_ended` app message): the bot says "Thanks for calling.
Goodbye." and exits, and the server deletes the room `ROOM_CLOSE_DELAY_SECS` (6 s)
later, which hangs up a dial-in caller and ends a browser caller's call.

## Agent drop (QH-9)

If the agent leaves the room without End (tab closed, network lost), the bot sees
the agent's participant leave and posts `POST /queue/calls/{session_id}/agent-left`
(bot key route). The server waits `AGENT_DROP_GRACE_SECS` (3 s) so an End that lands
just after the leave still wins; if the same agent is still on the call then:

- the call goes back to `queued` at the front of the queue (`agent_id` and
  `answered_at` cleared), emitted as `call_queued` with `position: 1` and
  `requeued: true`;
- that agent goes `offline` (the "not available" state, same as a missed offer);
- the bot gets a `requeued` app message: it reopens the gate in front of the LLM,
  stops the listen only transcript, says "Sorry, we lost the connection to your
  agent. You're back at the front of the line, and the next person will be right with
  you.", and resumes hold updates;
- the next available agent is offered the call at once; their accept works like any
  other (the bot says "Connecting you now." and goes listen only again).

Daily notices a closed tab only when the peer connection times out, so the drop is
seen about 30 s after the tab closes (34 s in the live test); a network loss is the
same. The portal of the dropped agent, if it is still open (for example after a
reconnect), sees the re-queue for its call, leaves the room, and says so.

## Offers (QH-2, QH-3)

- One FIFO queue. Calls are offered in arrival order. A missed call keeps its place.
- Each waiting call rings one agent: the available agent idle the longest (idle since
  they last became available). One agent rings for one call at a time.
- 20 s to accept. A timeout or a decline sets that agent to `offline` (the "not
  available" state; they set themselves available again) and rings the next agent.
- Accept mints a non-owner Daily meeting token for the call's room (same code as
  `POST /rooms/{room}/agent-token`) and returns it with the intake summary (QH-6, QH-7).
  The token is minted when the offer rings (`PrefetchingMinter`), so accept does not
  wait on Daily; it stays in server memory and only the accept call for that room and
  agent receives it. A missed offer drops it; a failed prefetch is minted again.
  If Daily fails, the call goes back to the queue and the agent back to available.
  Tokens go only to the agent who accepted, never into broadcast events.

## API

REST:

| Method | Path | Body | Result |
| --- | --- | --- | --- |
| POST | `/agents/sign-in` | `{name}` | agent (demo sign in, AP-1: same name, same agent) |
| POST | `/agents/{agent_id}/status` | `{state}` | agent |
| POST | `/agents/{agent_id}/accept` | `{session_id}` | `{session_id, room_url, token, expires_at, summary, outcome, identity_result, member_id, flags}` |
| POST | `/agents/{agent_id}/decline` | `{session_id}` | `{status}` |
| GET | `/queue` | | snapshot: `queue`, `active`, `agents`, `offer_timeout_secs` |
| POST | `/queue/calls` | `{session_id, summary, outcome?, identity_result?, member_id?, flags?}` | `{session_id, status, position}` (bot handoff) |
| POST | `/queue/calls/{session_id}/end` | | caller left |
| POST | `/queue/calls/{session_id}/agent-left` | | the agent left the room (bot, QH-9) |
| GET | `/calls/{session_id}/caller?agent_id=` | | caller panel data (P3-3, see below) |
| GET | `/calls/{session_id}/wrap-up?agent_id=` | | wrap up view data (P3-5) |
| POST | `/agents/{agent_id}/wrap-up` | `{session_id, reason_code, notes}` | the call, saved; agent available (P3-5) |

Invalid commands return 409 with a message; a Daily token failure on accept returns
502. `/queue/calls` only takes sessions this server started (`/calls/browser` or the
dial-in webhook register them), otherwise 404.

Websocket `/ws/queue`: the first message is `{"type": "snapshot", ...}`, then every
event: `call_queued` (with `position`), `call_updated`, `call_accepted`, `call_ended`,
`agent_updated`, `offer` (`agent_id`, `call` with `summary`, `expires_at`),
`offer_missed` (`reason` timeout or declined). Commands on the same socket:

```
{"type": "sign_in", "name": "Ana"}                          -> {"type": "signed_in", "agent": {...}}
{"type": "set_status", "agent_id": "...", "state": "..."}   -> {"type": "status_set", ...}
{"type": "accept", "agent_id": "...", "session_id": "..."}  -> {"type": "accepted", token, room_url, summary, ...}
{"type": "decline", "agent_id": "...", "session_id": "..."} -> {"type": "declined", ...}
errors                                                      -> {"type": "error", "message": "..."}
```

No auth beyond the demo sign in (AP-1). Do not expose this server publicly as is.

## Caller panel data (P3-3, AP-5, AP-13, DC-8)

Code: `server/agent_assist_server/caller_api.py`. `GET /calls/{session_id}/caller?agent_id=...`

- Only the agent the call was accepted by can read it (the call's `agent_id` must match;
  queued and ringing calls return 403, unknown calls 404). It stays readable in wrap up.
  The shared login (P2-6) covers it like every other route.
- Always returned: `session_id`, `identity_result`, `outcome`, `flags`, `intake` (the
  intake answers from the handoff summary), `wait_secs` (queued to answered).
- Verified callers only (`identity_result` verified and a member id the DB knows):
  `member` (`member_id`, `name`, `first_name`, `last_name`, `dob`, `plan_id`,
  `plan_name`, `plan_type`), `calls_30d` (interactions in the last 30 days),
  `repeat_caller` (2 or more), `recent_events` (last 3 across interactions, claims, and
  referrals, newest first: `kind` call/claim/referral, `ref`, `occurred_on`, `status`,
  `summary`). `not_verified` is false.
- Anyone else: `not_verified` true, `member`/`calls_30d`/`repeat_caller` null,
  `recent_events` empty. The claimed member id is never looked up, so nothing about a
  member is guessed.
- DB reads use `DATABASE_URL` and optional `DATABASE_SCHEMA`, with a 2 s timeout
  (DC-4). A DB error or timeout is 503 with a generic message; the portal keeps the
  accept summary it already shows.

The portal shows the accept summary at once (QH-7) and fills the rest from this call.

## Bot side (QH-5, QH-6)

One hook contract: P2-3's `HandoffHook` in `bot/agent_assist_bot/intake.py`.

```
class HandoffHook(Protocol):
    async def on_intake_complete(self, handoff: IntakeHandoff) -> None: ...
```

The intake Flow calls it once per call when intake ends: read back confirmed
(`completed`), `request_person` (`asked_for_person`), or `report_emergency`
(`emergency`). `QueueHandoff` (`bot/agent_assist_bot/handoff.py`) implements it by
posting to `POST /queue/calls`:

```
{"session_id": "...", "call_id": "...", "outcome": "completed",
 "summary": {<captured intake fields>, "summary": "<one line summary>"},
 "identity_result": "verified" | "not_verified" | "not_found" | null,
 "member_id": "EVM100231" | null,
 "flags": ["identity_not_verified", "emergency", ...]}
```

`outcome`, `identity_result`, `member_id`, and `flags` are stored on the queued call
and appear in every call dict (snapshot, `offer`, `call_*` events) and in the accept
reply, so the portal can show them. With `QUEUE_PERSIST=1` they are also written to
`calls` (flags merged with the ones the bot already wrote; an unknown member id is
stored as null because of the foreign key).

`QueueHandoff` also has `on_call_ended(session_id)`, which the bot calls when the
caller leaves (`NoopHandoff` has no such method and is skipped).
`handoff_from_env()` returns `QueueHandoff` when `AGENT_ASSIST_SERVER_URL` is set,
otherwise `NoopHandoff`; `run_bot` (the CLI path) passes it to `build_worker`.

The server tells the bot about queue changes with Daily app messages sent through the
REST API (`/rooms/{room}/send-app-message`; receivers see sender `"api"`, and the bot
ignores app messages from anyone else):

```
{"type": "agent_assist.queue", "event": "queued", "position": 1}
{"type": "agent_assist.queue", "event": "position", "position": 1}
{"type": "agent_assist.queue", "event": "agent_accepted", "agent_name": "Ana"}
{"type": "agent_assist.queue", "event": "requeued", "position": 1}
{"type": "agent_assist.queue", "event": "call_ended"}
```

While queued the bot says a short update every 60 s ("Thanks for waiting. You're next
in line..."). No hold music yet; the update is the hold audio. On `agent_accepted` it
says "Connecting you now." and a gate in front of the LLM drops caller turns, so the
bot stays quiet while the agent talks. It then goes listen only and streams the live
transcript to the agent's portal (P3-4, see docs/transcript.md).

## Config

| Env | Where | Meaning |
| --- | --- | --- |
| `AGENT_ASSIST_SERVER_URL` | bot (inherited from server) | Base URL of the server's queue API, e.g. `http://127.0.0.1:8000`. Unset: the handoff is a no-op. |
| `QUEUE_PERSIST` | server | `1` turns on the Postgres write-through (needs `DATABASE_URL`). |

## Not yet

- QH-8 callback offer after 5 minutes (Should, later card).
- AP-9 summary draft and suggested reason code at wrap up (Phase 4).
- Hold music.
- Server side hold (the portal's hold only mutes the agent mic in v1).
