"""Live P3-2: the agent portal in headless Chromium accepts a real call.

Run locally with keys, a Postgres, a built portal (npm run build in portal/), and
Playwright's Chromium:
    PLAYWRIGHT_BROWSERS_PATH=... uv run --with playwright \
        pytest -m live server/tests/test_portal_call_live.py -s -v

Needs DAILY_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL.
P3_SCREENSHOT_DIR (optional) keeps the two 1600x1000 screenshots.

Same setup as the Phase 2 exit test (test_e2e_live.py): the real server under
uvicorn starts the bot on POST /calls/browser, a daily-python caller (S1 harness)
finishes intake as demo persona Maria Lopez, and the bot enqueues the call. Then,
instead of a websocket agent, the built portal at /agent runs in headless Chromium
(fake mic): sign in, go available, the incoming card rings, Enter accepts, the
portal joins the room with the Pipecat client SDK and the Daily transport, and the
in-call shell shows. Daily presence must list the caller, the bot, and the agent.
End leaves the room and sets wrap up.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import socket
import time
import uuid
from pathlib import Path

import httpx
import pytest
import uvicorn
from agent_assist_bot.bot import BOT_NAME
from agent_assist_bot.calls import DATABASE_SCHEMA_ENV, PostgresCallStore
from agent_assist_bot.handoff import SERVER_URL_ENV
from agent_assist_server.app import app
from agent_assist_server.bot_launcher import BotLauncher
from agent_assist_server.daily import DailyRestClient
from agent_assist_server.portal import DEFAULT_DIST
from harness.intake_script import MARIA_LOPEZ as SCRIPT
from harness.intake_script import next_line
from harness.participant import SAMPLE_RATE, HarnessParticipant
from harness.tts import synthesize
from harness.turns import wait_bot_turn

pytestmark = pytest.mark.live

REQUIRED = ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL")
AGENT_NAME = "Agent Ana"
MAX_TURNS = 14
VIEWPORT = {"width": 1600, "height": 1000}
CHROMIUM_ARGS = [
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
]


@pytest.fixture
def keys_present():
    missing = [k for k in REQUIRED if not os.getenv(k)]
    if missing:
        pytest.skip(f"missing env: {', '.join(missing)}")
    if not (DEFAULT_DIST / "index.html").is_file():
        pytest.skip("portal not built: run npm ci && npm run build in portal/")
    pytest.importorskip("playwright.async_api")


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


@pytest.fixture
def shots(tmp_path) -> Path:
    d = Path(os.getenv("P3_SCREENSHOT_DIR") or tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    return d


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def log(t0: float, msg: str) -> None:
    print(f"[{time.time() - t0:7.2f}s] {msg}", flush=True)


async def intake_row(store: PostgresCallStore, session_id: str) -> tuple[dict | None, str | None]:
    """(intake, identity_result) for next_line."""
    cur = await store._execute(
        "SELECT intake, identity_result FROM calls WHERE session_id = %s", (session_id,)
    )
    row = await cur.fetchone()
    return (row[0], row[1]) if row else (None, None)


async def queued_call(http: httpx.AsyncClient, session_id: str) -> dict | None:
    for c in (await http.get("/queue")).json()["queue"]:
        if c["session_id"] == session_id:
            return c
    return None


async def agent_state(http: httpx.AsyncClient) -> str:
    agents = (await http.get("/queue")).json()["agents"]
    return next(a["state"] for a in agents if a["name"] == AGENT_NAME)


async def run_intake(http, store, caller, rec, session_id, audio, after) -> dict | None:
    """Answer the bot's intake turns until the call is queued (as in test_e2e_live)."""
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


