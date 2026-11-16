"""Live P4-3: after accept, the caller's trigger words put the retro auth hero on the agent's
screen within 3 s (DC-6), sent to the agent only, and the agent's accept is logged (DC-9).

Run locally with keys and a Postgres in the environment:
    uv run pytest -m live server/tests/test_assist_live.py -s -v

Needs DAILY_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL.

Same setup as test_e2e_live.py: the real server under uvicorn starts the bot on
POST /calls/browser with DATABASE_SCHEMA set to a throwaway migrated and seeded
schema. A daily-python caller finishes intake as Maria Alvarez (CLM-900001, denied
CO-197). A websocket agent accepts and joins the room as a daily-python participant
(the portal's assist UI is P4-4; this harness is the agent's client). Then:

1. The agent says a line with a call plan phrase ("I can see the claim").
2. The caller says the trigger: "So why did my MRI claim get denied?"
3. The agent client waits for the `hero` assist message.
4. The agent accepts the hero with an RTVI client message (assist_feedback).

Asserts: the hero is `retro_auth_review` for CLM-900001 with its trigger and source,
it reached the agent within 3 s of the caller saying "denied" (word time from
Deepgram on the exact audio played), the plan ticks, the caller got no assist
message, the bot did not speak after its accept line, and assist_events has the
card_sent rows (with latency) and the accept with the simulated action.
Prints the timings (TIMINGS ...).
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
from agent_assist_bot.assist_contract import FEEDBACK_TYPE
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
from harness.turns import SPEECH_RMS, wait_bot_turn

pytestmark = pytest.mark.live

REQUIRED = ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL")
AGENT_NAME = "Agent Ana"
MAX_TURNS = 14
DC6_SECS = 3.0
TRIGGER_WORD = "denied"
AGENT_LINE = "Okay Maria, I pulled up your account, and I can see the claim right here."
CALLER_TRIGGER = "So why did my MRI claim get denied?"
VOICES = {"caller": "aura-2-arcas-en", "agent": "aura-2-thalia-en"}
# Intake STT sometimes mishears the surname ("Hovarez", "Marie"), which leaves the
# caller not_verified and the assist without member data. That is the identity
# capture side card (docs/PLAN.md); this test spells the name so it measures assist.
INTAKE_LINES = dict(
    SCRIPT,
    identity=(
        "Sure. My name is Maria Alvarez, that's A L V A R E Z. My date of birth is March "
        "twelfth, nineteen eighty four. And my member ID is E V M, one zero zero, two three one."
    ),
)


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


async def word_end(pcm: bytes, word: str) -> float:
    """Seconds into `pcm` where `word` ends, from Deepgram prerecorded word timings."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.deepgram.com/v1/listen",
            params={"model": "nova-3", "encoding": "linear16", "sample_rate": SAMPLE_RATE},
            headers={"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}"},
            content=pcm,
        )
    resp.raise_for_status()
    words = resp.json()["results"]["channels"][0]["alternatives"][0]["words"]
    hit = next(w for w in words if w["word"].strip(".,?!").lower() == word)
    return float(hit["end"])


def assist_data(messages) -> list[tuple[float, dict]]:
    out = []
    for t, _sender, m in messages:
        if isinstance(m, dict) and m.get("type") == "server-message":
            d = m.get("data") or {}
            if d.get("type") == "assist":
                out.append((t, d))
    return out


async def queued_call(http: httpx.AsyncClient, session_id: str) -> dict | None:
    for c in (await http.get("/queue")).json()["queue"]:
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
    for _ in range(MAX_TURNS):
        turn = await wait_bot_turn(rec, after)
        if turn is None:
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


async def wait_event(client, pred, timeout: float) -> tuple[float, dict]:
    end = time.time() + timeout
    while time.time() < end:
        for t, d in assist_data(client.app_messages):
            if pred(d):
                return t, d
        await asyncio.sleep(0.02)
    raise TimeoutError("assist event never arrived")


