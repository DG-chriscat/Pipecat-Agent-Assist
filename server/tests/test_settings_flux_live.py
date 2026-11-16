"""Live P5-5: the Settings page picks Flux, and the next call speaks with it.

    cd portal && npm run build && cd ..
    PLAYWRIGHT_BROWSERS_PATH=... P5_SCREENSHOT_DIR=docs/screenshots/p5-5 \\
      uv run --with playwright pytest -m live server/tests/test_settings_flux_live.py -s -v

Needs DAILY_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL (a throwaway
schema is migrated, seeded, and dropped; settings persist in it, SETTINGS_PERSIST=1).

1. Portal (headless Chromium, 1600x1000): sign in, open Settings from the rail.
   Screenshots light and dark. Pick Flux and a Flux voice, a custom greeting, a custom
   hold message every 8 s; a greeting without the AI disclosure is refused first
   (screenshot). Save.
2. A browser call (POST /calls/browser), a daily-python caller answering intake as
   Maria Alvarez. The bot's own log (`[settings]`, `[timing]`) is kept in a file.
3. Checks: the bot built Flux with the saved voice; the greeting heard is the saved
   one; intake reaches the queue; the saved hold message plays over the music; a
   websocket agent accepts and "Connecting you now." is heard. Timings printed.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import socket
import sys
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
from agent_assist_bot.timing import parse_timing
from agent_assist_server.app import app
from agent_assist_server.bot_launcher import BotLauncher, build_bot_command
from agent_assist_server.daily import DailyRestClient
from harness.intake_script import MARIA_ALVAREZ as SCRIPT
from harness.intake_script import next_line
from harness.participant import SAMPLE_RATE, HarnessParticipant
from harness.tts import synthesize
from harness.turns import SPEECH_RMS, audio_slice, transcribe, wait_bot_turn

pytestmark = pytest.mark.live

REQUIRED = ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL")
VIEWPORT = {"width": 1600, "height": 1000}
AGENT_NAME = "Agent Ana"
MAX_TURNS = 14
FLUX_VOICE = os.environ.get("P5_FLUX_VOICE", "flux-alexis-en")
FLUX_QUIET_SECS = 2.0
GREETING = (
    "Hi, thanks for calling Deepgram Vitals. You're speaking with an AI assistant. "
    "I'll ask a few quick questions, and then a person will join the call to help you."
)
HOLD_TEXT = "Thanks for holding. {place} A person will be with you soon."
HOLD_SECS = 8
WAIT_SECS = 14.0


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


async def queued_call(http, session_id):
    try:
        resp = await http.get("/queue")
    except httpx.TransportError:
        # uvicorn closes idle keep-alive connections after 5 s; the caller's turns can
        # leave the client idle that long, so one stale connection is expected.
        resp = await http.get("/queue")
    for c in resp.json()["queue"]:
        if c["session_id"] == session_id:
            return c
    return None


async def intake_row(store, session_id):
    cur = await store._execute(
        "SELECT intake, identity_result FROM calls WHERE session_id = %s", (session_id,)
    )
    row = await cur.fetchone()
    return (row[0], row[1]) if row else (None, None)


async def recv_type(ws, kind: str, timeout: float) -> dict:
    async def _wait():
        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] == kind:
                return msg

    return await asyncio.wait_for(_wait(), timeout)


def logged_bot_command(log_path: Path):
    """The real bot command, with its stdout and stderr appended to log_path."""

    def command(req):
        return ["bash", "-c", f'exec "$0" "$@" >>"{log_path}" 2>&1', *build_bot_command(req)]

    return command


async def test_settings_page_flux_then_a_call_uses_it(schema, monkeypatch, tmp_path):
    from playwright.async_api import async_playwright, expect

    shots = Path(os.getenv("P5_SCREENSHOT_DIR") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setenv(SERVER_URL_ENV, base)
    monkeypatch.setenv(DATABASE_SCHEMA_ENV, schema)
    monkeypatch.setenv("SETTINGS_PERSIST", "1")
    for k in ("APP_PASSWORD", "QUEUE_PERSIST", "HOLD_UPDATE_SECS", "INTAKE_PACK"):
        monkeypatch.delenv(k, raising=False)
    bot_log = tmp_path / "bot.log"
    launcher = BotLauncher(command_for=logged_bot_command(bot_log))
    app.state.bot_launcher = launcher
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serve_task = asyncio.create_task(server.serve())
    daily = DailyRestClient.from_env()
    store = PostgresCallStore(os.environ["DATABASE_URL"], schema=schema)
    audio = {k: await synthesize(v, sample_rate=SAMPLE_RATE) for k, v in SCRIPT.items()}
    room_name = None
    t0 = time.time()
    try:
        while not server.started:
            await asyncio.sleep(0.05)

        # ---- 1. the Settings page -------------------------------------------------
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            ctx = await browser.new_context(viewport=VIEWPORT, color_scheme="light")
            page = await ctx.new_page()
            page.on("console", lambda m: m.type == "error" and print(f"  [portal] {m.text}"))
            await page.goto(f"{base}/agent/")
            await page.get_by_label("Your name").fill(AGENT_NAME)
            await page.get_by_role("button", name="Sign in").click()
            await expect(page.get_by_role("switch", name="Available")).to_be_enabled(timeout=10_000)
            await page.get_by_role("button", name="Settings").click()
            region = page.get_by_role("region", name="Settings")
            await expect(region.get_by_label("Voice", exact=True)).to_be_visible(timeout=10_000)
            await page.wait_for_timeout(500)
            await page.screenshot(path=str(shots / "settings-light.png"), animations="disabled")
            log(t0, "screenshot settings-light.png (defaults)")

            # Refused: a greeting without the disclosure (client check, Save disabled).
            greeting = region.get_by_label("Greeting", exact=True)
            await greeting.fill("Hi, thanks for calling. How can I help?")
            await expect(region.get_by_role("button", name="Save", exact=True)).to_be_disabled()

            await page.get_by_role("button", name="Agent menu").click()
            await page.get_by_role("menuitemradio", name="Dark").click()
            await page.keyboard.press("Escape")
            await page.wait_for_function("document.documentElement.dataset.theme === 'dark'")
            await page.wait_for_timeout(400)
            await page.screenshot(
                path=str(shots / "settings-dark-disclosure-check.png"), animations="disabled"
            )
            log(t0, "screenshot settings-dark-disclosure-check.png")

            await (
                region.get_by_role("radiogroup", name="Model", exact=True)
                .get_by_role("radio", name="Flux")
                .click()
            )
            await region.get_by_label("Voice", exact=True).select_option(FLUX_VOICE)
            await greeting.fill(GREETING)
            await region.get_by_label("Hold message").fill(HOLD_TEXT)
            await region.get_by_label("Every").fill(str(HOLD_SECS))
            await region.get_by_role("button", name="Save", exact=True).click()
            await expect(region.get_by_role("status")).to_contain_text("Applies to the next call")
            await page.wait_for_timeout(300)
            await page.screenshot(path=str(shots / "settings-dark-flux.png"), animations="disabled")
            log(t0, "screenshot settings-dark-flux.png (saved)")

            await page.get_by_role("button", name="Agent menu").click()
            await page.get_by_role("menuitemradio", name="Light").click()
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(400)
            await page.screenshot(
                path=str(shots / "settings-light-flux.png"), animations="disabled"
            )
            log(t0, "screenshot settings-light-flux.png (saved)")

            # Preview through the server (real Deepgram /v2/speak).
            async with page.expect_response("**/settings/preview") as resp:
                await region.get_by_role("button", name="Preview").click()
            r = await resp.value
            assert r.status == 200 and r.headers["content-type"] == "audio/mpeg"
            log(t0, f"preview: {len(await r.body())} bytes of MP3 from {FLUX_VOICE}")
            await browser.close()

        async with httpx.AsyncClient(base_url=base, timeout=30) as http:
            saved = (await http.get("/settings")).json()
            assert saved["values"]["tts_model"] == "flux"
            assert saved["values"]["tts_voice"] == FLUX_VOICE
            from agent_assist_server.db import connect

            async with await connect(schema=schema) as c:
                cur = await c.execute("SELECT kind, changes FROM settings_events")
                events = await cur.fetchall()
            assert events and events[-1][0] == "save"
            log(t0, f"settings_events: {[(k, sorted(ch)) for k, ch in events]}")

            # ---- 2. a call ----------------------------------------------------------
            t_req = time.time()
            r = await http.post("/calls/browser")
            assert r.status_code == 200, r.text
            out = r.json()
            session_id = out["session_id"]
            room_name = out["room_url"].rsplit("/", 1)[-1]
            async with HarnessParticipant("caller") as caller:
                await caller.join(out["room_url"], out["caller_token"])
                bot = await caller.wait_for_participant(BOT_NAME, timeout=120)
                rec = caller.recording_for(BOT_NAME)
                t_start = max(caller.joined_at, bot.joined_at)
                after = t_start
                queued = None
                first = None
                for _ in range(MAX_TURNS):
                    # Poll the queue while waiting: once queued, hold music follows the
                    # handoff line, so that turn never goes quiet.
                    # Flux leaves ~1.1 s between sentences (docs/spikes/tts-flux-vs-aura.md),
                    # so wait longer than the harness default before answering.
                    task = asyncio.create_task(
                        wait_bot_turn(rec, after, start_timeout=30, quiet=FLUX_QUIET_SECS)
                    )
                    while not task.done() and queued is None:
                        queued = await queued_call(http, session_id)
                        await asyncio.sleep(0.2)
                    if queued is not None:
                        task.cancel()
                        break
                    turn = task.result()
                    if turn is None:
                        break
                    if first is None:
                        first = turn
                    if queued := await queued_call(http, session_id):
                        break
                    key = next_line(*await intake_row(store, session_id))
                    _, after = await asyncio.wait_for(caller.play(audio[key]), 60)
                assert first is not None, "no greeting"
                heard_greeting = await transcribe(audio_slice(rec, first[0] - 0.1, first[1]))
                log(
                    t0,
                    f"first bot turn {first[0] - t_start:.2f}s after both joined: "
                    f"{heard_greeting!r}",
                )
                end = time.time() + 10
                while queued is None and time.time() < end:
                    queued = await queued_call(http, session_id)
                    await asyncio.sleep(0.2)
                assert queued is not None, "intake never enqueued the call"
                queued_at = queued["queued_at"]
                log(
                    t0,
                    f"queued {queued_at - t_start:.1f}s after join; "
                    f"identity={queued['identity_result']}",
                )

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
                    await asyncio.sleep(6)

                heard_hold = await transcribe(
                    audio_slice(rec, queued_at + HOLD_SECS - 1, queued_at + HOLD_SECS + 6)
                )
                log(t0, f"hold update ({HOLD_SECS}s after queued): {heard_hold!r}")
                # The music stops on accept, then "Connecting you now." (QH-6).
                heard_connect = await transcribe(audio_slice(rec, accept_at, accept_at + 6))
                line = rec.first_speech_after(accept_at + 0.6, threshold=SPEECH_RMS)
                log(
                    t0,
                    f"after accept: {heard_connect!r}"
                    + (f" (speech from +{line - accept_at:.2f}s)" if line else ""),
                )
                await caller.leave()

        # ---- 3. what the bot did -----------------------------------------------------
        text = bot_log.read_text(errors="replace")
        settings_line = next(
            (ln for ln in text.splitlines() if "[settings]" in ln and session_id in ln), ""
        )
        log(t0, f"bot: {settings_line.split('[settings]')[-1].strip()}")
        timing = {}
        for ln in text.splitlines():
            p = parse_timing(ln)
            if p and p[2].get("session") == session_id:
                timing.setdefault(p[0], p[1])
        rel = {k: round(v - t_req, 3) for k, v in sorted(timing.items(), key=lambda kv: kv[1])}
        log(t0, f"bot timeline (s after POST /calls/browser): {rel}")
        if "bot_joined" in timing and "greeting_first_audio" in timing:
            log(
                t0,
                "bot_joined -> greeting_first_audio: "
                f"{timing['greeting_first_audio'] - timing['bot_joined']:.3f}s",
            )
        flux_lines = [ln for ln in text.splitlines() if "DeepgramFluxTTSService" in ln][:3]
        for ln in flux_lines:
            print("   ", ln[-160:])

        assert f"tts=flux/{FLUX_VOICE}" in settings_line, settings_line
        assert "hold_every=8s" in settings_line
        assert any("DeepgramFluxTTSService" in ln for ln in text.splitlines())
        low = heard_greeting.lower()
        assert "ai assistant" in low and "person will join" in low, heard_greeting
        assert "holding" in heard_hold.lower() and "line" in heard_hold.lower(), heard_hold
        assert "connecting you" in heard_connect.lower(), heard_connect
        assert re.search(r"V2_MODEL_ON_V1|error", settings_line, re.I) is None
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
        for attr in (
            "bot_launcher",
            "queue_service",
            "bot_channels",
            "settings_store",
            "call_settings",
            "voice_catalog",
        ):
            if hasattr(app.state, attr):
                delattr(app.state, attr)
        print(f"bot log kept at {bot_log}", file=sys.stderr)
