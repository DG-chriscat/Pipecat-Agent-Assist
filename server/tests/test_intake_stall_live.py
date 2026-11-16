"""Live replay of the Nov 16 prod stall (session a9a276e8): name wrong, corrected to
James Chen, "Correctamundo". The call must reach POST /queue/calls and a websocket
agent must get the offer.

Run locally with keys and a Postgres in the environment:
    uv run pytest -m live server/tests/test_intake_stall_live.py -s -v

Needs DAILY_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL.

Same setup as test_e2e_live.py: the real server under uvicorn starts the bot on POST
/calls/browser; the caller is a daily-python client speaking Deepgram TTS audio.
The script follows the calls row: the first identity line gives the wrong last name
("Chang"), the read back is corrected to James Chen, and every confirm is
"Correctamundo." (the reply the prod LLM answered in text instead of calling
verify_identity).
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
from harness.intake_script import persona
from harness.participant import SAMPLE_RATE, HarnessParticipant
from harness.tts import synthesize
from harness.turns import audio_slice, transcribe, wait_bot_turn

pytestmark = pytest.mark.live

REQUIRED = ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL")
AGENT_NAME = "Agent Ana"
MAX_TURNS = 16
JAMES = persona("james_chen")
SCRIPT = {
    # Wrong last name on purpose, as in the prod call.
    "identity": (
        "Sure. My name is James Chang. My date of birth is July fourth, nineteen "
        "seventy nine. And my member ID is E V M, one zero zero, two three two."
    ),
    "fix_name": "No, my name is James Chen. Chen, C H E N.",
    "fix_member_id": JAMES.lines["fix_member_id"],
    "confirm": "Correctamundo.",
    "reason": JAMES.lines["reason"],
    "claim_number": JAMES.lines["claim_number"],
    "service_date": JAMES.lines["service_date"],
    "claim_concern": JAMES.lines["claim_concern"],
}
ORDER = ["reason", "claim_number", "service_date", "claim_concern"]


def next_key(intake: dict | None, identity_result: str | None) -> str:
    intake = intake or {}
    if not intake.get("full_name"):
        return "identity"
    if not identity_result:
        name = (intake.get("full_name") or "").lower().split()
        if name[-1:] != ["chen"]:
            return "fix_name"
        mid = "".join(c for c in intake.get("member_id", "") if c.isalnum()).upper()
        return "confirm" if mid == JAMES.member_id else "fix_member_id"
    for key in ORDER:
        if not intake.get(key):
            return key
    return "confirm"  # the final read back


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


async def call_row(store: PostgresCallStore, session_id: str) -> dict:
    cur = await store._execute(
        "SELECT status, member_id, intake, identity_result, flags FROM calls WHERE session_id = %s",
        (session_id,),
    )
    row = await cur.fetchone()
    keys = ["status", "member_id", "intake", "identity_result", "flags"]
    return dict(zip(keys, row, strict=True)) if row else {}


async def wait_queued(http: httpx.AsyncClient, session_id: str, timeout: float) -> dict | None:
    end = time.time() + timeout
    while True:
        for c in (await http.get("/queue")).json()["queue"]:
            if c["session_id"] == session_id:
                return c
        if time.time() >= end:
            return None
        await asyncio.sleep(0.2)


async def test_live_nov16_script_reaches_the_queue_and_an_agent(schema, monkeypatch):
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setenv(SERVER_URL_ENV, base)
    monkeypatch.setenv(DATABASE_SCHEMA_ENV, schema)
    monkeypatch.delenv("QUEUE_PERSIST", raising=False)
    audio = {k: await synthesize(v, sample_rate=SAMPLE_RATE) for k, v in SCRIPT.items()}
    launcher = BotLauncher()
    app.state.bot_launcher = launcher
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serve_task = asyncio.create_task(server.serve())
    store = PostgresCallStore(os.environ["DATABASE_URL"], schema=schema)

    daily = DailyRestClient.from_env()
    room_name = None
    transcript: list[tuple[str, tuple]] = []
    said: list[str] = []
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
                await caller.wait_for_participant(BOT_NAME, timeout=120)
                t_start = time.time()
                rec = caller.recording_for(BOT_NAME)
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
                    key = next_key(row.get("intake"), row.get("identity_result"))
                    said.append(key)
                    log(t0, f"caller says [{key}]")
                    s, e = await asyncio.wait_for(caller.play(audio[key]), 60)
                    transcript.append(("caller", (s, SCRIPT[key])))
                    after = e
                queued = queued or await wait_queued(http, session_id, 15)
                assert queued is not None, "intake never enqueued the call (POST /queue/calls)"
                intake_secs = queued["queued_at"] - t_start
                log(t0, f"POST /queue/calls: enqueued {intake_secs:.1f}s after intake started")
                row = await call_row(store, session_id)
                log(
                    t0,
                    f"calls row: status={row['status']} identity={row['identity_result']} "
                    f"member_id={row['member_id']} flags={row['flags']}",
                )
                log(t0, f"queued summary: {json.dumps(queued['summary'])}")
                assert "fix_name" in said and "confirm" in said, said
                assert row["status"] == "ready_for_queue"
                assert row["intake"]["full_name"].lower().endswith("chen")

                async with websockets.connect(f"ws://127.0.0.1:{port}/ws/queue") as ws:
                    assert json.loads(await ws.recv())["type"] == "snapshot"
                    await ws.send(json.dumps({"type": "sign_in", "name": AGENT_NAME}))
                    agent = (await recv_type(ws, "signed_in", 5))["agent"]
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
                    log(t0, f"websocket agent got the offer: {json.dumps(offer['call'])}")
                    assert offer["call"]["session_id"] == session_id

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
