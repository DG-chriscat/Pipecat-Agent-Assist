"""Live: real Daily room with dial-in settings, real bot process joins it.

Run locally with keys in the environment:
    uv run pytest -m live server/tests/test_dialin_live.py -s

No phone number is involved (dg-chriscat is on the free plan), so callId/callDomain
are placeholders: the bot joins and its pinlessCallUpdate is expected to be rejected.
"""

import asyncio
import os
import time
import uuid

import httpx
import pytest
from agent_assist_bot.bot import BOT_NAME
from agent_assist_server import dialin
from agent_assist_server.app import app
from agent_assist_server.bot_launcher import BotLauncher
from agent_assist_server.daily import DailyRestClient

pytestmark = pytest.mark.live

REQUIRED = ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY")


@pytest.fixture
def keys_present():
    missing = [k for k in REQUIRED if not os.getenv(k)]
    if missing:
        pytest.skip(f"missing env: {', '.join(missing)}")


async def test_live_webhook_creates_dialin_room_and_bot_joins(keys_present, monkeypatch):
    monkeypatch.delenv(dialin.HMAC_ENV, raising=False)
    launcher = BotLauncher()
    app.state.bot_launcher = launcher
    daily = DailyRestClient.from_env()
    room_name = None
    try:
        t0 = time.monotonic()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                dialin.DIALIN_WEBHOOK_PATH,
                json={
                    "callId": str(uuid.uuid4()),
                    "callDomain": str(uuid.uuid4()),
                    "To": "+15550001111",
                    "From": "+15552223333",
                },
            )
        assert r.status_code == 200, r.text
        out = r.json()
        room_url = out["dailyRoom"]
        room_name = room_url.rsplit("/", 1)[-1]
        print(f"\nwebhook -> room created in {time.monotonic() - t0:.2f}s: {room_url}")

        room = await _get_room(daily, room_name)
        sip = room["config"].get("sip")
        print(f"room sip config: {sip}")
        print(f"room sip_endpoint present: {bool(room['config'].get('sip_endpoint'))}")
        assert sip and sip["sip_mode"] == "dial-in"

        proc = launcher.processes[out["sessionId"]]
        joined_at = None
        for _ in range(60):
            presence = await daily.get_presence(room_name)
            if presence.get("total_count", 0) >= 1:
                joined_at = time.monotonic()
                names = [p.get("userName") for p in presence.get("data", [])]
                print(f"bot present after {joined_at - t0:.2f}s: {names}")
                assert BOT_NAME in names, names
                break
            if proc.returncode is not None:
                break
            await asyncio.sleep(0.5)
        assert proc.returncode is None, f"bot exited early with {proc.returncode}"
        assert joined_at is not None, "bot never joined the room"
    finally:
        for p in launcher.processes.values():
            if p.returncode is None:
                p.terminate()
                try:
                    await asyncio.wait_for(p.wait(), 10)
                except TimeoutError:
                    p.kill()
        if room_name:
            await daily.delete_room(room_name)
            print(f"room {room_name} deleted")
        del app.state.bot_launcher


async def _get_room(daily: DailyRestClient, name: str) -> dict:
    async with httpx.AsyncClient(
        base_url=daily.api_url, headers={"Authorization": f"Bearer {daily.api_key}"}
    ) as c:
        r = await c.get(f"/rooms/{name}")
    r.raise_for_status()
    return r.json()
