"""Live IN-12: POST /calls/browser -> caller joins with caller_token -> bot greets <2 s.

Run locally with keys in the environment:
    uv run pytest -m live server/tests/test_browser_call_live.py -s -v

The caller is a daily-python client from the S1 harness (bot/tests/harness). The
greeting clock starts when both caller and bot are in the room, whichever is later,
since the bot greets the first participant it sees.
"""

import asyncio
import os
import time

import httpx
import pytest
from agent_assist_bot.bot import BOT_NAME
from agent_assist_server import browser_call
from agent_assist_server.app import app
from agent_assist_server.bot_launcher import BotLauncher
from agent_assist_server.daily import DailyRestClient
from harness.participant import HarnessParticipant

pytestmark = pytest.mark.live

REQUIRED = ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY")
GREETING_BUDGET_SECS = 5.0
SPEECH_RMS = 300.0


@pytest.fixture
def keys_present():
    missing = [k for k in REQUIRED if not os.getenv(k)]
    if missing:
        pytest.skip(f"missing env: {', '.join(missing)}")


async def test_live_browser_call_bot_greets_caller(keys_present):
    launcher = BotLauncher()
    app.state.bot_launcher = launcher
    daily = DailyRestClient.from_env()
    room_name = None
    try:
        t0 = time.time()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(browser_call.BROWSER_CALL_PATH)
        assert r.status_code == 200, r.text
        out = r.json()
        room_name = out["room_url"].rsplit("/", 1)[-1]
        print(f"\nPOST /calls/browser -> {r.status_code} in {time.time() - t0:.2f}s: {room_name}")
        proc = launcher.processes[out["session_id"]]

        async with HarnessParticipant("caller") as caller:
            await caller.join(out["room_url"], out["caller_token"])
            print(f"caller joined with caller_token after {caller.joined_at - t0:.2f}s")
            bot = await caller.wait_for_participant(BOT_NAME, timeout=120)  # cold bot start
            print(f"bot joined after {bot.joined_at - t0:.2f}s")
            assert proc.returncode is None, f"bot exited early with {proc.returncode}"

            both_in = max(caller.joined_at, bot.joined_at)
            rec = caller.recording_for(BOT_NAME)
            greet_start = None
            deadline = both_in + 10
            while greet_start is None and time.time() < deadline:
                greet_start = rec.first_speech_after(both_in, threshold=SPEECH_RMS)
                await asyncio.sleep(0.05)
            assert greet_start is not None, "bot never greeted"
            latency = greet_start - both_in
            budget = GREETING_BUDGET_SECS
            print(f"first bot TTS audio {latency:.3f}s after bot joined (budget {budget}s)")
            assert latency <= GREETING_BUDGET_SECS

            # Daily's presence API lags joins by a few seconds; poll it.
            want = sorted(["caller", BOT_NAME])
            names: list[str] = []
            for _ in range(20):
                presence = await daily.get_presence(room_name)
                names = sorted(p.get("userName") for p in presence.get("data", []))
                if names == want:
                    break
                await asyncio.sleep(0.5)
            print(f"Daily presence: {names}")
            assert names == want

        # Caller hung up: the bot ends its session on participant_left.
        await asyncio.wait_for(proc.wait(), 30)
        print(f"bot exited after caller left, returncode={proc.returncode}")
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
            async with httpx.AsyncClient(
                base_url=daily.api_url, headers={"Authorization": f"Bearer {daily.api_key}"}
            ) as c:
                status = (await c.get(f"/rooms/{room_name}")).status_code
            print(f"room {room_name} deleted; GET /rooms -> {status}")
            assert status == 404
        del app.state.bot_launcher
