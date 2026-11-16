"""Live P5-8 (AP-22): Flux voice controls on the Settings page, then a call that uses them.

    cd portal && npm run build && cd ..
    PLAYWRIGHT_BROWSERS_PATH=... P5_SCREENSHOT_DIR=docs/screenshots/flux-controls \\
      uv run --with playwright pytest -m live server/tests/test_flux_controls_live.py -s -v

Needs DAILY_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL (a throwaway
schema is migrated, seeded, and dropped; settings persist in it, SETTINGS_PERSIST=1).

1. Portal (headless Chromium): sign in, open Settings, screenshot the Voice section with
   Aura-2 (controls disabled, light), then pick Flux, speed 1.1x, expressiveness Warm (1),
   slow read backs on; screenshot the Voice section light and dark; Preview (real
   /v2/speak, the read back part at 0.9x); Save.
2. A browser call (POST /calls/browser) with a daily-python caller answering intake as
   Maria Alvarez, until the call is queued. The bot's log is kept in a file.
3. Checks from the bot log: the [settings] line has speed=1.1 expressivity=1
   slow_readback=0.9; Flux connected once (no reconnect for a speed change); a
   `[timing] event=tts_speed speed=0.9 reason=readback` line comes before the identity
   read back and `speed=1.1 reason=restore` right after it; Flux applied each Configure.
   The audio of the read back is measured (words per second) against the next line.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import socket
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest
import uvicorn
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
from harness.turns import audio_slice, transcribe, wait_bot_turn

pytestmark = pytest.mark.live

REQUIRED = ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL")
VIEWPORT = {"width": 1600, "height": 1000}
AGENT_NAME = "Agent Ana"
MAX_TURNS = 14
FLUX_VOICE = "flux-alexis-en"
FLUX_QUIET_SECS = 2.0


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


def logged_bot_command(log_path: Path):
    def command(req):
        return ["bash", "-c", f'exec "$0" "$@" >>"{log_path}" 2>&1', *build_bot_command(req)]

    return command


async def voice_section_shot(page, region, path: Path) -> None:
    await page.wait_for_timeout(400)
    await region.locator(".settings-group").first.screenshot(path=str(path), animations="disabled")


async def set_theme(page, name: str) -> None:
    await page.get_by_role("button", name="Agent menu").click()
    await page.get_by_role("menuitemradio", name=name).click()
    await page.keyboard.press("Escape")
    await page.wait_for_function(f"document.documentElement.dataset.theme === '{name.lower()}'")


def words_per_sec(text: str, secs: float) -> float:
    return len(text.split()) / secs if secs > 0 else 0.0


async def test_flux_controls_then_a_call_slows_the_read_back(schema, monkeypatch, tmp_path):
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
    bot_log = Path(os.getenv("P5_BOT_LOG") or tmp_path / "bot.log")
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

            model = region.get_by_role("radiogroup", name="Model", exact=True)
            speed = region.get_by_role("slider", name="Speed")
            await model.get_by_role("radio", name="Aura-2").click()
            await expect(speed).to_be_disabled()
            await voice_section_shot(page, region, shots / "voice-aura2-disabled-light.png")
            log(t0, "screenshot voice-aura2-disabled-light.png (controls disabled)")

            await model.get_by_role("radio", name="Flux").click()
            await region.get_by_label("Voice", exact=True).select_option(FLUX_VOICE)
            await speed.fill("1.1")
            await expect(region.get_by_text("1.1x", exact=True)).to_be_visible()
            expressiveness = region.get_by_role("radiogroup", name="Expressiveness")
            await expressiveness.get_by_role("radio", name="Warm").click()
            slow = region.get_by_role("switch", name="Slow down for read backs")
            await expect(slow).to_have_attribute("aria-checked", "true")
            await voice_section_shot(page, region, shots / "voice-flux-light.png")
            log(t0, "screenshot voice-flux-light.png (1.1x, Warm, slow read backs on)")
            await set_theme(page, "Dark")
            await voice_section_shot(page, region, shots / "voice-flux-dark.png")
            log(t0, "screenshot voice-flux-dark.png")

            async with page.expect_response("**/settings/preview") as resp:
                await region.get_by_role("button", name="Preview", exact=True).click()
            r = await resp.value
            body = r.request.post_data_json
            assert r.status == 200 and r.headers["content-type"] == "audio/mpeg"
            assert body["tts_speed"] == 1.1 and body["tts_expressivity"] == 1
            assert body["slow_readback"] is True and body["readback_speed"] == 0.9
            log(t0, f"preview: {len(await r.body())} bytes of MP3, sent {body}")

            await region.get_by_role("button", name="Save", exact=True).click()
            await expect(region.get_by_role("status")).to_contain_text("Applies to the next call")
            await set_theme(page, "Light")
            await browser.close()

        async with httpx.AsyncClient(base_url=base, timeout=30) as http:
            saved = (await http.get("/settings")).json()["values"]
            assert (saved["tts_model"], saved["tts_speed"], saved["tts_expressivity"]) == (
                "flux",
                1.1,
                1,
            )
            assert saved["slow_readback"] is True and saved["readback_speed"] == 0.9
            log(t0, "saved: flux 1.1x expressivity 1, slow read backs at 0.9x")

            # ---- 2. a call ----------------------------------------------------------
            r = await http.post("/calls/browser")
            assert r.status_code == 200, r.text
            out = r.json()
            session_id = out["session_id"]
            room_name = out["room_url"].rsplit("/", 1)[-1]
            turns: list[tuple[float, float]] = []
            async with HarnessParticipant("caller") as caller:
                await caller.join(out["room_url"], out["caller_token"])
                bot = await caller.wait_for_participant(BOT_NAME, timeout=120)
                rec = caller.recording_for(BOT_NAME)
                after = max(caller.joined_at, bot.joined_at)
                queued = None
                for _ in range(MAX_TURNS):
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
                    turns.append(turn)
                    if queued := await queued_call(http, session_id):
                        break
                    key = next_line(*await intake_row(store, session_id))
                    _, after = await asyncio.wait_for(caller.play(audio[key]), 60)
                assert queued is not None, "intake never enqueued the call"
                log(t0, f"queued; identity={queued['identity_result']}")

                heard = []
                for start, end in turns:
                    text = await transcribe(audio_slice(rec, start - 0.1, end))
                    heard.append((start, end, text))
                await caller.leave()

        # ---- 3. what the bot did -----------------------------------------------------
        text = bot_log.read_text(errors="replace")
        lines = text.splitlines()
        settings_line = next((ln for ln in lines if "[settings]" in ln and session_id in ln), "")
        log(t0, f"bot: {settings_line.split('[settings]')[-1].strip()}")
        speed_lines = [ln for ln in lines if "event=tts_speed" in ln and session_id in ln]
        print("tts_speed lines:")
        for ln in speed_lines:
            print("   ", ln.split("| ")[-1])
        configure = [ln for ln in lines if "configuration applied" in ln]
        print("Flux ConfigureSuccess:")
        for ln in configure:
            print("   ", ln.split("| ")[-1][-120:])
        connects = [ln for ln in lines if "Connecting to Deepgram Flux WebSocket" in ln]
        log(t0, f"Flux TTS websocket connects: {len(connects)}")

        speeds = []
        for ln in speed_lines:
            p = parse_timing(ln)
            assert p is not None
            speeds.append((p[1], float(p[2]["speed"]), p[2]["reason"]))
        print("bot turns (heard):")
        rate = {}
        for start, end, said in heard:
            wps = words_per_sec(said, end - start)
            print(f"    {start:.2f}-{end:.2f} ({end - start:.1f}s, {wps:.2f} words/s): {said!r}")
            rate[start] = (said, wps)

        assert "speed=1.1 expressivity=1 slow_readback=0.9" in settings_line, settings_line
        assert len(connects) == 1, "a speed change must not reconnect"
        assert [(s, r) for _, s, r in speeds][:2] == [(0.9, "readback"), (1.1, "restore")]
        assert len(configure) >= 2
        readback = next((h for h in heard if "member id" in h[2].lower()), None)
        assert readback is not None, heard
        nxt = next((h for h in heard if h[0] > readback[0]), None)
        slow_at, restore_at = speeds[0][0], speeds[1][0]
        # The slow Configure is sent before the read back is heard, the restore after it
        # was synthesized and before the next line is heard.
        assert slow_at < readback[0] and restore_at < (nxt[0] if nxt else float("inf"))
        rb_wps = words_per_sec(readback[2], readback[1] - readback[0])
        log(t0, f"read back {rb_wps:.2f} words/s at 0.9x")
        if nxt:
            log(t0, f"next line {words_per_sec(nxt[2], nxt[1] - nxt[0]):.2f} words/s at 1.1x")
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
