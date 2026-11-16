# Deepgram Vitals

Deepgram Vitals is a healthcare voice agent assist demo on Pipecat and Daily. A bot takes the call, runs intake, queues the caller, hands off to a human agent, then feeds live assist cards to the agent portal.

- Product: [docs/PRD.md](docs/PRD.md)
- Plan: [docs/PLAN.md](docs/PLAN.md)
- Agent rules: [AGENTS.md](AGENTS.md)

## Dev
```
uv sync
uv run ruff check . && uv run pytest
```

## Daily dial-in (PRD IN-1)
Endpoint: `POST /daily/dialin` on the server. Daily calls it when a call arrives on a
number configured for pinless dial-in. The server creates a SIP dial-in room, mints a
bot token, and starts `python -m agent_assist_bot` for that call. The bot forwards the
held call into the room (`/dialin/pinlessCallUpdate`) and greets the caller on join.

Run the server:
```
uv run --env-file .env uvicorn agent_assist_server.app:app --port 8000
```

Point Daily at it (needs a public URL, e.g. a tunnel, and a Daily phone number):
```
curl -X POST https://api.daily.co/v1/ \
  -H "Authorization: Bearer $DAILY_API_KEY" -H "Content-Type: application/json" \
  -d '{"properties": {"pinless_dialin": [{
        "phone_number": "+1XXXXXXXXXX",
        "room_creation_api": "https://<public-host>/daily/dialin"}]}}'
```
Daily sends `{"test": "test"}` to verify the URL; the endpoint answers `{"status": "OK"}`.
Save the `hmac` Daily returns as `DAILY_DIALIN_HMAC` to enable signature checks.

Warm bot (P5-2): the server keeps `WARM_BOT_POOL` bots (default 1, max 2, 0 off)
already loaded and hands each new call (dial-in or browser) to one; see
[docs/warm-bot.md](docs/warm-bot.md).

Live test (real Daily room + bot, no phone call): `uv run --env-file .env pytest -m live -s`

Live call test (IN-1, against a deployed server):
```
DIALIN_TEST_SIP_URI=<pinless sip_uri from GET /domain-dialin-config> \
  DIALIN_TEST_SERVER_LOG=<server log file, optional> \
  uv run --env-file .env pytest -m live server/tests/test_dialin_call_live.py -s
```
A throwaway room dials the number's pinless SIP URI (same webhook, hold, and forward
path as a phone call). The caller leg is recorded and transcribed to find when the
greeting was heard. With `DIALIN_TEST_SERVER_LOG` the test also reads the server and bot
`[timing] event=... t=...` lines and asserts greeting first audio minus bot joined
under 2 s. `DIALIN_TEST_PSTN=1` dials the real number (needs PSTN dial-out on the
domain); `DIALIN_TEST_WAIT_SECS=N` waits for a person to call.

## Mock database (PRD DC-3, DC-8)
Postgres via `DATABASE_URL`. `uv run --env-file .env python -m agent_assist_server.db migrate`
then `... seed`. Tables and demo personas: [docs/db.md](docs/db.md).

## Login (P2-6)
Set `APP_PASSWORD` to put the whole app behind one shared password. Every page, API,
and the `/ws/queue` websocket then needs a session cookie; `/healthz` and
`POST /daily/dialin` (own HMAC) stay open. `SESSION_SECRET` is required with it, or the
server refuses to start. Leave `APP_PASSWORD` unset for local dev (a warning is logged).

```
# local: add to .env
APP_PASSWORD=<pick one>
SESSION_SECRET=<output of: python -c 'import secrets; print(secrets.token_urlsafe(32))'>

# Fly.io
fly secrets set APP_PASSWORD='<pick one>' \
  SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  TRUSTED_CLIENT_IP_HEADER=Fly-Client-IP
```

Open any page and you land on `/login`, then go back where you were. Sessions last 12
hours and are per browser, so a caller on `/caller` and an agent on another machine
can both sign in. `/logout` signs out. Ten wrong passwords from one IP in 10 minutes
return 429. Changing `SESSION_SECRET` signs everyone out. The server passes its bots a
derived key (`AGENT_ASSIST_BOT_KEY`) for the queue handoff, never the password.
