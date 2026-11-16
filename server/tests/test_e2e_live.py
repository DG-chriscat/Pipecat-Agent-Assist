"""Live P2-5 (Phase 2 exit): voice intake -> queue -> agent offer -> accept -> agent joins.

Run locally with keys and a Postgres in the environment:
    uv run pytest -m live server/tests/test_e2e_live.py -s -v

Needs DAILY_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL.

Nothing is called by hand: the real server runs under uvicorn on a local port and
starts the bot itself on POST /calls/browser. The bot inherits
AGENT_ASSIST_SERVER_URL (so its intake hook is QueueHandoff) and DATABASE_SCHEMA
(a throwaway migrated and seeded schema, so identity is checked against the seed
members and nothing touches public). The caller is a daily-python client (S1
harness) that answers as demo persona Maria Alvarez with Deepgram TTS audio; which
line to say next is picked from the calls row. When the Flow confirms the read back
it enqueues the call. A websocket agent on /ws/queue then signs in, goes
available, gets the offer with the intake summary, accepts inside the 20 s offer
window, and joins the room with the returned token. The bot says "Connecting you
now." and then does not answer the caller.

Timings printed at the end (TIMINGS ...) go into docs/spikes/phase2.md.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import socket
import time
import uuid

import httpx
import pytest
import uvicorn
import websockets
from agent_assist_bot.bot import BOT_NAME
from agent_assist_bot.calls import DATABASE_SCHEMA_ENV, PostgresCallStore
from agent_assist_bot.handoff import SERVER_URL_ENV
from agent_assist_server.app import app
from agent_assist_server.bot_launcher import BotLauncher
from agent_assist_server.daily import DailyRestClient
from harness.intake_script import MARIA_ALVAREZ as SCRIPT
from harness.intake_script import next_line
from harness.participant import SAMPLE_RATE, HarnessParticipant
from harness.tts import synthesize
from harness.turns import SPEECH_RMS, audio_slice, transcribe, wait_bot_turn

pytestmark = pytest.mark.live

REQUIRED = ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL")
AGENT_NAME = "Agent Ana"
INTAKE_BUDGET_SECS = 120.0
OFFER_WINDOW_SECS = 20.0
QUEUE_WAIT_SECS = 5.0  # the call sits in the queue with no agent available
MAX_TURNS = 14


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


async def call_row(store: PostgresCallStore, session_id: str) -> dict | None:
    cur = await store._execute(
        "SELECT status, member_id, intake, identity_result, flags FROM calls WHERE session_id = %s",
        (session_id,),
    )
    row = await cur.fetchone()
    keys = ["status", "member_id", "intake", "identity_result", "flags"]
    return dict(zip(keys, row, strict=True)) if row else None


async def wait_queued(http: httpx.AsyncClient, session_id: str, timeout: float) -> dict | None:
    end = time.time() + timeout
    while True:
        for c in (await http.get("/queue")).json()["queue"]:
            if c["session_id"] == session_id:
                return c
        if time.time() >= end:
            return None
        await asyncio.sleep(0.2)


async def test_live_voice_intake_to_queue_to_agent(schema, monkeypatch):
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    # Inherited by the bot subprocess the server launches.
    monkeypatch.setenv(SERVER_URL_ENV, base)
    # Hold music (QH-5) would read as bot speech to the RMS turn detection.
    monkeypatch.setenv("HOLD_MUSIC", "off")
    monkeypatch.setenv(DATABASE_SCHEMA_ENV, schema)
    monkeypatch.delenv("QUEUE_PERSIST", raising=False)  # queue state in memory only
    audio = {k: await synthesize(v, sample_rate=SAMPLE_RATE) for k, v in SCRIPT.items()}
    launcher = BotLauncher()
    app.state.bot_launcher = launcher
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serve_task = asyncio.create_task(server.serve())
    daily = DailyRestClient.from_env()
    store = PostgresCallStore(os.environ["DATABASE_URL"], schema=schema)
    room_name = None
    timings: dict[str, float] = {}
    identity = None
    transcript: list[tuple[str, tuple]] = []
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
            log(t0, f"POST /calls/browser -> 200: room {room_name}, schema {schema}")
            proc = launcher.processes[session_id]

            async with HarnessParticipant("caller") as caller:
                await caller.join(out["room_url"], out["caller_token"])
                bot = await caller.wait_for_participant(BOT_NAME, timeout=120)
                t_start = max(caller.joined_at, bot.joined_at)
                log(t0, "caller and bot in the room; intake starts")
                rec = caller.recording_for(BOT_NAME)

                # ---- voice intake ---------------------------------------------
                after = t_start
                queued = None
                for _ in range(MAX_TURNS):
                    turn = await wait_bot_turn(rec, after)
                    if turn is None:
                        break
                    transcript.append(("bot", turn))
                    queued = await wait_queued(http, session_id, 0)
                    if queued:
                        break
                    row = await call_row(store, session_id)
                    row = row or {}
                    key = next_line(row.get("intake"), row.get("identity_result"))
                    s, e = await asyncio.wait_for(caller.play(audio[key]), 60)
                    transcript.append(("caller", (s, SCRIPT[key])))
                    after = e
                queued = queued or await wait_queued(http, session_id, 10)
                assert queued is not None, "intake never enqueued the call"
                timings["intake_secs"] = queued["queued_at"] - t_start
                log(t0, f"call enqueued {timings['intake_secs']:.1f}s after intake started")
                row = await call_row(store, session_id)
                assert row is not None
                assert row["status"] == "ready_for_queue"
                assert queued["outcome"] == "completed"
                # The queue carries what intake recorded. Whether identity verifies by
                # voice depends on STT hearing the member ID (test_live_intake gates
                # that); here we check the handoff, so a mismatch is logged, not fatal.
                assert queued["identity_result"] == row["identity_result"]
                assert queued["member_id"] == row["member_id"]
                assert queued["flags"] == list(row["flags"] or [])
                identity = row["identity_result"]
                log(t0, f"identity_result={identity} flags={queued['flags']}")
                summary = queued["summary"]
                assert summary.get("summary") and summary.get("reason")
                log(t0, f"queued summary: {json.dumps(summary)}")

                # ---- waits in the queue, then an agent is offered the call ----
                await asyncio.sleep(QUEUE_WAIT_SECS)
                snap = (await http.get("/queue")).json()
                assert [c["session_id"] for c in snap["queue"]] == [session_id]
                assert snap["queue"][0]["status"] == "queued"
                log(t0, f"call still waiting after {QUEUE_WAIT_SECS:.0f}s with no agent")

                async with websockets.connect(f"ws://127.0.0.1:{port}/ws/queue") as ws:
                    assert json.loads(await ws.recv())["type"] == "snapshot"
                    await ws.send(json.dumps({"type": "sign_in", "name": AGENT_NAME}))
                    agent = (await recv_type(ws, "signed_in", 5))["agent"]
                    available_at = time.time()
                    await ws.send(
                        json.dumps(
                            {
                                "type": "set_status",
                                "agent_id": agent["agent_id"],
                                "state": "available",
                            }
                        )
                    )
                    offer = await recv_type(ws, "offer", 5)
                    offered_at = time.time()
                    timings["available_to_offer_secs"] = offered_at - available_at
                    timings["queue_wait_secs"] = offered_at - queued["queued_at"]
                    log(t0, f"offer {timings['available_to_offer_secs']:.3f}s after available")
                    assert offer["call"]["session_id"] == session_id
                    assert offer["call"]["summary"] == summary
                    window = offer["expires_at"] - offered_at
                    assert OFFER_WINDOW_SECS - 1 <= window <= OFFER_WINDOW_SECS + 0.5

                    # A person reads the offer for a moment before accepting.
                    await asyncio.sleep(2)
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
                    accepted = await recv_type(ws, "accepted", 10)
                    timings["offer_to_accept_secs"] = accept_at - offered_at
                    timings["accept_to_token_secs"] = time.time() - accept_at
                    assert accept_at < offer["expires_at"], "accepted after the offer expired"
                    assert accepted["room_url"] == out["room_url"]
                    assert accepted["summary"] == summary
                    assert accepted["token"]
                    log(t0, f"accept -> token in {timings['accept_to_token_secs']:.3f}s")

                    async with HarnessParticipant(AGENT_NAME) as agent_client:
                        await agent_client.join(accepted["room_url"], accepted["token"])
                        timings["accept_to_agent_joined_secs"] = agent_client.joined_at - accept_at
                        log(
                            t0,
                            "agent joined room "
                            f"{timings['accept_to_agent_joined_secs']:.2f}s after accept",
                        )
                        await agent_client.wait_for_participant("caller", timeout=10)
                        await caller.wait_for_participant(AGENT_NAME, timeout=10)

                        line = await wait_bot_turn(rec, accept_at, start_timeout=8, quiet=1.5)
                        assert line is not None, "bot did not say the connecting line"
                        transcript.append(("bot", line))
                        timings["accept_to_connecting_line_secs"] = line[0] - accept_at
                        said = await transcribe(audio_slice(rec, *line))
                        log(t0, f"bot connecting line {line[0] - accept_at:.2f}s after accept")
                        assert "connecting" in said.lower(), said

                        # Goes quiet: the caller asks a question; the bot must not answer.
                        pcm = await synthesize("Hi, can you tell me where my claim is?")
                        s, said_end = await caller.play(pcm)
                        transcript.append(("caller", (s, "Hi, can you tell me where my claim is?")))
                        await asyncio.sleep(10)
                        reply = rec.first_speech_after(said_end - 0.5, threshold=SPEECH_RMS)
                        assert reply is None, f"bot spoke {reply - said_end:.2f}s after caller"
                        log(t0, "bot stayed quiet for 10 s after the caller spoke")

                        presence = await daily.get_presence(room_name)
                        names = sorted(p.get("userName") for p in presence.get("data", []))
                        log(t0, f"Daily presence: {names}")
                        assert AGENT_NAME in names and "caller" in names

                    snap = (await http.get("/queue")).json()
                    me = next(a for a in snap["agents"] if a["agent_id"] == agent["agent_id"])
                    assert me["state"] == "on_call"
                    assert proc.returncode is None

                print("--- transcript (bot turns transcribed by Deepgram) ---")
                for who, item in transcript:
                    if who == "bot":
                        text = await transcribe(audio_slice(rec, *item))
                        print(f"[{item[0] - t_start:6.1f}s] bot:    {text}")
                    else:
                        print(f"[{item[0] - t_start:6.1f}s] caller: {item[1]}")

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

    print("TIMINGS " + json.dumps({k: round(v, 2) for k, v in timings.items()}, indent=2))
    print(f"IDENTITY {identity}")
    assert timings["intake_secs"] < INTAKE_BUDGET_SECS
    assert timings["offer_to_accept_secs"] < OFFER_WINDOW_SECS
