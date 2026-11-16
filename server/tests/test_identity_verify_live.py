"""Live IN-13: a Linda Nguyen call heard as "Linda Yuen", through handoff, in the portal.

Run locally with keys, a Postgres, a built portal (npm run build in portal/), and
Playwright's Chromium:
    PLAYWRIGHT_BROWSERS_PATH=... uv run --with playwright \\
        pytest -m live server/tests/test_identity_verify_live.py -s -v

Needs DAILY_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL (a throwaway
schema is created and dropped). IDENTITY_SCREENSHOT_DIR (optional) keeps the 1600x1000
screenshots (docs/screenshots/identity-v2/).

1. A daily-python caller finishes intake as Linda Nguyen but gives her name as "Linda
   Yuen" (member ID and DOB exact): intake ends close_match (IN-6).
2. Agent Ana accepts. The first hero is the verify hero (IN-13): member ID and DOB
   match, the name heard vs on file. The caller asks about her denied claim: the
   record shows as a locked tile, PHI Reveal is off. Screenshots light and dark.
3. Mark verified: the hero acks, the locked tile is replaced by the real record, the
   caller panel shows Linda Nguyen with "Verified by agent", and Reveal works.
4. The decision is on the call record: calls.identity_decision and an assist_events
   identity_decision row.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import time
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
from harness.intake_script import Persona, next_line, persona
from harness.participant import SAMPLE_RATE, HarnessParticipant
from harness.tts import synthesize
from harness.turns import wait_bot_turn

# Same portal setup as the P4-4 live test (fake mic, theme menu, throwaway schema).
_spec = importlib.util.spec_from_file_location(
    "assist_ui_live", Path(__file__).with_name("test_assist_ui_live.py")
)
assert _spec and _spec.loader
_ui = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ui)
AGENT_NAME = _ui.AGENT_NAME
CHROMIUM_ARGS = _ui.CHROMIUM_ARGS
FAKE_MIC = _ui.FAKE_MIC
MAX_TURNS = _ui.MAX_TURNS
VIEWPORT = _ui.VIEWPORT
free_port, intake_row, log = _ui.free_port, _ui.intake_row, _ui.log
queued_call, set_theme, snap = _ui.queued_call, _ui.set_theme, _ui.snap
keys_present, schema = _ui.keys_present, _ui.schema

pytestmark = pytest.mark.live

LINDA = persona("linda_nguyen")
# The caller says "Yuen" every time (also on a correction), the way STT heard her in the
# identity spike, so intake ends close_match whatever the one fix does.
YUEN = Persona(
    "linda_yuen",
    "Linda Yuen",
    LINDA.dob,
    LINDA.member_id,
    dict(
        LINDA.lines,
        identity=(
            "Sure. My name is Linda Yuen. My date of birth is January thirtieth, nineteen "
            "fifty eight. And my member ID is seven four one, five two six."
        ),
        fix_name="No, my name is Linda Yuen.",
        fix_both=("No. My name is Linda Yuen, and my member ID is seven four one, five two six."),
    ),
)
CALLER_TRIGGER = "So why was my dermatology claim denied?"


@pytest.fixture
def shots(tmp_path) -> Path:
    d = Path(os.getenv("IDENTITY_SCREENSHOT_DIR") or tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    return d


async def run_intake(http, store, caller, rec, session_id, audio, after) -> dict | None:
    for _ in range(MAX_TURNS):
        turn = await wait_bot_turn(rec, after)
        if turn is None:
            break
        if q := await queued_call(http, session_id):
            return q
        key = next_line(*await intake_row(store, session_id), YUEN)
        _, after = await asyncio.wait_for(caller.play(audio[key]), 60)
    end = time.time() + 10
    while time.time() < end:
        if q := await queued_call(http, session_id):
            return q
        await asyncio.sleep(0.2)
    return None


async def test_verify_hero_then_records_unlock(schema, shots, monkeypatch):  # noqa: F811
    from playwright.async_api import async_playwright, expect

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setenv(SERVER_URL_ENV, base)
    monkeypatch.setenv(DATABASE_SCHEMA_ENV, schema)
    monkeypatch.setenv("QUEUE_PERSIST", "1")
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    audio = {k: await synthesize(v, sample_rate=SAMPLE_RATE) for k, v in YUEN.lines.items()}
    trigger_pcm = await synthesize(CALLER_TRIGGER, sample_rate=SAMPLE_RATE)
    launcher = BotLauncher()
    app.state.bot_launcher = launcher
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serve_task = asyncio.create_task(server.serve())
    daily = DailyRestClient.from_env()
    store = PostgresCallStore(os.environ["DATABASE_URL"], schema=schema)
    room_name = None
    t0 = time.time()
    taken: list[str] = []
    try:
        while not server.started:
            await asyncio.sleep(0.05)
        async with httpx.AsyncClient(base_url=base, timeout=30) as http, async_playwright() as pw:
            browser = await pw.chromium.launch(args=CHROMIUM_ARGS)
            ctx = await browser.new_context(
                viewport=VIEWPORT, permissions=["microphone"], color_scheme="light"
            )
            await ctx.add_init_script(FAKE_MIC)
            page = await ctx.new_page()
            page.on("console", lambda m: m.type == "error" and print(f"  [portal] {m.text}"))
            await page.goto(f"{base}/agent/")
            await page.get_by_label("Your name").fill(AGENT_NAME)
            await page.get_by_role("button", name="Sign in").click()
            toggle = page.get_by_role("switch", name="Available")
            await expect(toggle).to_be_enabled(timeout=10_000)

            r = await http.post("/calls/browser")
            assert r.status_code == 200, r.text
            out = r.json()
            session_id = out["session_id"]
            room_name = out["room_url"].rsplit("/", 1)[-1]
            proc = launcher.processes[session_id]

            async with HarnessParticipant("caller") as caller:
                await caller.join(out["room_url"], out["caller_token"])
                bot = await caller.wait_for_participant(BOT_NAME, timeout=120)
                rec = caller.recording_for(BOT_NAME)
                t_start = max(caller.joined_at, bot.joined_at)
                queued = await run_intake(http, store, caller, rec, session_id, audio, t_start)
                assert queued is not None, "intake never enqueued the call"
                intake, _ = await intake_row(store, session_id)
                heard = intake.get("full_name")
                log(t0, f"enqueued: identity={queued['identity_result']} heard={heard!r}")
                # "Yuen" gives close_match; STT sometimes hears "Yuan" (4 edits from
                # Nguyen: not_verified). Both get the verify hero with the ID match.
                assert queued["identity_result"] in ("close_match", "not_verified"), queued

                # ---- 1. accept: the verify hero comes first ---------------------------
                await toggle.click()
                await expect(page.get_by_role("dialog", name="Incoming call")).to_be_visible(
                    timeout=5_000
                )
                accept_at = time.time()
                await page.keyboard.press("Enter")
                await expect(page.get_by_role("button", name="Mute")).to_be_enabled(timeout=30_000)
                hero = page.get_by_test_id("hero")
                await expect(hero).to_have_attribute("data-state", "verify", timeout=10_000)
                log(t0, f"verify hero {time.time() - accept_at:.2f}s after Enter")
                await expect(hero).to_contain_text("Confirm who you're speaking with")
                checks = hero.get_by_role("list", name="Identity checks")
                await expect(checks).to_contain_text("Member ID")
                await expect(checks).to_contain_text("On file “Linda Nguyen”")
                await expect(checks).not_to_contain_text("1958")
                panel = page.get_by_role("complementary", name="Caller")
                await expect(panel.get_by_role("button", name="Reveal member ID")).to_be_disabled()
                await caller.wait_for_participant(AGENT_NAME, timeout=15)
                await wait_bot_turn(rec, accept_at, start_timeout=10)

                # ---- 2. the caller's question: records arrive locked ------------------
                said_at = time.time()
                await asyncio.wait_for(caller.play(trigger_pcm), 60)
                locked = page.locator(".shelf [data-locked='true']")
                await expect(locked.first).to_be_visible(timeout=15_000)
                log(t0, f"locked tile {time.time() - said_at:.2f}s after the question")
                await expect(locked.first).to_contain_text("Verify the caller to see records")
                await expect(page.locator(".shelf")).not_to_contain_text("52976")
                await expect(hero).to_have_attribute("data-state", "verify")
                await snap(page, shots / "in-call-verify-live-light.png", t0)
                await set_theme(page, "dark")
                # Esc closed the menu; the verify hero cannot be dismissed.
                await expect(hero).to_have_attribute("data-state", "verify")
                await snap(page, shots / "in-call-verify-live-dark.png", t0)
                await set_theme(page, "light")
                taken += ["in-call-verify-live-light.png", "in-call-verify-live-dark.png"]

                # ---- 3. Mark verified -------------------------------------------------
                await hero.get_by_role("button", name="Mark verified").click()
                await expect(hero).to_have_attribute("data-state", "verified", timeout=2_000)
                await expect(locked).to_have_count(0, timeout=5_000)
                await expect(page.locator(".shelf")).to_contain_text("52976", timeout=5_000)
                await expect(panel.get_by_test_id("identity-badge")).to_have_text(
                    "Verified by agent"
                )
                await expect(panel.get_by_role("heading", name="Linda Nguyen")).to_be_visible(
                    timeout=10_000
                )
                reveal = panel.get_by_role("button", name="Reveal member ID")
                await expect(reveal).to_be_enabled()
                hist = page.get_by_role("list", name="Earlier this call")
                await expect(hist).to_contain_text("Caller verified")
                await snap(page, shots / "in-call-verified-live-light.png", t0)
                await set_theme(page, "dark")
                await snap(page, shots / "in-call-verified-live-dark.png", t0)
                taken += ["in-call-verified-live-light.png", "in-call-verified-live-dark.png"]
                await reveal.click()
                await expect(panel.get_by_test_id("phi-member_id")).to_have_text("741526")

                await page.get_by_role("button", name="End call").click()
                await expect(page.get_by_role("region", name="Wrap up")).to_be_visible(
                    timeout=15_000
                )
                await caller.wait_for_participant_left(AGENT_NAME, timeout=15)
                await browser.close()
            await asyncio.wait_for(proc.wait(), 30)

            cur = await store._execute(
                "SELECT identity_result, identity_decision, phi_reveals FROM calls "
                "WHERE session_id = %s",
                (session_id,),
            )
            identity_result, decision, reveals = await cur.fetchone()
            cur = await store._execute(
                "SELECT e.kind, e.card_id, e.data FROM assist_events e "
                "JOIN calls c USING (call_id) WHERE c.session_id = %s ORDER BY e.event_id",
                (session_id,),
            )
            events = await cur.fetchall()
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
        for attr in ("bot_launcher", "queue_service"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)

    print(f"calls: identity_result={identity_result} identity_decision={decision}")
    print(f"phi_reveals: {reveals}")
    decisions = [(c, d) for k, c, d in events if k == "identity_decision"]
    print(f"assist_events identity_decision: {decisions}")
    assert identity_result in ("close_match", "not_verified")
    assert decision["decision"] == "verified" and decision["agent_name"] == AGENT_NAME
    assert [r["field"] for r in reveals] == ["member_id"]
    assert len(decisions) == 1
    card_id, data = decisions[0]
    assert card_id == "verify:identity"
    assert data["decision"] == "verified" and data["by"] == AGENT_NAME
    assert data["intake_result"] == identity_result
    print(f"SCREENSHOTS {shots}: {taken}")
