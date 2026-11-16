# Browser caller (PRD IN-12, ADR 0002)

For the PoC the caller joins from a browser page instead of a phone. The server creates the
room and starts the same bot the dial-in path starts (same `build_worker`, same IN-2 greeting),
just without dial-in settings.

## How it works

1. `GET /caller` serves `server/agent_assist_server/static/caller.html`: Start call, Mute,
   Hang up, and a status line. It loads `@daily-co/daily-js` from the unpkg CDN.
2. Start call does `POST /calls/browser` (`server/agent_assist_server/browser_call.py`):
   - creates a private Daily room (`caller-<id>`, expires in 1 hour, ejects at expiry),
   - mints an owner token for the bot (`user_name` "Vitals Assistant") and a non-owner token for the
     caller (`user_name` "caller"),
   - starts the bot through `BotLauncher`: `python -m agent_assist_bot --room-url ... --session-id ...`
     with no `--call-id`/`--call-domain`; the bot token goes in `DAILY_ROOM_TOKEN`, not argv,
   - returns `{"session_id", "room_url", "caller_token"}`. The bot token is never returned.
   - `DAILY_API_KEY` missing: 503. Daily error or unreachable: 502, no bot is started.
3. The page joins `room_url` audio only with `caller_token`. The bot greets the first
   participant it sees and ends its session when the caller leaves.

The room is not deleted when the call ends; it expires after an hour. The phone path is
unchanged: `/daily/dialin` still passes `--call-id`/`--call-domain` to the same bot CLI.

## Run it manually

Needs `DAILY_API_KEY`, `DEEPGRAM_API_KEY`, `ANTHROPIC_API_KEY` in the environment (see
`.env.example`; never commit them).

```bash
uv sync --all-packages
set -a; . ./.env; set +a
uv run uvicorn agent_assist_server.app:app --port 8000
```

Open http://localhost:8000/caller in Chrome (getUserMedia needs `localhost` or HTTPS), click
Start call, and allow the microphone. The status shows "In call with: Vitals Assistant" and you hear
the greeting. Talk to it, use Mute, then Hang up. The bot process exits after you leave.

Without the page:

```bash
curl -s -X POST localhost:8000/calls/browser
```

Join the returned `room_url` with the `caller_token` from any Daily client.

## Tests

- Unit (Daily and the launcher mocked): `uv run pytest server/tests/test_browser_call.py
  bot/tests/test_bot.py`.
- Live: `uv run pytest -m live server/tests/test_browser_call_live.py -s -v`. It calls
  `POST /calls/browser`, joins with a daily-python client (the S1 harness) using `caller_token`,
  waits for the bot, checks that the first bot TTS audio arrives within 5 s of the bot joining
  (clock starts when both caller and bot are in the room), checks Daily presence, hangs up,
  checks the bot process exits, then deletes the room and checks it is gone (404).

Live result (2026-11-15, dg-chriscat):

```
POST /calls/browser -> 200 in 0.82s: caller-78eea986f463
caller joined with caller_token after 2.07s
bot joined after 8.20s
first bot TTS audio 1.052s after bot joined (budget 2.0s)
Daily presence: ['Intake Bot', 'caller']
bot exited after caller left, returncode=0
room caller-78eea986f463 deleted; GET /rooms -> 404
PASSED
============================== 1 passed in 16.08s ==============================
```

"bot joined after" includes the bot process start (Python imports, Silero VAD and Smart Turn
model load), roughly 6 to 10 s on the build host and much longer when the host is loaded. That
is time the caller sits in an empty room; the page shows "Waiting for the assistant...". Not in
scope here, noted as an open item: pre-warming a bot process would remove it.

The manual Chrome check (real microphone) was not run on the build host; it needs a person.
