"""Live intake (PRD IN-3 to IN-6, IN-8): scripted caller audio completes intake.

Run locally with keys and a Postgres in the environment:
    uv run pytest -m live bot/tests/test_live_intake.py -s -v

Needs DAILY_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL.

The test migrates and seeds a throwaway schema, starts the real bot process
(`python -m agent_assist_bot`, browser caller path) pointed at that schema with
DATABASE_SCHEMA, and joins as demo persona Maria Alvarez (docs/db.md) with the S1
harness. Each caller line is Deepgram TTS audio, played after the bot goes quiet.
Which line to say next is picked from the calls row (the next field not yet saved),
so the script follows the flow instead of guessing the LLM's wording.

Passes when the calls row is ready_for_queue with identity verified, member
EVM100231, a reason, and the intake took under 2 minutes (PRD target).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys
import time
import uuid

import pytest
from agent_assist_bot.bot import BOT_NAME
from agent_assist_bot.calls import PostgresCallStore
from harness.intake_script import MARIA_ALVAREZ as SCRIPT
from harness.intake_script import next_line
from harness.participant import SAMPLE_RATE, HarnessParticipant
from harness.tts import synthesize
from harness.turns import audio_slice as _slice
from harness.turns import transcribe as _transcribe
from harness.turns import wait_bot_turn as _wait_bot_turn

pytestmark = pytest.mark.live

REQUIRED = ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL")
INTAKE_BUDGET_SECS = 120.0
MAX_TURNS = 14

# Maria Alvarez, demo persona 1: MRI claim CLM-900001 denied CO-197.
SESSION_ID = f"live-intake-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def keys_present():
    missing = [k for k in REQUIRED if not os.getenv(k)]
    if missing:
        pytest.skip(f"missing env: {', '.join(missing)}")


@pytest.fixture
async def schema(keys_present):
    from agent_assist_server.db import connect, migrate, seed

    name = f"test_{uuid.uuid4().hex[:12]}"
    async with await connect() as admin:
        await admin.execute(f'CREATE SCHEMA "{name}"')
    try:
        async with await connect(schema=name) as c:
            await migrate.apply_migrations(c)
            await seed.load_seed(c, as_of=dt.date.today())
        yield name
    finally:
        async with await connect() as admin:
            await admin.execute(f'DROP SCHEMA "{name}" CASCADE')


async def _call_row(store: PostgresCallStore) -> dict | None:
    cur = await store._execute(
        "SELECT status, member_id, intake, identity_result, flags, created_at, updated_at "
        "FROM calls WHERE session_id = %s",
        (SESSION_ID,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    keys = ["status", "member_id", "intake", "identity_result", "flags", "created_at", "updated_at"]
    return dict(zip(keys, row, strict=True))


async def _start_bot(room_url: str, token: str, schema: str):
    env = dict(os.environ, DAILY_ROOM_TOKEN=token, DATABASE_SCHEMA=schema)
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "agent_assist_bot",
        "--room-url",
        room_url,
        "--session-id",
        SESSION_ID,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    lines: list[str] = []

    async def pump():
        assert proc.stdout
        async for raw in proc.stdout:
            lines.append(raw.decode(errors="replace").rstrip())

    asyncio.create_task(pump())
    return proc, lines


async def _stop(proc) -> None:
    if proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 15)
        except TimeoutError:
            proc.kill()
            await proc.wait()


async def test_live_intake_maria_alvarez(schema):
    from agent_assist_server.daily import DailyRestClient

    audio = {k: await synthesize(v, sample_rate=SAMPLE_RATE) for k, v in SCRIPT.items()}
    daily = DailyRestClient.from_env()
    room = await daily.create_browser_room()
    bot_token = await daily.create_meeting_token(room.name)
    caller_token = await daily.create_agent_token(room.name, "caller", int(time.time()) + 600)
    store = PostgresCallStore(os.environ["DATABASE_URL"], schema=schema)
    print(f"\nroom {room.name}, schema {schema}, session {SESSION_ID}")
    proc, bot_log = await _start_bot(room.url, bot_token, schema)
    transcript: list[tuple[str, tuple]] = []
    row = None
    try:
        async with HarnessParticipant("caller") as caller:
            await caller.join(room.url, caller_token)
            bot = await caller.wait_for_participant(BOT_NAME, timeout=120)
            both_in = max(caller.joined_at, bot.joined_at)
            rec = caller.recording_for(BOT_NAME)
            t_start = both_in
            after = both_in
            for _turn in range(MAX_TURNS):
                turn = await _wait_bot_turn(rec, after)
                if turn is None:
                    break
                # Transcribed after the call, so the harness adds no delay per turn.
                transcript.append(("bot", turn))
                row = await _call_row(store)
                if row and row["status"] == "ready_for_queue":
                    break
                key = next_line((row or {}).get("intake"))
                # write_frames never completes if the call dropped; don't hang on it.
                s, e = await asyncio.wait_for(caller.play(audio[key]), 60)
                transcript.append(("caller", (s, SCRIPT[key])))
                after = e
            # Give the last function call a moment to land.
            for _ in range(20):
                row = await _call_row(store)
                if row and row["status"] == "ready_for_queue":
                    break
                await asyncio.sleep(0.5)
            t_done = time.time()
            for who, item in transcript:
                if who == "bot":
                    said = await _transcribe(_slice(rec, *item))
                    print(f"[{item[0] - t_start:6.1f}s] bot:    {said}")
                else:
                    print(f"[{item[0] - t_start:6.1f}s] caller: {item[1]}")
    finally:
        await _stop(proc)
        await store.close()
        await daily.delete_room(room.name)
        if log_path := os.getenv("INTAKE_BOT_LOG"):
            with open(log_path, "w") as f:
                f.write("\n".join(bot_log))

    intake_secs = (row["updated_at"].timestamp() - t_start) if row else t_done - t_start
    print("--- bot log (intake lines) ---")
    for line in bot_log:
        if "[intake]" in line or "[caller]" in line or "ERROR" in line:
            print("  " + line.split(" - ", 1)[-1])
    result = {
        "session_id": SESSION_ID,
        "status": row and row["status"],
        "identity_result": row and row["identity_result"],
        "member_id": row and row["member_id"],
        "flags": row and row["flags"],
        "intake": row and row["intake"],
        "intake_seconds": round(intake_secs, 1),
    }
    print("INTAKE RESULT " + json.dumps(result, indent=2, default=str))

    assert row is not None, "bot never created the calls row"
    assert row["status"] == "ready_for_queue"
    assert row["identity_result"] == "verified"
    assert row["member_id"] == "EVM100231"
    assert row["intake"].get("reason")
    assert row["intake"].get("summary")
    assert intake_secs < INTAKE_BUDGET_SECS
