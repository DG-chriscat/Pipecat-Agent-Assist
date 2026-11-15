# Pipecat Agent Assist

Healthcare voice agent assist demo on Pipecat and Daily. A bot takes the call, runs intake, queues the caller, hands off to a human agent, then feeds live assist cards to the agent portal.

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

Live test (real Daily room + bot, no phone call): `uv run --env-file .env pytest -m live -s`
