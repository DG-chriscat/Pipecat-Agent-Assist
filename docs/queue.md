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
`summary_draft` and `suggested_reason_code` (the bot's AP-9 draft, P5-1, null until it
lands), `draft_pending` (true while the draft is still coming, up to 30 s after the
call ended), `pack_id` and `reason_codes` (the draft's pack, else the active pack's
`wrap_up.reason_codes`), and `wrap_up` (once saved). Only the agent on the call can
read it. The draft comes from the bot (`POST /queue/calls/{session_id}/wrap-up-draft`,
docs/wrap_up.md).

`POST /agents/{agent_id}/wrap-up {session_id, reason_code, notes, summary}` saves the
reason code (must be one of the pack's, else 422), notes (max 2000 characters), and
summary (max 3000) on the call (`wrap_up: {reason_code, notes, summary, saved_at}`, a
`call_updated` event), then sets the agent `available`, which may ring them at once.
Not in wrap up for that call: 409. With `QUEUE_PERSIST=1` the write-through stores
`calls.ended_by`, `wrap_up_reason`, `wrap_up_notes`, `wrapped_up_at` (migration
`0004_wrap_up.sql`), `wrap_up_draft`, and `wrap_up_summary` (`0007_wrap_up_summary.sql`).
The manual `wrap_up -> available` toggle still works as an escape hatch (nothing
saved).

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
| POST | `/queue/calls/{session_id}/wrap-up-draft` | `{summary, reason_code, pack_id, source}` | the bot's summary and reason code draft (P5-1); 422 if the code is not in the pack, 409 if no agent took the call |
| GET | `/calls/{session_id}/caller?agent_id=` | | caller panel data (P3-3, see below) |
| GET | `/calls/{session_id}/wrap-up?agent_id=` | | wrap up view data (P3-5) |
| POST | `/agents/{agent_id}/wrap-up` | `{session_id, reason_code, notes, summary}` | the call, saved; agent available (P3-5, P5-1) |
| POST | `/calls/{session_id}/phi-reveal` | `{agent_id, field: member_id\|dob}` | logs a PHI reveal on the call (AP-17); 403 unless the agent is on the call and the caller is verified (at intake, or by the agent's decision, IN-13) |
| POST | `/calls/{session_id}/identity-decision` | `{agent_id, decision: verified\|not_verified}` | IN-13: stores `{decision, agent_id, agent_name, at}` on the call (`calls.identity_decision`); 404 unknown call, 403 not the agent on the call, 409 already decided, 422 bad value |

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

Websocket `/ws/bot/{session_id}` (QH-5b) is for the call's bot only: the server
pushes `agent_accepted`, `requeued` and `call_ended` for that session on it and
ignores what the bot sends.

No auth beyond the demo sign in (AP-1). Do not expose this server publicly as is.

## Caller panel data (P3-3, AP-5, AP-13, DC-8)

Code: `server/agent_assist_server/caller_api.py`. `GET /calls/{session_id}/caller?agent_id=...`

- Only the agent the call was accepted by can read it (the call's `agent_id` must match;
  queued and ringing calls return 403, unknown calls 404). It stays readable in wrap up.
  The shared login (P2-6) covers it like every other route.
- Always returned: `session_id`, `identity_result`, `identity_decision` (IN-13, or
  null), `outcome`, `flags`, `intake` (the intake answers from the handoff summary),
  `wait_secs` (queued to answered).
- Verified callers only (`identity_result` verified, or the agent marked the caller
  verified on the verify hero, IN-13; and a member id the DB knows):
  `member` (`member_id`, `name`, `first_name`, `last_name`, `dob`, `plan_id`,
  `plan_name`, `plan_type`), `calls_30d` (interactions in the last 30 days),
  `repeat_caller` (2 or more), `recent_events` (last 3 across interactions, claims, and
  referrals, newest first: `kind` call/claim/referral, `ref`, `occurred_on`, `status`,
  `summary`). `not_verified` is false. After Mark verified the member is the one intake
  matched by member ID **and** date of birth (`intake.identity_checks` has both:
  `close_match`, or `not_verified` on the name alone, as when STT hears "Linda Ewan"
  for Linda Nguyen). A member matched by name, or by the ID alone (wrong DOB), or
  `not_found`, is never shown, even after Mark verified (`Call.record_member_id`).
- Identity n of 3 (AP-18): the bot's handoff puts `identity_checks` (which of
  `full_name`, `dob`, `member_id` matched the member) in the summary, so it reaches the
  portal in `intake.identity_checks`. Missing when identity was never checked.
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
 "member_id": "418302" | null,
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

`agent_accepted`, `requeued` and `call_ended` also carry an `"id"` and are pushed on
the bot's own websocket (`/ws/bot/{session_id}`, QH-5b, see "How agent_accepted
reaches the bot" below); the Daily app message is the fallback copy.

While queued the bot says a short update every 60 s ("Thanks for waiting. You're next
in line..."), with hold music under it (see "Hold music" below). On `agent_accepted`
the music stops, the bot says "Connecting you now.", and a gate in front of the LLM
drops caller turns, so the bot stays quiet while the agent talks. It then goes listen
only and streams the live transcript to the agent's portal (P3-4, see
docs/transcript.md).

## Hold music (QH-5)

Code: `bot/agent_assist_bot/hold_music.py` (mixer, config), `hold.py` (when it plays),
`hold_music_gen.py` (the track). The caller hears a calm loop while waiting for a
person, on both phone dial-in and the browser caller page (same bot, same transport).

How it plays: `HoldMusicMixer` is the Daily output transport's audio mixer
(`DailyParams.audio_out_mixer`, Pipecat 1.15). Pipecat calls it for every 40 ms output
chunk, passing the bot's TTS audio or silence, so the music keeps going between bot
lines. The hold controller turns it on and off; the mixer handles the levels itself:

| Moment | Music |
| --- | --- |
| Intake | off (the mixer is wired in from the start but silent) |
| `queued` | on: waits for the handoff line to finish, then fades in over 1.5 s |
| Hold update (every 60 s) | ducks 12 dB while the bot speaks, back up 0.4 s after |
| `agent_accepted` | off before "Connecting you now." is queued; ramps to zero in one 40 ms chunk |
| `requeued` (QH-9) | on again after the requeue line |
| `call_ended`, caller hangs up | off |

Ducking keys off the audio itself (any bot speech in the chunk), so a short LLM reply
to a caller who talks while on hold is ducked too.

Live check (`server/tests/test_hold_music_live.py`, browser and phone). The phone leg
is a real SIP leg into a Daily dial-in room (the room the dial-in webhook makes), dialed
over SIP from a second room; only the carrier and the pinless forward are skipped.

| Run | Path to the bot | Browser | Phone (SIP) |
| --- | --- | --- | --- |
| QH-5 (#36), Nov 16 | Daily app message (kept-alive REST) | 465 / 458 ms | 551 / 617 ms (#38) |
| QH-5b, Nov 16 | bot socket, Daily as fallback | 154 ms | 411 ms |

Times are from the agent's accept to the music stopping at the caller's ear (the first
silent 50 ms window). QH-5b run: longest gap during the 15 s wait 0.5 s (browser) /
0.6 s (phone), between the handoff line and the fade in; Deepgram transcribed the hold
update over the music word for word on both; "Connecting you now." followed 50 ms /
350 ms after the music stopped, and no music after it. The bot logs showed the socket
copy of `agent_accepted` acting first and the Daily copy arriving 210 to 235 ms later
and being dropped as a duplicate.

### How agent_accepted reaches the bot (QH-5b)

The server's REST hop through Daily (`send-app-message`, about 225 to 260 ms to
delivery even on a kept-alive connection) was most of the time to stop. So the bot
also has its own websocket to the server:

1. After a successful handoff (`POST /queue/calls`), the bot opens
   `ws(s)://<AGENT_ASSIST_SERVER_URL>/ws/bot/{session_id}` (`server_channel.py`),
   with `Authorization: Bearer <AGENT_ASSIST_BOT_KEY>` when the server has login on.
   With login on, only the bot key opens it (a portal cookie does not, and the bot
   key does not open `/ws/queue`). An unknown session closes with 4404.
2. The bot notifier (`queue_api.BotNotifier`) pushes `agent_accepted`, `requeued`
   and `call_ended` on that socket at once, and still sends each one as a Daily app
   message. Both copies carry the same `"id"`. `queued` and `position` go through
   Daily only (nothing is timed on them).
3. The bot acts on whichever copy comes first; `HoldController` drops the second by
   id, so a duplicate never speaks twice or turns the music back on.
4. The Daily copy is the fallback: if the socket is down, refused, or never opened,
   the app message still stops the music. A dropped socket reconnects with a short
   backoff (0.2 s, 0.5 s, 1 s, then every 2 s); 4401/4404 stop retrying. The socket
   closes when the call ends.

Server side, the Daily path is unchanged: every app message goes out on one
kept-alive Daily connection (`DailyRestClient.keep_alive()`), warmed with a room
presence call when an offer rings.

### The track

`bot/agent_assist_bot/assets/hold_music.wav` is our own: an ambient pad (Dmaj7, Bm7,
Gmaj7, A6, 10 s each, no drums, no melody, no vocals), 40 s, 16 kHz mono 16-bit,
normalized to -20 LUFS, looping without a seam. It is rendered by a deterministic
script, so there is no licensing question:

```
uv run python -m agent_assist_bot.hold_music_gen            # rewrites the bundled WAV
uv run python -m agent_assist_bot.hold_music_gen /tmp/x.wav  # or somewhere else
```

A unit test re-renders it and compares with the committed file.

### Using a licensed track instead

Set `HOLD_MUSIC_PATH` on the bot (it inherits the server's environment, so on Fly set
it as an app env var) to a file the bot can read: WAV, FLAC, or OGG, mono or stereo,
any sample rate (mixed down to mono and resampled to the output rate at call start).
For it to sit under speech like the bundled loop, master it around -20 LUFS, make it
loop cleanly (the mixer wraps to the start with no crossfade), and keep it short (it
is held in memory per call; 30 to 60 s is plenty). In the Docker image, put the file
in the image (for example under `bot/agent_assist_bot/assets/`) or on a mounted
volume. A path that does not exist turns hold music off with a warning; the call goes
on with updates only.

## Config

| Env | Where | Meaning |
| --- | --- | --- |
| `AGENT_ASSIST_SERVER_URL` | bot (inherited from server) | Base URL of the server's queue API, e.g. `http://127.0.0.1:8000`. Unset: the handoff is a no-op. |
| `QUEUE_PERSIST` | server | `1` turns on the Postgres write-through (needs `DATABASE_URL`). |
| `HOLD_MUSIC` | bot | `on` (default) or `off`. Off: no mixer; the 60 s updates are the only hold audio. |
| `HOLD_MUSIC_VOLUME` | bot | Music gain under the bot, 0 to 1 (default `0.35`). Out of range is clamped. |
| `HOLD_MUSIC_PATH` | bot | Optional track that replaces the bundled loop (see "Using a licensed track instead"). |
| `HOLD_UPDATE_SECS` | bot | Seconds between hold updates (default 60). The live test uses 8. |

## Not yet

- QH-8 callback offer after 5 minutes (Should, later card).
- Server side hold (the portal's hold only mutes the agent mic in v1).
