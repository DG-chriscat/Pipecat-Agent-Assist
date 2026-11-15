"""Live S1 test: the bot joins a Daily room, greets the caller, and replies.

Run: uv run pytest -m live bot/tests/test_live_s1.py -s
Needs DAILY_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx
import pytest
from agent_assist_bot.bot import BOT_NAME
from agent_assist_bot.daily_rooms import DailyRoomsClient
from agent_assist_bot.llm import DEFAULT_ANTHROPIC_MODEL
from harness.participant import SAMPLE_RATE, HarnessParticipant
from harness.tts import synthesize

pytestmark = pytest.mark.live

CALLER_LINE = "Hi, I have a question about my claim"
GREETING_BUDGET_SECS = 2.0
ROOM_TTL_SECS = 540  # under 10 minutes
SPEECH_RMS = 300.0


def _require_env():
    missing = [
        k for k in ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY") if not os.getenv(k)
    ]
    if missing:
        pytest.skip(f"missing env: {', '.join(missing)}")


async def _start_bot(room_url: str) -> tuple[asyncio.subprocess.Process, list[str]]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "agent_assist_bot.spike_s1",
        "--room-url",
        room_url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    lines: list[str] = []
    joined = asyncio.Event()

    async def pump():
        assert proc.stdout
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            lines.append(line)
            if f"Joined {room_url}" in line:
                joined.set()

    asyncio.create_task(pump())
    await asyncio.wait_for(joined.wait(), 60)
    return proc, lines


async def _stop_bot(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 15)
        except TimeoutError:
            proc.kill()
            await proc.wait()


async def _wait_for_quiet(rec, after: float, quiet_secs: float = 1.5, timeout: float = 30):
    """Wait until the remote speaker has been silent for quiet_secs after `after`."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        segs = [s for s in rec.segments(threshold=SPEECH_RMS) if s[1] > after]
        if segs and time.time() - segs[-1][1] >= quiet_secs:
            return segs[-1][1]
        await asyncio.sleep(0.1)
    raise TimeoutError("remote speaker never went quiet")


async def _transcribe(pcm: bytes) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.deepgram.com/v1/listen",
            params={
                "model": "nova-3",
                "encoding": "linear16",
                "sample_rate": SAMPLE_RATE,
                "smart_format": "true",
            },
            headers={"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}"},
            content=pcm,
        )
    resp.raise_for_status()
    return resp.json()["results"]["channels"][0]["alternatives"][0]["transcript"]


def _slice(rec, start: float, end: float) -> bytes:
    return b"".join(c for t, c in rec.chunks if start - 0.05 <= t <= end + 0.05)


async def test_bot_greets_and_replies(tmp_path: Path):
    _require_env()
    caller_pcm = await synthesize(CALLER_LINE, sample_rate=SAMPLE_RATE)

    async with DailyRoomsClient() as rooms, rooms.temporary_room(ttl_secs=ROOM_TTL_SECS) as room:
        print(f"\nroom: {room.url} (expires in {ROOM_TTL_SECS}s, deleted at end)")
        proc, bot_log = await _start_bot(room.url)
        try:
            async with HarnessParticipant("caller") as caller:
                await caller.join(room.url)
                joined_at = caller.joined_at
                assert joined_at is not None
                await caller.wait_for_participant(BOT_NAME, timeout=10)
                print(f"bot joined as {BOT_NAME!r}")
                bot_rec = caller.recording_for(BOT_NAME)

                # 1. Greeting latency: caller join -> first audible bot audio.
                deadline = joined_at + 10
                greet_start = None
                while time.time() < deadline and greet_start is None:
                    greet_start = bot_rec.first_speech_after(joined_at, threshold=SPEECH_RMS)
                    await asyncio.sleep(0.05)
                assert greet_start is not None, "bot never greeted"
                greet_latency = greet_start - joined_at
                print(f"greeting latency: {greet_latency:.3f}s (budget {GREETING_BUDGET_SECS}s)")
                greet_end = await _wait_for_quiet(bot_rec, greet_start)

                # 2. Caller speaks; reply latency: end of caller audio -> first bot audio.
                await asyncio.sleep(0.5)
                spoke_start, spoke_end = await caller.play(caller_pcm)
                print(f"caller spoke {spoke_end - spoke_start:.2f}s: {CALLER_LINE!r}")
                reply_start = None
                deadline = spoke_end + 15
                while time.time() < deadline and reply_start is None:
                    reply_start = bot_rec.first_speech_after(spoke_end, threshold=SPEECH_RMS)
                    await asyncio.sleep(0.05)
                assert reply_start is not None, "bot never replied"
                reply_latency = reply_start - spoke_end
                reply_end = await _wait_for_quiet(bot_rec, reply_start)
                print(f"reply latency (end of caller audio -> bot audio): {reply_latency:.3f}s")

                greeting_text = await _transcribe(_slice(bot_rec, greet_start, greet_end))
                reply_text = await _transcribe(_slice(bot_rec, reply_start, reply_end))
                print(f"bot greeting heard: {greeting_text!r}")
                print(f"bot reply heard:    {reply_text!r}")
                caller.save_recording(BOT_NAME, tmp_path / "bot_heard.wav")
        finally:
            await _stop_bot(proc)

    log_path = os.getenv("S1_BOT_LOG")
    if log_path:
        Path(log_path).write_text("\n".join(bot_log))
    stt_lines = [line for line in bot_log if "[caller]" in line]
    print("bot STT lines:", *stt_lines, sep="\n  ")
    result = {
        "llm_model": os.getenv("LLM_MODEL") or DEFAULT_ANTHROPIC_MODEL,
        "greeting_latency_s": round(greet_latency, 3),
        "reply_latency_s": round(reply_latency, 3),
        "greeting_text": greeting_text,
        "reply_text": reply_text,
    }
    print("S1 RESULT " + json.dumps(result))

    assert greet_latency <= GREETING_BUDGET_SECS
    assert greeting_text.strip()
    assert reply_text.strip()
    assert any("claim" in line.lower() for line in stt_lines), "bot STT missed the caller"
