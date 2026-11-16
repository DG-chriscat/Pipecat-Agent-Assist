"""Live intake (PRD IN-3 to IN-6, IN-8): scripted caller audio completes intake.

Run locally with keys and a Postgres in the environment:
    uv run pytest -m live bot/tests/test_live_intake.py -s -v

Needs DAILY_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL.

One run per demo persona (docs/db.md). INTAKE_RUNS=2 runs each persona twice (the
identity card's scored table, docs/spikes/identity.md); INTAKE_PERSONAS=maria_lopez,...
picks personas. INTAKE_RESULTS=<path> appends one JSON line per run.

Each run migrates and seeds a throwaway schema, starts the real bot process
(`python -m agent_assist_bot`, browser caller path) pointed at that schema with
DATABASE_SCHEMA, and joins as the persona with the S1 harness. Each caller line is
Deepgram TTS audio, played after the bot goes quiet. Which line to say next is picked
from the calls row (the next field not yet saved, or a yes / correction while the bot
reads the identity details back), so the script follows the flow instead of guessing
the LLM's wording.

Passes when the calls row is ready_for_queue with the persona's expected identity
result (verified; not_verified for Ben Cole, who gives a wrong DOB on purpose),
the right member ID, a reason, and the intake took under 2 minutes (PRD target).

P5-7: INTAKE_STT_MODEL=nova-3-general|flux-general-en picks the intake STT (unset: the
bot's default, Flux). Each result line also has `response_gaps`: seconds from the end
of each caller line's voice to the bot's first audio after it.

P5-9: LLM_INTAKE=claude-haiku-5-5|phonellm-mini-alpha-2-fp8|think-fast picks the intake
LLM (unset: the Settings default). Result lines also carry what the bot said
(`bot_lines`, transcribed) and the intake function calls (`functions`, names only).
Think-Fast and phoneLLM need DEEPGRAM_API_KEY only; ANTHROPIC_API_KEY stays required
for the other jobs' defaults.
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
from agent_assist_bot.call_settings import SETTINGS_ENV
from agent_assist_bot.calls import PostgresCallStore
from harness.audio import trailing_silence
from harness.intake_script import PERSONAS, Persona, claim_heard_right, next_line
from harness.participant import SAMPLE_RATE, HarnessParticipant
from harness.tts import synthesize
from harness.turns import audio_slice as _slice
from harness.turns import transcribe as _transcribe
from harness.turns import wait_bot_turn as _wait_bot_turn

pytestmark = pytest.mark.live

REQUIRED = ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL")
INTAKE_BUDGET_SECS = 120.0
MAX_TURNS = 16

_picked = [k for k in os.getenv("INTAKE_PERSONAS", "").split(",") if k]
_personas = [p for p in PERSONAS if not _picked or p.key in _picked]
_runs = range(1, int(os.getenv("INTAKE_RUNS", "1")) + 1)
CASES = [pytest.param(p, n, id=f"{p.key}-{n}") for p in _personas for n in _runs]


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


async def _call_row(store: PostgresCallStore, session_id: str) -> dict | None:
    cur = await store._execute(
        "SELECT status, member_id, intake, identity_result, flags, created_at, updated_at "
        "FROM calls WHERE session_id = %s",
        (session_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    keys = ["status", "member_id", "intake", "identity_result", "flags", "created_at", "updated_at"]
    return dict(zip(keys, row, strict=True))


async def _start_bot(room_url: str, token: str, schema: str, session_id: str):
    env = dict(os.environ, DAILY_ROOM_TOKEN=token, DATABASE_SCHEMA=schema)
    # P5-7: INTAKE_STT_MODEL picks the intake STT (the Settings value, as a cold bot
    # gets it); unset runs the bot's default (Flux). P5-9: LLM_INTAKE picks the intake
    # LLM (claude-haiku-5-5, phonellm-mini-alpha-2-fp8, think-fast) the same way.
    overrides = {
        k: v
        for k, v in (
            ("stt_intake_model", os.getenv("INTAKE_STT_MODEL")),
            ("llm_intake", os.getenv("LLM_INTAKE")),
        )
        if v
    }
    if overrides:
        env[SETTINGS_ENV] = json.dumps(overrides)
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "agent_assist_bot",
        "--room-url",
        room_url,
        "--session-id",
        session_id,
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


@pytest.mark.parametrize("who, run", CASES)
async def test_live_intake(schema, who: Persona, run: int):
    from agent_assist_server.daily import DailyRestClient

    session_id = f"live-intake-{who.key}-{uuid.uuid4().hex[:6]}"
    script = who.lines
    audio = {k: await synthesize(v, sample_rate=SAMPLE_RATE) for k, v in script.items()}
    trailing = {k: trailing_silence(v, SAMPLE_RATE) for k, v in audio.items()}
    daily = DailyRestClient.from_env()
    room = await daily.create_browser_room()
    bot_token = await daily.create_meeting_token(room.name)
    caller_token = await daily.create_agent_token(room.name, "caller", int(time.time()) + 600)
    store = PostgresCallStore(os.environ["DATABASE_URL"], schema=schema)
    print(f"\n{who.key} run {run}: room {room.name}, schema {schema}, session {session_id}")
    proc, bot_log = await _start_bot(room.url, bot_token, schema, session_id)
    transcript: list[tuple[str, tuple]] = []
    lines_said: list[str] = []
    bot_lines: list[str] = []
    # P5-7: caller stop (end of the caller's audio, minus its trailing silence) to the
    # bot's first audio after it, one per caller line the bot answered.
    response_gaps: list[float] = []
    caller_stop: float | None = None
    row = None
    t_start = t_done = time.time()
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
                if caller_stop is not None:
                    response_gaps.append(turn[0] - caller_stop)
                # Transcribed after the call, so the harness adds no delay per turn.
                transcript.append(("bot", turn))
                row = await _call_row(store, session_id)
                if row and row["status"] == "ready_for_queue":
                    break
                row = row or {}
                key = next_line(row.get("intake"), row.get("identity_result"), who)
                lines_said.append(key)
                # write_frames never completes if the call dropped; don't hang on it.
                s, e = await asyncio.wait_for(caller.play(audio[key]), 60)
                transcript.append(("caller", (s, script[key])))
                caller_stop = e - trailing[key]
                after = e
            # Give the last function call a moment to land.
            for _ in range(20):
                row = await _call_row(store, session_id)
                if row and row["status"] == "ready_for_queue":
                    break
                await asyncio.sleep(0.5)
            t_done = time.time()
            for who_said, item in transcript:
                if who_said == "bot":
                    said = await _transcribe(_slice(rec, *item))
                    bot_lines.append(said)
                    print(f"[{item[0] - t_start:6.1f}s] bot:    {said}")
                else:
                    print(f"[{item[0] - t_start:6.1f}s] caller: {item[1]}")
    finally:
        await _stop(proc)
        await store.close()
        await daily.delete_room(room.name)
        if log_path := os.getenv("INTAKE_BOT_LOG"):
            with open(log_path, "a") as f:
                f.write(f"===== {session_id}\n" + "\n".join(bot_log) + "\n")

    done = row and row["status"] == "ready_for_queue"
    intake_secs = (row["updated_at"].timestamp() - t_start) if done else t_done - t_start
    print("--- bot log (intake lines) ---")
    for line in bot_log:
        if "[intake]" in line or "[caller]" in line or "ERROR" in line:
            print("  " + line.split(" - ", 1)[-1])
    intake = (row or {}).get("intake") or {}
    # IN-6 v2: a name heard close by spelling, with member ID and DOB exact, is a correct
    # check for that member (close_match; the agent confirms the name, IN-13).
    accepted = {who.expected_identity}
    if who.expected_identity == "verified":
        accepted.add("close_match")
    correct = bool(row) and row["identity_result"] in accepted and row["member_id"] == who.member_id
    result = {
        "persona": who.key,
        "run": run,
        "session_id": session_id,
        "status": row and row["status"],
        "identity_result": row and row["identity_result"],
        "expected_identity": who.expected_identity,
        "identity_correct": correct,
        "identity_exact": bool(row) and row["identity_result"] == who.expected_identity,
        "member_id": row and row["member_id"],
        "heard_name": intake.get("full_name"),
        "heard_member_id": intake.get("member_id"),
        "heard_dob": intake.get("dob"),
        # P5-10: member ID heard right (6 digits as saved) and the claim number heard
        # right (5 digits in the saved answer); None when the persona has no claim.
        "member_id_heard_right": intake.get("member_id") == who.member_id,
        "heard_claim_number": intake.get("claim_number"),
        "claim_heard_right": (
            claim_heard_right(intake.get("claim_number") or "", who.claim_number)
            if who.claim_number
            else None
        ),
        "caller_lines": lines_said,
        "flags": row and row["flags"],
        "intake_seconds": round(intake_secs, 1),
        "stt_intake_model": os.getenv("INTAKE_STT_MODEL") or "default",
        "llm_intake": os.getenv("LLM_INTAKE") or "default",
        "response_gaps": [round(g, 3) for g in response_gaps],
        # P5-9: what the bot said, for spoken artifacts (reasoning text, markdown).
        "bot_lines": bot_lines,
        "functions": [
            line.split("[flow] ", 1)[1]
            for line in bot_log
            if "[flow]" in line and ": function " in line
        ],
    }
    print("INTAKE RESULT " + json.dumps(result, indent=2, default=str))
    if out := os.getenv("INTAKE_RESULTS"):
        with open(out, "a") as f:
            f.write(json.dumps(result, default=str) + "\n")

    assert row is not None, "bot never created the calls row"
    assert row["status"] == "ready_for_queue"
    assert row["identity_result"] in accepted
    assert row["member_id"] == who.member_id
    assert row["intake"].get("reason")
    assert row["intake"].get("summary")
    assert intake_secs < INTAKE_BUDGET_SECS
