"""Live P2-4: browser call -> queue -> agent ws offer -> accept -> agent joins with token.

Run locally with keys in the environment:
    uv run pytest -m live server/tests/test_queue_live.py -s -v

The real server runs under uvicorn on a local port, so the bot subprocess reaches the
queue API through AGENT_ASSIST_SERVER_URL exactly as in production. Driving the full
intake Flow by voice is P2-5; here the test calls the bot's hook (QueueHandoff, the
intake HandoffHook) itself with an IntakeHandoff, where the Flow would call it when
intake ends. The caller and agent are daily-python clients (S1 harness); the agent's
control channel is a websocket client on /ws/queue.

Checks: the bot gets the "queued" message and gives a hold update about 60 s later
(QH-5); the agent ws client gets the offer with the intake summary and a 20 s expiry
(QH-3, QH-7); accept returns a token the agent joins with (QH-6); the bot says
"Connecting you now." and then does not answer the caller (QH-6); caller hang-up ends
the call. Since P3-5 the agent leaving without End is a drop (QH-9): the call goes
back to the front of the queue and the agent is set not available.
"""

import asyncio
import json
import os
import socket
import time

import httpx
import pytest
import uvicorn
import websockets
from agent_assist_bot.bot import BOT_NAME
from agent_assist_bot.handoff import SERVER_URL_ENV, QueueHandoff, enqueue_payload
from agent_assist_bot.intake import IntakeHandoff
from agent_assist_server import browser_call
from agent_assist_server.app import app
from agent_assist_server.bot_launcher import BotLauncher
from agent_assist_server.daily import DailyRestClient
from harness.participant import HarnessParticipant
from harness.tts import synthesize

pytestmark = pytest.mark.live

REQUIRED = ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY")
SPEECH_RMS = 300.0
AGENT_NAME = "Agent Ana"
FLAGS = ["identity_not_verified"]


def intake_handoff(session_id: str) -> IntakeHandoff:
    return IntakeHandoff(
        call_id="live-call",
        session_id=session_id,
        outcome="completed",
        summary="Maria Lopez, MRI claim 70412 denied with CO-197.",
        intake={
            "full_name": "Maria Lopez",
            "member_id": "418302",
            "reason": "MRI claim 70412 denied, CO-197",
        },
        identity_result="not_verified",
        member_id=None,
        flags=tuple(FLAGS),
    )


@pytest.fixture
def keys_present():
    missing = [k for k in REQUIRED if not os.getenv(k)]
    if missing:
        pytest.skip(f"missing env: {', '.join(missing)}")


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


async def wait_speech_after(rec, t: float, timeout: float) -> float | None:
    end = time.time() + timeout
    while time.time() < end:
        start = rec.first_speech_after(t, threshold=SPEECH_RMS)
        if start is not None:
            return start
        await asyncio.sleep(0.1)
    return None


async def wait_quiet(rec, after: float, quiet: float = 4.0, timeout: float = 40) -> float:
    """Wait until the bot has been silent for `quiet` s (greeting + identity question)."""
    end = time.time() + timeout
    while time.time() < end:
        segs = [s for s in rec.segments(threshold=SPEECH_RMS) if s[1] >= after]
        last = segs[-1][1] if segs else after
        if time.time() - last >= quiet:
            return last
        await asyncio.sleep(0.2)
    raise AssertionError("bot never went quiet")


