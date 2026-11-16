"""Live QH-5: hold music while the caller waits in the queue, browser and phone.

Run locally with keys and a Postgres in the environment:
    uv run pytest -m live server/tests/test_hold_music_live.py -s -v

Needs DAILY_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL (a throwaway
schema is created and dropped). HOLD_MUSIC_LIVE_DIR (optional) keeps what the caller
heard as WAV files.

The real server runs locally and starts the bot with hold music on (the default) and
hold updates every 8 s (HOLD_UPDATE_SECS) so one lands inside the wait. Two callers:

- browser: POST /calls/browser, the browser caller page's endpoint; a daily-python
  client stands in for the page and joins the room with the caller token.
- phone: a Daily SIP dial-in room (the room the dial-in webhook creates,
  `create_dialin_room`) and a bot started for it. A second room dials the room's SIP
  endpoint with SIP dial-out, so the bot hears and speaks over a real SIP phone leg
  (narrowband, through Daily's SIP gateway) exactly as on a phone call. Only the
  carrier leg and the pinless forward are skipped: the pinless path is the one the
  deployed server's webhook drives (test_dialin_call_live.py), and it ends in this
  same room type with this same bot and transport.

Each caller finishes intake as Maria Alvarez and waits about 15 s in the queue with
nobody available, recording the bot's audio. Checks:

- during the wait the caller always hears something: no gap over 1 s (RMS);
- the hold update is intelligible over the music (Deepgram transcribes it);
- a websocket agent accepts: the music stops within 500 ms of the accept at the
  caller's ear (QH-5b: the server pushes agent_accepted on the bot's own socket; the
  Daily app message is only the fallback), and "Connecting you now." is heard after it.
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
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
import websockets
from agent_assist_bot.bot import BOT_NAME
from agent_assist_bot.calls import DATABASE_SCHEMA_ENV, PostgresCallStore
from agent_assist_bot.handoff import SERVER_URL_ENV
from agent_assist_bot.hold import HOLD_INTERVAL_ENV
from agent_assist_server.app import app
from agent_assist_server.bot_launcher import BotLauncher, BotLaunchRequest
from agent_assist_server.daily import DailyRestClient
from agent_assist_server.queue_api import get_bot_channels, get_queue
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
WINDOW_SECS = 0.1  # RMS window for the continuity check
SILENT_RMS = 30.0  # below this a window is silence (int16)
MAX_GAP_SECS = 1.0
MUSIC_STOP_SECS = 0.5
SIP_LEG_NAME = "phone-caller"


@dataclass
class Leg:
    """The caller's side of one call: what they hear (`rec`) and how they speak."""

    kind: str
    session_id: str
    proc: Any
    caller: HarnessParticipant
    rec: Any
    t_start: float
    rooms: list[str] = field(default_factory=list)

    def accepted_message_at(self) -> float | None:
        """When the agent_accepted app message reached the bot's room (browser only:
        the phone caller is in another room and never sees app messages)."""
        if self.kind != "browser":
            return None
        for t, _, m in self.caller.app_messages:
            if isinstance(m, dict) and m.get("event") == "agent_accepted":
                return t
        return None


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
    snap: dict = {"queue": []}
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


async def run_intake(http, store, leg: Leg, audio) -> dict | None:
    """Answer the bot until the call is queued. The queue is polled while waiting for
    each bot turn: once queued, music follows the handoff line, so that turn never
    goes quiet."""
    after = leg.t_start
    for _ in range(MAX_TURNS):
        turn = asyncio.create_task(wait_bot_turn(leg.rec, after))
        while not turn.done():
            if q := await queued_call(http, leg.session_id):
                turn.cancel()
                return q
            await asyncio.sleep(0.2)
        if turn.result() is None:
            break
        if q := await queued_call(http, leg.session_id):
            return q
        key = next_line(*await intake_row(store, leg.session_id))
        _, after = await asyncio.wait_for(leg.caller.play(audio[key]), 60)
    end = time.time() + 10
    while time.time() < end:
        if q := await queued_call(http, leg.session_id):
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


# ---- the two ways in ----------------------------------------------------------------


@asynccontextmanager
async def browser_leg(http, launcher, t0):
    r = await http.post("/calls/browser")
    assert r.status_code == 200, r.text
    out = r.json()
    room = out["room_url"].rsplit("/", 1)[-1]
    log(t0, f"browser: POST /calls/browser -> 200: room {room}")
    async with HarnessParticipant("caller") as caller:
        await caller.join(out["room_url"], out["caller_token"])
        bot = await caller.wait_for_participant(BOT_NAME, timeout=120)
        yield Leg(
            kind="browser",
            session_id=out["session_id"],
            proc=launcher.processes[out["session_id"]],
            caller=caller,
            rec=caller.recording_for(BOT_NAME),
            t_start=max(caller.joined_at, bot.joined_at),
            rooms=[room],
        )