async def test_live_portal_accepts_call_and_joins_room(schema, shots, monkeypatch):
    from playwright.async_api import async_playwright, expect

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setenv(SERVER_URL_ENV, base)
    # Hold music (QH-5) would read as bot speech to the RMS turn detection.
    monkeypatch.setenv("HOLD_MUSIC", "off")
    monkeypatch.setenv(DATABASE_SCHEMA_ENV, schema)
    monkeypatch.delenv("QUEUE_PERSIST", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)  # the module app was built without login
    audio = {k: await synthesize(v, sample_rate=SAMPLE_RATE) for k, v in SCRIPT.items()}
    question = await synthesize("Hi, can you tell me where my claim is? It was for an MRI.")
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
        async with httpx.AsyncClient(base_url=base, timeout=30) as http, async_playwright() as pw:
            browser = await pw.chromium.launch(args=CHROMIUM_ARGS)
            ctx = await browser.new_context(viewport=VIEWPORT, permissions=["microphone"])
            page = await ctx.new_page()
            page.on("console", lambda m: m.type == "error" and print(f"  [portal] {m.text}"))

            # ---- agent signs in, stays not available while intake runs ----------
            await page.goto(f"{base}/agent/")
            await page.get_by_label("Your name").fill(AGENT_NAME)
            await page.get_by_role("button", name="Sign in").click()
            toggle = page.get_by_role("switch", name="Available")
            await expect(toggle).to_be_enabled(timeout=10_000)
            log(t0, "portal signed in")

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
                t_start = max(caller.joined_at, bot.joined_at)
                rec = caller.recording_for(BOT_NAME)
                log(t0, "caller and bot in the room; intake starts")
                queued = await run_intake(http, store, caller, rec, session_id, audio, t_start)
                assert queued is not None, "intake never enqueued the call"
                summary = queued["summary"]
                identity = queued["identity_result"]
                log(t0, f"call enqueued: identity={identity} {json.dumps(summary)}")

                # ---- available -> the offer card rings ------------------------------
                available_at = time.time()
                await toggle.click()
                card = page.get_by_role("dialog", name="Incoming call")
                await expect(card).to_be_visible(timeout=5_000)
                timings["available_to_card_secs"] = time.time() - available_at
                name = summary.get("full_name") or "Unknown caller"
                await expect(card).to_contain_text(name)
                await expect(card.get_by_role("timer")).to_have_text(re.compile(r"^(1\d|20)$"))
                secs = timings["available_to_card_secs"]
                log(t0, f"incoming card up {secs:.2f}s after available")
                await page.wait_for_timeout(1500)
                await page.screenshot(path=str(shots / "p3-2-incoming-call.png"))

                # ---- Enter accepts; summary first, then audio ------------------------
                accept_at = time.time()
                await page.keyboard.press("Enter")
                island = page.get_by_role("group", name="Call")
                await expect(island).to_be_visible(timeout=10_000)
                await expect(page.get_by_role("complementary", name="Caller")).to_contain_text(name)
                timings["accept_to_summary_secs"] = time.time() - accept_at
                await expect(page.get_by_role("button", name="Mute")).to_be_enabled(timeout=30_000)
                timings["accept_to_audio_joined_secs"] = time.time() - accept_at
                log(
                    t0,
                    f"summary on screen {timings['accept_to_summary_secs']:.2f}s after Enter, "
                    f"room joined {timings['accept_to_audio_joined_secs']:.2f}s after Enter",
                )
                await caller.wait_for_participant(AGENT_NAME, timeout=15)
                log(t0, "caller sees the portal agent in the room")

                # Daily's REST presence lags a joined participant by a few seconds.
                names: list[str] = []
                end = time.time() + 20
                while time.time() < end:
                    presence = await daily.get_presence(room_name)
                    names = sorted(p.get("userName") or "" for p in presence.get("data", []))
                    if {"caller", BOT_NAME, AGENT_NAME} <= set(names):
                        break
                    await asyncio.sleep(1)
                log(t0, f"Daily presence: {names}")
                assert {"caller", BOT_NAME, AGENT_NAME} <= set(names), names

                # The caller talks: the island waveform follows remote audio.
                play = asyncio.create_task(caller.play(question))
                wave = page.get_by_test_id("waveform")
                await expect(wave).to_have_class(re.compile("speaking"), timeout=8_000)
                log(t0, "waveform animating while the caller speaks")
                await page.screenshot(path=str(shots / "p3-2-in-call.png"))
                await play

                # Mute and hold flip the local mic only.
                mute = page.get_by_role("button", name="Mute")
                await mute.click()
                await expect(mute).to_have_attribute("aria-pressed", "true")
                await mute.click()
                hold = page.get_by_role("button", name="Hold")
                await hold.click()
                await expect(hold).to_have_attribute("aria-pressed", "true")
                await hold.click()
                assert (await agent_state(http)) == "on_call"

                # ---- End: wrap up, leave the room; the bot says goodbye and leaves ----
                end_at = time.time()
                await page.get_by_role("button", name="End call").click()
                wrap = page.get_by_role("region", name="Wrap up")
                await expect(wrap).to_be_visible(timeout=10_000)
                assert (await agent_state(http)) == "wrap_up"
                # REST presence lags; the caller's own client sees the leave at once.
                gone = await caller.wait_for_participant_left(AGENT_NAME, timeout=15)
                timings["end_to_agent_left_secs"] = gone.left_at - end_at
                log(t0, f"agent left the room {timings['end_to_agent_left_secs']:.2f}s after End")
                await browser.close()

            # P3-5: after the agent's End the bot says goodbye and exits; the room closes.
            await asyncio.wait_for(proc.wait(), 30)
            log(t0, f"bot exited after End, returncode={proc.returncode}")
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
    print(f"SCREENSHOTS {shots}")
