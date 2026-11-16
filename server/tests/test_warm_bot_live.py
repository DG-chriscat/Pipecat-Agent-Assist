"""Live P5-2: browser calls, cold start vs warm bot, from the call timing lines.

    uv run pytest -m live server/tests/test_warm_bot_live.py -s -v

Three calls through POST /calls/browser, each answered by a daily-python caller (the
S1 harness):
  1. cold: no pool, the bot process starts for the call (today's path)
  2. warm: a pool of 1, ready before the call
  3. warm again: the bot the pool started after call 2 (refill)

Per call it reads the existing `[timing]` lines: the server's call_requested and
bot_launched (with mode=warm|cold), the bot's bot_joined and greeting_first_audio
(bot processes inherit the test's stderr; capfd collects them). It prints request to
bot joined, and request to greeting, and asserts the warm calls join much sooner.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx
import pytest
from agent_assist_bot.bot import BOT_NAME
from agent_assist_bot.timing import parse_timing
from agent_assist_server import browser_call
from agent_assist_server.app import app
from agent_assist_server.bot_launcher import BotLauncher, WarmBotPool
from agent_assist_server.daily import DailyRestClient
from harness.participant import HarnessParticipant

pytestmark = pytest.mark.live

REQUIRED = ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY")
SPEECH_RMS = 300.0
GREETING_BUDGET_SECS = 5.0
# Warm: request to bot joined. The warm bot still joins Daily and builds its
# pipeline (~1 to 2 s); the cold one also imports and loads models first.
WARM_JOIN_BUDGET_SECS = 4.0


@pytest.fixture
def keys_present(monkeypatch):
    missing = [k for k in REQUIRED if not os.getenv(k)]
    if missing:
        pytest.skip(f"missing env: {', '.join(missing)}")
    # Calls run without the queue server and without Postgres: intake is not the point.
    monkeypatch.delenv("AGENT_ASSIST_SERVER_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)


def timeline(text: str, session: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in text.splitlines():
        parsed = parse_timing(line)
        if parsed and parsed[2].get("session") == session:
            out.setdefault(parsed[0], parsed[1])
    return out


async def one_call(launcher: BotLauncher, daily: DailyRestClient, label: str) -> dict:
    t0 = time.time()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(browser_call.BROWSER_CALL_PATH)
    assert r.status_code == 200, r.text
    out = r.json()
    room_name = out["room_url"].rsplit("/", 1)[-1]
    mode = launcher.last_mode
    proc = launcher.processes[out["session_id"]]
    try:
        async with HarnessParticipant("caller") as caller:
            await caller.join(out["room_url"], out["caller_token"])
            bot = await caller.wait_for_participant(BOT_NAME, timeout=120)
            both_in = max(caller.joined_at, bot.joined_at)
            rec = caller.recording_for(BOT_NAME)
            greet = None
            while greet is None and time.time() < both_in + 10:
                greet = rec.first_speech_after(both_in, threshold=SPEECH_RMS)
                await asyncio.sleep(0.05)
            assert greet is not None, f"{label}: bot never greeted"
            await asyncio.sleep(0.5)  # let the greeting's timing line land
        await asyncio.wait_for(proc.wait(), 30)
    finally:
        await daily.delete_room(room_name)
    return {
        "label": label,
        "mode": mode,
        "session": out["session_id"],
        "t0": t0,
        "caller_bot_joined": bot.joined_at - t0,
        "caller_greeting_heard": greet - t0,
        "greeting_after_both_in": greet - both_in,
    }


async def test_live_warm_bot_joins_sooner_than_a_cold_start(keys_present, capfd, caplog):
    caplog.set_level(logging.INFO, logger="agent_assist_server")
    daily = DailyRestClient.from_env()
    results: list[dict] = []

    cold = BotLauncher(pool=None)
    app.state.bot_launcher = cold
    try:
        results.append(await one_call(cold, daily, "cold"))
    finally:
        del app.state.bot_launcher

    pool = WarmBotPool(1)
    warm = BotLauncher(pool=pool)
    app.state.bot_launcher = warm
    ready_notes: list[str] = []
    try:
        await warm.start()
        started = time.time()
        while pool.ready_count < 1:
            assert time.time() - started < 90, "warm bot never got ready"
            await asyncio.sleep(0.1)
        ready_notes.append(f"first warm bot ready {time.time() - started:.2f}s after pool start")
        results.append(await one_call(warm, daily, "warm"))
        started = time.time()
        while pool.ready_count < 1:
            assert time.time() - started < 90, "pool never refilled"
            await asyncio.sleep(0.1)
        ready_notes.append(f"refill ready {time.time() - started:.2f}s after call 2 ended")
        results.append(await one_call(warm, daily, "warm (refill)"))
    finally:
        await warm.close()
        for p in warm.processes.values():
            if p.returncode is None:
                p.terminate()
                await p.wait()
        del app.state.bot_launcher

    logs = capfd.readouterr()
    text = "\n".join([logs.out, logs.err, caplog.text])
    with capfd.disabled():
        print()
        for note in ready_notes:
            print(note)
        hdr = (
            f"{'call':<14} {'mode':<5} {'req->launched':>13} {'req->bot_joined':>15} "
            f"{'req->greeting':>13} {'joined->greeting':>16} | caller: joined, heard"
        )
        print(hdr)
        for r in results:
            ev = timeline(text, r["session"])
            r["events"] = ev
            req = ev.get("call_requested", r["t0"])

            def since(name, ev=ev, req=req):
                return f"{ev[name] - req:.2f}s" if name in ev else "n/a"

            j2g = (
                f"{ev['greeting_first_audio'] - ev['bot_joined']:.2f}s"
                if {"greeting_first_audio", "bot_joined"} <= ev.keys()
                else "n/a"
            )
            print(
                f"{r['label']:<14} {r['mode']:<5} {since('bot_launched'):>13} "
                f"{since('bot_joined'):>15} {since('greeting_first_audio'):>13} {j2g:>16} | "
                f"{r['caller_bot_joined']:.2f}s, {r['caller_greeting_heard']:.2f}s"
            )

    assert [r["mode"] for r in results] == ["cold", "warm", "warm"]
    for r in results:
        assert {"call_requested", "bot_launched", "bot_joined", "greeting_first_audio"} <= r[
            "events"
        ].keys(), f"{r['label']}: missing timing lines {sorted(r['events'])}"
        assert r["greeting_after_both_in"] <= GREETING_BUDGET_SECS
    cold_join = results[0]["events"]["bot_joined"] - results[0]["events"]["call_requested"]
    for r in results[1:]:
        join = r["events"]["bot_joined"] - r["events"]["call_requested"]
        assert join <= WARM_JOIN_BUDGET_SECS, f"{r['label']}: joined {join:.2f}s after request"
        assert join < cold_join