@asynccontextmanager
async def phone_leg(daily: DailyRestClient, launcher, t0):
    """A SIP dial-in room with its bot, and a phone leg dialed into it over SIP."""
    room = await daily.create_dialin_room(caller=SIP_LEG_NAME)
    session_id = str(uuid.uuid4())
    await launcher.launch(
        BotLaunchRequest(
            room_url=room.url,
            token=await daily.create_meeting_token(room.name),
            session_id=session_id,
        )
    )
    get_queue(app).register_session(session_id, room.url)
    log(t0, f"phone: dial-in room {room.name}, bot started")
    headers = {"Authorization": f"Bearer {daily.api_key}"}
    async with httpx.AsyncClient(base_url=daily.api_url, headers=headers, timeout=30) as c:
        # Daily puts the room's SIP address under config.sip_uri.endpoint.
        r = await c.get(f"/rooms/{room.name}")
        assert r.status_code == 200, r.text
        sip_endpoint = ((r.json().get("config") or {}).get("sip_uri") or {}).get("endpoint")
        assert sip_endpoint, "Daily returned no SIP endpoint for the dial-in room"
        r = await c.post(
            "/rooms",
            json={
                "name": f"hold-music-phone-{uuid.uuid4().hex[:8]}",
                "privacy": "private",
                "properties": {"enable_dialout": True, "exp": int(time.time()) + 900},
            },
        )
        assert r.status_code == 200, r.text
        phone_room = r.json()
        r = await c.post(
            "/meeting-tokens",
            json={"properties": {"room_name": phone_room["name"], "is_owner": True}},
        )
        assert r.status_code == 200, r.text
        token = r.json()["token"]
    rooms = [room.name, phone_room["name"]]
    async with HarnessParticipant("phone") as phone:
        await phone.join(phone_room["url"], token)
        # Wait until the bot is in its room, as Daily's dial-in does before the call
        # is put through (the bot greets the first participant to join).
        end = time.time() + 120
        while time.time() < end:
            names = {p.get("userName") for p in (await daily.get_presence(room.name))["data"]}
            if BOT_NAME in names:
                break
            await asyncio.sleep(0.5)
        else:
            raise AssertionError("bot never joined the dial-in room")
        fut = phone._loop.create_future()
        phone._client.start_dialout(
            {"sipUri": f"sip:{sip_endpoint}", "displayName": SIP_LEG_NAME},
            completion=phone._completion(fut),
        )
        _, error = await fut
        assert not error, f"SIP dial-out failed: {error}"
        leg = await phone.wait_for_participant(SIP_LEG_NAME, timeout=60)
        log(t0, f"phone: SIP leg connected to {sip_endpoint.split('@')[0]}@...")
        yield Leg(
            kind="phone",
            session_id=session_id,
            proc=launcher.processes[session_id],
            caller=phone,
            rec=phone.recording_for(SIP_LEG_NAME),
            t_start=leg.joined_at,
            rooms=rooms,
        )


# ---- the test -----------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["browser", "phone"])
async def test_live_hold_music_while_queued(kind, schema, monkeypatch, tmp_path):
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
    rooms: list[str] = []
    t0 = time.time()
    try:
        while not server.started:
            await asyncio.sleep(0.05)
        get_queue(app)  # the queue service exists before a call registers with it
        async with httpx.AsyncClient(base_url=base, timeout=30) as http:
            leg_cm = browser_leg(http, launcher, t0) if kind == "browser" else None
            leg_cm = leg_cm or phone_leg(daily, launcher, t0)
            async with leg_cm as leg:
                rooms = leg.rooms
                rec = leg.rec
                log(t0, f"{kind}: caller and bot connected; intake starts")

                # ---- intake: no music -------------------------------------------
                queued = await run_intake(http, store, leg, audio)
                assert queued is not None, "intake never enqueued the call"
                queued_at = queued["queued_at"]
                log(t0, f"call enqueued; identity={queued['identity_result']}")
                intake_wins = windows(rec, leg.t_start, queued_at)
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
                    # QH-5b: the bot opened its socket after handoff.
                    assert get_bot_channels(app).count(leg.session_id) == 1, "no bot socket"
                    log(t0, "bot socket to the server is open")
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
                                "session_id": leg.session_id,
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

                # Music stop: the first silent 50 ms window at the caller after accept.
                after = windows(rec, accept_at - 0.5, accept_at + 6, secs=0.05)
                stop = next((t for t, r in after if t >= accept_at and r < SILENT_RMS), None)
                assert stop is not None, "music never stopped after accept"
                accept_stop = stop - accept_at
                msg_at = leg.accepted_message_at()
                hop = (
                    f" (the Daily fallback copy of agent_accepted reached the room"
                    f" {(msg_at - accept_at) * 1000:.0f} ms after accept)"
                    if msg_at
                    else ""
                )
                stopped = f"music stopped at the caller {accept_stop * 1000:.0f} ms after accept"
                log(t0, stopped + hop)
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

                wav = out_dir / f"hold-music-live-{kind}.wav"
                write_wav(wav, audio_slice(rec, queued_at - 1, line_end + 1), SAMPLE_RATE)
                log(t0, f"caller's audio saved to {wav}")

                assert gap <= MAX_GAP_SECS, f"silence gap {gap:.2f}s while queued"
                low = heard_update.lower()
                assert "waiting" in low and "line" in low, heard_update
                assert accept_stop <= MUSIC_STOP_SECS, (
                    f"music stopped {accept_stop:.2f}s after the agent accepted"
                )
                assert "connecting you" in heard_connect.lower(), heard_connect
                assert tail_rms < SILENT_RMS * 3, "music came back after accept"

            # The caller hung up (left the room, or the SIP leg dropped): the bot ends.
            await asyncio.wait_for(leg.proc.wait(), 30)
            log(t0, f"caller left; bot exited returncode={leg.proc.returncode}")
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
        for name in rooms:
            await daily.delete_room(name)
            log(t0, f"room {name} deleted")
        for attr in ("bot_launcher", "queue_service", "bot_channels"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)
