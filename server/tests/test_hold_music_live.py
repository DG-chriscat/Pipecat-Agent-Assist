"""Live QH-5: hold music while the caller waits in the queue.

Run locally with keys and a Postgres in the environment:
    uv run pytest -m live server/tests/test_hold_music_live.py -s -v

Needs DAILY_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL (a throwaway
schema is created and dropped). HOLD_MUSIC_LIVE_DIR (optional) keeps what the caller
heard as WAV files.

The real server starts the bot (POST /calls/browser) with hold music on (the default)
and hold updates every 8 s (HOLD_UPDATE_SECS) so one lands inside the wait. A
daily-python caller (S1 harness) finishes intake as Maria Alvarez and then waits
about 15 s in the queue with nobody available, recording the bot's audio. Checks:

- during the wait the caller always hears something: no gap over 1 s (RMS);
- the hold update is intelligible over the music (Deepgram transcribes it);
- a websocket agent accepts: the music stops within 500 ms of the accept at the
  caller's ear (the server's agent_accepted app message goes out on a connection the
  offer already warmed), and "Connecting you now." is heard after it.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
import os
import socket
import struct
import time
import uuid
from pathlib import Path

import httpx
import pytest
import uvicorn
import websockets
from agent_assist_bot.bot import BOT_NAME
from agent_assist_bot.calls import DATABASE_SCHEMA_ENV, PostgresCallStore
from agent_assist_bot.handoff import SERVER_URL_ENV
from agent_assist_bot.hold import HOLD_INTERVAL_ENV
from agent_assist_server.app import app
from agent_assist_server.bot_launcher import BotLauncher
from agent_assist_server.daily import DailyRestClient
from harness.audio import write_wav
from harness.intake_script import MARIA_ALVAREZ as SCRIPT
from harness.intake_script import next_line
from harness.participant import SAMPLE_RATE, HarnessParticipant
from harness.tts import synthesize
from harness.turns import SPEECH_RMS, audio_slice, transcribe, wait_bot_turn

pytestmark = pytest.mark.live

REQUIRED = ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL")
AGENT_NAME = "Agent Ana"
MAX_TURNS = 14
HOLD_UPDATE_SECS = 8
WAIT_SECS = 15.0  # in the queue before the agent accepts
WINDOW_SECS = 0.1  # RMS window for the continuity and stop checks
SILENT_RMS = 30.0  # below this a 100 ms window is silence (int16)
MAX_GAP_SECS = 1.0
MUSIC_STOP_SECS = 0.5


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


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def log(t0: float, msg: str) -> None:
    print(f"[{time.time() - t0:7.2f}s] {msg}", flush=True)


async def recv_type(ws, kind: str, timeout: float) -> dict:
    async def _wait():
        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] == kind:
                return msg

    return await asyncio.wait_for(_wait(), timeout)


async def queued_call(http: httpx.AsyncClient, session_id: str) -> dict | None:
    for attempt in range(3):
        try:
            snap = (await http.get("/queue")).json()
            break
        except httpx.TransportError:  # uvicorn closed an idle keep-alive connection
            if attempt == 2:
                raise
    for c in snap["queue"]:
        if c["session_id"] == session_id:
            return c
    return None


async def intake_row(store: PostgresCallStore, session_id: str):
    cur = await store._execute(
        "SELECT intake, identity_result FROM calls WHERE session_id = %s", (session_id,)
    )
    row = await cur.fetchone()
    return (row[0], row[1]) if row else (None, None)


async def run_intake(http, store, caller, rec, session_id, audio, after) -> dict | None:
    """Answer the bot until the call is queued. The queue is polled while waiting for
    each bot turn: once queued, music follows the handoff line, so that turn never
    goes quiet."""
    for _ in range(MAX_TURNS):
        turn = asyncio.create_task(wait_bot_turn(rec, after))
        while not turn.done():
            if q := await queued_call(http, session_id):
                turn.cancel()
                return q
            await asyncio.sleep(0.2)
        if turn.result() is None:
            break
        if q := await queued_call(http, session_id):
            return q
        key = next_line(*await intake_row(store, session_id))
        _, after = await asyncio.wait_for(caller.play(audio[key]), 60)
    end = time.time() + 10
    while time.time() < end:
        if q := await queued_call(http, session_id):
            return q
        await asyncio.sleep(0.2)
    return None


def windows(rec, start: float, end: float, secs: float = WINDOW_SECS) -> list[tuple[float, float]]:
    """(time, RMS) per `secs` of the recording between start and end."""
    pcm = audio_slice(rec, start, end)
    first = next(t for t, _ in rec.chunks if t >= start - 0.05)
    n = int(secs * SAMPLE_RATE)
    out = []
    for i in range(0, len(pcm) // 2 - n + 1, n):
        samples = struct.unpack(f"<{n}h", pcm[2 * i : 2 * (i + n)])
        out.append((first + i / SAMPLE_RATE, math.sqrt(sum(s * s for s in samples) / n)))
    return out


def longest_gap(wins: list[tuple[float, float]]) -> float:
    gap = longest = 0.0
    for _, r in wins:
        gap = gap + WINDOW_SECS if r < SILENT_RMS else 0.0
        longest = max(longest, gap)
    return longest


async def test_live_hold_music_while_queued(schema, monkeypatch, tmp_path):
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    # Inherited by the bot subprocess. Hold music is on by default; make sure.
    monkeypatch.setenv(SERVER_URL_ENV, base)
    monkeypatch.setenv(DATABASE_SCHEMA_ENV, schema)
    monkeypatch.setenv(HOLD_INTERVAL_ENV, str(HOLD_UPDATE_SECS))
    for k in ("HOLD_MUSIC", "HOLD_MUSIC_VOLUME", "HOLD_MUSIC_PATH", "QUEUE_PERSIST"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    out_dir = Path(os.getenv("HOLD_MUSIC_LIVE_DIR") or tmp_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    audio = {k: await synthesize(v, sample_rate=SAMPLE_RATE) for k, v in SCRIPT.items()}
    launcher = BotLauncher()
    app.state.bot_launcher = launcher
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serve_task = asyncio.create_task(server.serve())
    daily = DailyRestClient.from_env()
    store = PostgresCallStore(os.environ["DATABASE_URL"], schema=schema)
    room_name = None
    t0 = time.time()
    try:
        while not server.started:
            await asyncio.sleep(0.05)
        async with httpx.AsyncClient(base_url=base, timeout=30) as http:
            r = await http.post("/calls/browser")
            assert r.status_code == 200, r.text
            out = r.json()
            session_id = out["session_id"]
            room_name = out["room_url"].rsplit("/", 1)[-1]
            log(t0, f"POST /calls/browser -> 200: room {room_name}")
            proc = launcher.processes[session_id]

            async with HarnessParticipant("caller") as caller:
                await caller.join(out["room_url"], out["caller_token"])
                bot = await caller.wait_for_participant(BOT_NAME, timeout=120)
                rec = caller.recording_for(BOT_NAME)
                t_start = max(caller.joined_at, bot.joined_at)
                log(t0, "caller and bot in the room; intake starts")

                # ---- intake: no music -------------------------------------------
                queued = await run_intake(http, store, caller, rec, session_id, audio, t_start)
                assert queued is not None, "intake never enqueued the call"
                queued_at = queued["queued_at"]
                log(t0, f"call enqueued; identity={queued['identity_result']}")
                intake_wins = windows(rec, t_start, queued_at)
                assert longest_gap(intake_wins) > 1.0, "audio under intake: music too early?"
                log(
                    t0,
                    f"intake: longest silence {longest_gap(intake_wins):.1f}s "
                    "(the bot waits for the caller in silence, no music)",
                )

                # ---- the wait: music, a hold update over it ---------------------
                async with websockets.connect(f"ws://127.0.0.1:{port}/ws/queue") as ws:
                    assert json.loads(await ws.recv())["type"] == "snapshot"
                    await ws.send(json.dumps({"type": "sign_in", "name": AGENT_NAME}))
                    agent = (await recv_type(ws, "signed_in", 5))["agent"]
                    await asyncio.sleep(max(0.0, queued_at + WAIT_SECS - time.time()))
                    await ws.send(
                        json.dumps(
                            {
                                "type": "set_status",
                                "agent_id": agent["agent_id"],
                                "state": "available",
                            }
                        )
                    )
                    await recv_type(ws, "offer", 5)
                    # A person reads the card before pressing Accept (the wrap up live
                    # test waits 1 s too). Accepting 0 ms after the offer puts the
                    # accept message behind the offer's connection warm up (~550 ms).
                    await asyncio.sleep(1.0)
                    accept_at = time.time()
                    await ws.send(
                        json.dumps(
                            {
                                "type": "accept",
                                "agent_id": agent["agent_id"],
                                "session_id": session_id,
                            }
                        )
                    )
                    await recv_type(ws, "accepted", 10)
                    log(t0, f"agent accepted after {accept_at - queued_at:.1f}s in the queue")
                    await asyncio.sleep(8)  # music stop, then "Connecting you now."

                wait = windows(rec, queued_at, accept_at)
                per_sec = [
                    round(
                        math.sqrt(
                            sum(r * r for t, r in wait if s <= t - queued_at < s + 1)
                            / max(1, sum(1 for t, _ in wait if s <= t - queued_at < s + 1))
                        )
                    )
                    for s in range(int(accept_at - queued_at))
                ]
                gap = longest_gap(wait)
                log(t0, f"wait RMS per second (int16): {per_sec}")
                log(t0, f"wait: {len(wait)} windows, longest silence {gap:.2f}s")

                # The hold update: the first bot speech turn about HOLD_UPDATE_SECS in.
                upd = audio_slice(rec, queued_at + HOLD_UPDATE_SECS - 1, accept_at)
                heard_update = await transcribe(upd)
                log(t0, f"hold update over the music: {heard_update!r}")

                # Music stop: the first silent 50 ms window after the bot heard the
                # accept (the caller gets the same app message at the same moment).
                msgs = [
                    t
                    for t, _, m in caller.app_messages
                    if isinstance(m, dict) and m.get("event") == "agent_accepted"
                ]
                assert msgs, "the room never got the agent_accepted message"
                msg_at = msgs[0]
                after = windows(rec, msg_at - 0.5, msg_at + 6, secs=0.05)
                stop = next((t for t, r in after if t >= msg_at and r < SILENT_RMS), None)
                assert stop is not None, "music never stopped after accept"
                stop_secs = stop - msg_at
                log(
                    t0,
                    f"agent_accepted reached the room {(msg_at - accept_at) * 1000:.0f} ms after "
                    f"accept; music stopped {stop_secs * 1000:.0f} ms after that "
                    f"({(stop - accept_at) * 1000:.0f} ms after accept)",
                )
                # The connecting line: the first loud window after the music stopped.
                line = next((t for t, r in after if t > stop and r >= SPEECH_RMS), None)
                assert line is not None, "no connecting line after the music stopped"
                line_end = line + 3.0
                heard_connect = await transcribe(audio_slice(rec, line - 0.1, line_end))
                log(
                    t0,
                    f"after the music: {heard_connect!r} "
                    f"(+{(line - stop) * 1000:.0f} ms after the music stopped)",
                )
                tail = windows(rec, line_end + 0.5, line_end + 3)
                tail_rms = max(r for _, r in tail) if tail else 0.0
                log(t0, f"after the connecting line: max RMS {tail_rms:.0f} (no music)")

                write_wav(
                    out_dir / "hold-music-live-wait.wav",
                    audio_slice(rec, queued_at - 1, line_end + 1),
                    SAMPLE_RATE,
                )
                log(t0, f"caller's audio saved to {out_dir / 'hold-music-live-wait.wav'}")

                assert gap <= MAX_GAP_SECS, f"silence gap {gap:.2f}s while queued"
                low = heard_update.lower()
                assert "waiting" in low and "line" in low, heard_update
                assert stop_secs <= MUSIC_STOP_SECS, f"music stopped {stop_secs:.2f}s after accept"
                accept_stop = stop - accept_at
                assert accept_stop <= MUSIC_STOP_SECS, (
                    f"music stopped {accept_stop:.2f}s after the agent accepted"
                )
                assert "connecting you" in heard_connect.lower(), heard_connect
                assert tail_rms < SILENT_RMS * 3, "music came back after accept"

            await asyncio.wait_for(proc.wait(), 30)
            log(t0, f"caller left; bot exited returncode={proc.returncode}")
    finally:
        for p in launcher.processes.values():
            if p.returncode is None:
                p.terminate()
                try:
                    await asyncio.wait_for(p.wait(), 10)
                except TimeoutError:
                    p.kill()
        server.should_exit = True
        await asyncio.wait_for(serve_task, 10)
        await store.close()
        if room_name:
            await daily.delete_room(room_name)
            log(t0, f"room {room_name} deleted")
        for attr in ("bot_launcher", "queue_service"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)