async def test_live_browser_call_queue_offer_accept_join(keys_present, monkeypatch):
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setenv(SERVER_URL_ENV, base)  # inherited by the bot subprocess
    # Hold music (QH-5) would read as bot speech to the RMS checks below.
    monkeypatch.setenv("HOLD_MUSIC", "off")
    # The bot runs the real intake Flow; keep its calls row in memory, off the shared DB.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    launcher = BotLauncher()
    app.state.bot_launcher = launcher
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serve_task = asyncio.create_task(server.serve())
    daily = DailyRestClient.from_env()
    room_name = None
    t0 = time.time()
    try:
        while not server.started:
            await asyncio.sleep(0.05)
        async with httpx.AsyncClient(base_url=base, timeout=30) as http:
            r = await http.post(browser_call.BROWSER_CALL_PATH)
            assert r.status_code == 200, r.text
            out = r.json()
            session_id = out["session_id"]
            room_name = out["room_url"].rsplit("/", 1)[-1]
            log(t0, f"POST /calls/browser -> {r.status_code}: room {room_name}")
            proc = launcher.processes[session_id]

            async with HarnessParticipant("caller") as caller:
                await caller.join(out["room_url"], out["caller_token"])
                log(t0, "caller joined")
                bot = await caller.wait_for_participant(BOT_NAME, timeout=120)
                log(t0, "bot joined")
                bot_rec = caller.recording_for(BOT_NAME)
                greet = await wait_speech_after(bot_rec, bot.joined_at, 10)
                assert greet is not None, "bot never greeted"
                log(t0, f"bot greeting audio at +{greet - bot.joined_at:.2f}s after bot joined")
                last = await wait_quiet(bot_rec, greet)
                log(t0, f"bot quiet after greeting + identity question ({last - greet:.1f}s)")

                # Intake ends: the Flow would call the hook here (full voice intake: P2-5).
                queued_at = time.time()
                handoff = intake_handoff(session_id)
                summary = enqueue_payload(handoff)["summary"]
                await QueueHandoff(base).on_intake_complete(handoff)
                snap = (await http.get("/queue")).json()
                assert [c["session_id"] for c in snap["queue"]] == [session_id]
                assert snap["queue"][0]["flags"] == FLAGS
                log(t0, f"call enqueued: position 1, flags={snap['queue'][0]['flags']}")

                # QH-5: about 60 s on hold, the bot gives a short update.
                hold = await wait_speech_after(bot_rec, queued_at + 1, 75)
                assert hold is not None, "no hold update while queued"
                log(t0, f"hold update audio {hold - queued_at:.1f}s after enqueue")
                assert 55 <= hold - queued_at <= 70
                await asyncio.sleep(6)  # let the hold line finish

                async with websockets.connect(f"ws://127.0.0.1:{port}/ws/queue") as ws:
                    snap = json.loads(await ws.recv())
                    assert snap["type"] == "snapshot"
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
                    log(t0, f"agent ws got offer {time.time() - available_at:.3f}s after available")
                    assert offer["call"]["session_id"] == session_id
                    assert offer["call"]["summary"] == summary
                    assert offer["call"]["flags"] == FLAGS
                    assert offer["call"]["identity_result"] == "not_verified"
                    assert 19 <= offer["expires_at"] - time.time() <= 20.5

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
                    log(t0, f"accept -> token in {time.time() - accept_at:.3f}s")
                    assert accepted["room_url"] == out["room_url"]
                    assert accepted["summary"] == summary
                    assert accepted["flags"] == FLAGS
                    assert accepted["token"]

                    async with HarnessParticipant(AGENT_NAME) as agent_client:
                        await agent_client.join(accepted["room_url"], accepted["token"])
                        joined_after = agent_client.joined_at - accept_at
                        log(t0, f"agent joined room {joined_after:.2f}s after accept")
                        await agent_client.wait_for_participant("caller", timeout=10)
                        await caller.wait_for_participant(AGENT_NAME, timeout=10)

                        line = await wait_speech_after(bot_rec, accept_at, 8)
                        assert line is not None, "bot did not say the connecting line"
                        log(t0, f"bot connecting line audio {line - accept_at:.2f}s after accept")
                        await asyncio.sleep(4)  # "Connecting you now." is short

                        # Quiet: the caller asks a question; the bot must not answer.
                        pcm = await synthesize("Hi, can you tell me where my claim is?")
                        _, said_end = await caller.play(pcm)
                        await asyncio.sleep(10)
                        reply = bot_rec.first_speech_after(said_end - 0.5, threshold=SPEECH_RMS)
                        assert reply is None, f"bot spoke {reply - said_end:.2f}s after caller"
                        log(t0, "bot stayed quiet for 10 s after the caller spoke")

                        presence = await daily.get_presence(room_name)
                        names = sorted(p.get("userName") for p in presence.get("data", []))
                        log(t0, f"Daily presence: {names}")

                    # QH-9 (P3-5): the agent left without ending the call. After the
                    # grace period the call is back at the front of the queue and the
                    # agent is not available; the bot stays with the caller.
                    left_at = time.time()
                    requeued = None
                    while time.time() - left_at < 15:
                        snap = (await http.get("/queue")).json()
                        if [c["session_id"] for c in snap["queue"]] == [session_id]:
                            requeued = snap
                            break
                        await asyncio.sleep(0.2)
                    assert requeued is not None, "agent drop did not re-queue the call"
                    me = next(a for a in snap["agents"] if a["agent_id"] == agent["agent_id"])
                    assert me["state"] == "offline"
                    assert snap["active"] == []
                    assert proc.returncode is None, "bot ended when the agent left"
                    log(t0, f"agent left; call re-queued {time.time() - left_at:.2f}s later")

            # Caller hung up while queued again: bot tells the queue and exits.
            await asyncio.wait_for(proc.wait(), 30)
            log(t0, f"bot exited after caller left, returncode={proc.returncode}")
            snap = (await http.get("/queue")).json()
            me = next(a for a in snap["agents"] if a["agent_id"] == agent["agent_id"])
            log(t0, f"after hang-up: agent state={me['state']}, queue={len(snap['queue'])}")
            assert me["state"] == "offline"
            assert snap["active"] == [] and snap["queue"] == []
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
        if room_name:
            await daily.delete_room(room_name)
            log(t0, f"room {room_name} deleted")
        for attr in ("bot_launcher", "queue_service"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)