async def test_live_trigger_to_hero_on_the_agent_screen(schema, monkeypatch):
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setenv(SERVER_URL_ENV, base)
    # Hold music (QH-5) would read as bot speech to the RMS turn detection.
    monkeypatch.setenv("HOLD_MUSIC", "off")
    monkeypatch.setenv(DATABASE_SCHEMA_ENV, schema)
    monkeypatch.delenv("QUEUE_PERSIST", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    audio = {k: await synthesize(v, sample_rate=SAMPLE_RATE) for k, v in INTAKE_LINES.items()}
    agent_pcm = await synthesize(AGENT_LINE, sample_rate=SAMPLE_RATE, voice=VOICES["agent"])
    trigger_pcm = await synthesize(CALLER_TRIGGER, sample_rate=SAMPLE_RATE, voice=VOICES["caller"])
    trigger_offset = await word_end(trigger_pcm, TRIGGER_WORD)
    launcher = BotLauncher()
    app.state.bot_launcher = launcher
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serve_task = asyncio.create_task(server.serve())
    daily = DailyRestClient.from_env()
    store = PostgresCallStore(os.environ["DATABASE_URL"], schema=schema)
    room_name = None
    timings: dict[str, float] = {}
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
            proc = launcher.processes[session_id]
            log(t0, f"room {room_name}")

            async with HarnessParticipant("caller") as caller:
                await caller.join(out["room_url"], out["caller_token"])
                bot = await caller.wait_for_participant(BOT_NAME, timeout=120)
                rec = caller.recording_for(BOT_NAME)
                t_start = max(caller.joined_at, bot.joined_at)
                queued = await run_intake(http, store, caller, rec, session_id, audio, t_start)
                assert queued is not None, "intake never enqueued the call"
                log(t0, f"enqueued: identity={queued['identity_result']}")
                assert queued["identity_result"] == "verified"

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
                    await recv_type(ws, "offer", 5)
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

                    async with HarnessParticipant(AGENT_NAME) as agent_client:
                        await agent_client.join(accepted["room_url"], accepted["token"])
                        bot_in_agent_view = await agent_client.wait_for_participant(BOT_NAME, 10)
                        await caller.wait_for_participant(AGENT_NAME, timeout=10)
                        line = await wait_bot_turn(rec, accept_at, start_timeout=10, quiet=1.5)
                        assert line is not None, "bot did not say the connecting line"
                        log(t0, "accepted; bot said its accept line")

                        # Assist starts on accept: status + plan reach the agent.
                        await wait_event(agent_client, lambda d: d["event"] == "plan_update", 10)
                        status = next(
                            d
                            for _, d in assist_data(agent_client.app_messages)
                            if d["event"] == "assist_status"
                        )
                        log(t0, f"assist status: {status['state']} pack={status['pack_id']}")

                        # 1. agent line ticks the call plan ("pulled up", "i can see")
                        await agent_client.play(agent_pcm)
                        try:
                            _, tick = await wait_event(
                                agent_client,
                                lambda d: d["event"] == "plan_update" and d["done"] >= 1,
                                8,
                            )
                            ticked = [s["id"] for s in tick["steps"] if s["done"]]
                        except TimeoutError:
                            ticked = []
                        log(t0, f"plan ticked by the agent line: {ticked}")
                        await asyncio.sleep(1.0)

                        # 2. the caller's trigger
                        start, end = await asyncio.wait_for(caller.play(trigger_pcm), 30)
                        said_at = start + trigger_offset
                        log(t0, f"caller said {TRIGGER_WORD!r} at +{trigger_offset:.2f}s")

                        # 3. hero on the agent's client
                        got_at, hero = await wait_event(
                            agent_client,
                            lambda d: d["event"] == "hero" and d.get("card"),
                            15,
                        )
                        card = hero["card"]
                        timings["trigger_word_to_hero_secs"] = got_at - said_at
                        timings["utterance_end_to_hero_secs"] = got_at - end
                        timings["bot_line_to_send_secs"] = hero["ts"] - card["trigger"]["heard_at"]
                        log(
                            t0,
                            f"hero {card['id']} on the agent client "
                            f"{timings['trigger_word_to_hero_secs']:.2f}s after the trigger word",
                        )
                        await asyncio.sleep(2.0)  # supporting cards

                        # 4. agent accepts the hero
                        await agent_client.send_app_message(
                            {
                                "label": "rtvi-ai",
                                "type": "client-message",
                                "id": "fb-1",
                                "data": {
                                    "t": FEEDBACK_TYPE,
                                    "d": {"v": 1, "card_id": card["id"], "action": "accept"},
                                },
                            },
                            bot_in_agent_view.id,
                        )
                        _, after_accept = await wait_event(
                            agent_client,
                            lambda d: d["event"] == "hero" and d["reason"] == "accepted",
                            10,
                        )
                        log(t0, f"hero after accept: {after_accept['card']}")
                        await asyncio.sleep(1.0)
                        quiet_from = line[1] + 0.5
                        spoke = rec.first_speech_after(quiet_from, threshold=SPEECH_RMS)
                        agent_events = assist_data(agent_client.app_messages)
                        caller_events = assist_data(caller.app_messages)
                        caller_all = list(caller.app_messages)

            await asyncio.wait_for(proc.wait(), 30)
            log(t0, f"caller left; bot exited returncode={proc.returncode}")
            cur = await store._execute(
                "SELECT e.kind, e.card_id, e.data FROM assist_events e "
                "JOIN calls c USING (call_id) WHERE c.session_id = %s ORDER BY e.event_id",
                (session_id,),
            )
            events = await cur.fetchall()
            cur = await store._execute(
                "SELECT status FROM claims WHERE claim_id = 'CLM-900001'", ()
            )
            claim_status = (await cur.fetchone())[0]
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

    # ---- report ---------------------------------------------------------------------
    print("\nAssist messages on the agent client (arrival order):")
    for t, d in agent_events:
        extra = ""
        if d["event"] in ("hero", "card_added") and d.get("card"):
            c = d["card"]
            extra = (
                f" {c['id']} kind={c['kind']} rank={c['rank']} trigger={c['trigger']['phrase']!r}"
            )
        elif d["event"] == "plan_update":
            extra = f" {d['pack_id']} {d['done']}/{d['total']}"
        elif d["event"] == "assist_status":
            extra = f" {d['state']}"
        elif d["event"] == "card_removed":
            extra = f" {d['card_id']} ({d['reason']})"
        print(f"  +{t - accept_at:6.2f}s seq={d['seq']:<3} {d['event']}{extra}")
    print("hero card:")
    print("  " + json.dumps(card, indent=2).replace("\n", "\n  "))
    print("assist_events rows:")
    for kind, card_id, data in events:
        print(f"  {kind:10} {card_id or '-':45} {json.dumps(data)}")
    print(
        f"caller app messages: {len(caller_all)}; assist messages to caller: {len(caller_events)}"
    )
    print("TIMINGS " + json.dumps({k: round(v, 3) for k, v in timings.items()}))

    # ---- assertions -----------------------------------------------------------------
    assert card["rule_id"] == "retro_auth_review"
    assert card["id"] == "nbs:retro_auth_review:CLM-900001"
    assert card["body"]["action"]["id"] == "request_retro_auth_review"
    assert card["trigger"]["role"] == "caller" and card["trigger"]["phrase"]
    assert card["source"]["records"] == [{"connector": "claims", "id": "CLM-900001"}]
    assert timings["trigger_word_to_hero_secs"] < DC6_SECS, timings
    assert caller_events == [], "the caller received assist messages"
    assert spoke is None, f"bot spoke {spoke - quiet_from:.2f}s after its accept line"
    sent = [(c, d) for k, c, d in events if k == "card_sent"]
    assert any(c == card["id"] and "latency_ms" in d for c, d in sent)
    accepts = [(c, d) for k, c, d in events if k == "accept"]
    assert accepts == [
        (
            card["id"],
            {
                "kind": "next_step",
                "pack_id": "claims_status",
                "action_id": "request_retro_auth_review",
                "simulated": True,
            },
        )
    ]
    assert claim_status == "denied"  # DC-9: nothing written back
    final_plan = [d for _, d in agent_events if d["event"] == "plan_update"][-1]
    done = {s["id"]: s["by"] for s in final_plan["steps"] if s["done"]}
    assert done.get("take_next_step") == "action"
    assert done.get("confirm_claim") == "transcript"
