"""Live P4-4: the portal's assist column on a real Maria Alvarez call.

Run locally with keys, a Postgres, a built portal (npm run build in portal/), and
Playwright's Chromium (Pillow for the side by side sheets):
    PLAYWRIGHT_BROWSERS_PATH=... uv run --with playwright --with pillow \\
        pytest -m live server/tests/test_assist_ui_live.py -s -v

Needs DAILY_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL (a throwaway
schema is created and dropped). P4_SCREENSHOT_DIR (optional) keeps the 1600x1000
screenshots and side by side sheets with docs/design/in-call-focus.png and
docs/design/studio/in-call-studio-*.png.

Same call as test_assist_live.py, but the agent is the real portal in headless
Chromium (the P3-2/P4-0 setup):
1. A daily-python caller finishes intake as Maria Alvarez; Agent Ana's portal accepts
   with Enter.
2. The agent says a call plan line through the portal mic; the caller says the
   trigger ("So why did my MRI claim get denied?").
3. The hero shows the retro authorization review with its trigger, Say this, action,
   and source; the trigger word is marked in the conversation; the call plan ticked
   "Confirm which claim". Screenshots in light and dark, then Show me why.
4. Enter accepts: the hero shows the accepted state, Earlier this call logs the
   simulated action, the plan ticks "Take the next step" (by the bot, DC-9), and
   assist_events has the accept row. The claim is not changed.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import os
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
from harness.intake_script import MARIA_ALVAREZ as SCRIPT
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
VOICES = {"caller": "aura-2-arcas-en", "agent": "aura-2-thalia-en"}
DESIGN = Path(__file__).resolve().parents[2] / "docs" / "design"
HERO_ID = "nbs:retro_auth_review:CLM-900001"
AGENT_LINE = "Okay Maria, I pulled up your account, and I can see the claim right here."
CALLER_TRIGGER = "So why did my MRI claim get denied?"
# Same as test_assist_live.py: spell the surname so intake STT verifies the caller.
INTAKE_LINES = dict(
    SCRIPT,
    identity=(
        "Sure. My name is Maria Alvarez, that's A L V A R E Z. My date of birth is March "
        "twelfth, nineteen eighty four. And my member ID is E V M, one zero zero, two three one."
    ),
)

FAKE_MIC = """
(() => {
  let ctx = null, dest = null;
  const ensure = () => {
    if (!ctx) { ctx = new AudioContext(); dest = ctx.createMediaStreamDestination(); }
    return dest;
  };
  const orig = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = async (c) => {
    if (c && c.audio && !c.video) {
      const d = ensure();
      if (ctx.state !== 'running') await ctx.resume();
      return new MediaStream([d.stream.getAudioTracks()[0].clone()]);
    }
    return orig(c);
  };
  window.__speak = (b64, rate) => new Promise((resolve) => {
    ensure();
    const raw = atob(b64);
    const n = raw.length / 2;
    const buf = ctx.createBuffer(1, n, rate);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < n; i++) {
      let v = raw.charCodeAt(2 * i) | (raw.charCodeAt(2 * i + 1) << 8);
      if (v >= 32768) v -= 65536;
      ch[i] = v / 32768;
    }
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(dest);
    src.onended = () => resolve(true);
    src.start();
  });
})();
"""


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
    d = Path(os.getenv("P4_SCREENSHOT_DIR") or tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    return d


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def log(t0: float, msg: str) -> None:
    print(f"[{time.time() - t0:7.2f}s] {msg}", flush=True)


async def intake_row(store: PostgresCallStore, session_id: str):
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


async def set_theme(page, theme: str) -> None:
    """The rail avatar menu; Esc closes the menu (and must not dismiss the hero)."""
    await page.get_by_role("button", name="Agent menu").click()
    await page.get_by_role("menuitemradio", name=theme.capitalize()).click()
    await page.keyboard.press("Escape")
    await page.wait_for_function(f"document.documentElement.dataset.theme === '{theme}'")
    await page.wait_for_timeout(400)


async def snap(page, path: Path, t0: float) -> None:
    await page.screenshot(path=str(path), animations="disabled")
    log(t0, f"screenshot {path.name}")


def side_by_side(ref: Path, shot: Path, out: Path, label: str) -> None:
    from PIL import Image, ImageDraw

    a = Image.open(ref).convert("RGB").resize((1600, 1000))
    b = Image.open(shot).convert("RGB").resize((1600, 1000))
    pad, head = 24, 56
    sheet = Image.new("RGB", (1600 * 2 + pad * 3, 1000 + head + pad), (244, 240, 234))
    sheet.paste(a, (pad, head))
    sheet.paste(b, (1600 + pad * 2, head))
    draw = ImageDraw.Draw(sheet)
    draw.text((pad, 18), f"{ref.relative_to(DESIGN.parents[1])} (reference)", fill=(29, 29, 29))
    draw.text((1600 + pad * 2, 18), f"live: {label}", fill=(29, 29, 29))
    sheet.save(out)


async def test_assist_column_on_a_live_call(schema, shots, monkeypatch):
    from playwright.async_api import async_playwright, expect

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setenv(SERVER_URL_ENV, base)
    monkeypatch.setenv(DATABASE_SCHEMA_ENV, schema)
    monkeypatch.delenv("QUEUE_PERSIST", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    audio = {k: await synthesize(v, sample_rate=SAMPLE_RATE) for k, v in INTAKE_LINES.items()}
    agent_pcm = await synthesize(AGENT_LINE, sample_rate=SAMPLE_RATE, voice=VOICES["agent"])
    trigger_pcm = await synthesize(CALLER_TRIGGER, sample_rate=SAMPLE_RATE, voice=VOICES["caller"])
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
            log(t0, "portal signed in")

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
                log(t0, f"enqueued: identity={queued['identity_result']}")
                assert queued["identity_result"] == "verified"

                # ---- 1. accept in the portal ------------------------------------------
                await toggle.click()
                await expect(page.get_by_role("dialog", name="Incoming call")).to_be_visible(
                    timeout=5_000
                )
                accept_at = time.time()
                await page.keyboard.press("Enter")
                await expect(page.get_by_role("button", name="Mute")).to_be_enabled(timeout=30_000)
                log(t0, f"accepted; joined {time.time() - accept_at:.2f}s after Enter")
                hero = page.get_by_test_id("hero")
                await expect(hero).to_have_attribute("data-state", "listening")
                await caller.wait_for_participant(AGENT_NAME, timeout=15)
                await wait_bot_turn(rec, accept_at, start_timeout=10)

                # ---- 2. agent line, then the caller's trigger -------------------------
                b64 = base64.b64encode(agent_pcm).decode()
                await page.evaluate("([b, r]) => window.__speak(b, r)", [b64, SAMPLE_RATE])
                log(t0, f"agent: {AGENT_LINE}")
                await asyncio.sleep(1.5)
                said_at = time.time()
                await asyncio.wait_for(caller.play(trigger_pcm), 60)
                log(t0, f"caller: {CALLER_TRIGGER}")

                # ---- 3. the hero on screen ---------------------------------------------
                await expect(hero).to_have_attribute("data-state", "suggestion", timeout=15_000)
                log(t0, f"hero on screen {time.time() - said_at:.2f}s after the trigger started")
                await expect(hero.get_by_role("heading")).to_contain_text(
                    "retro authorization review"
                )
                await expect(hero).to_contain_text("Heard ")
                await expect(hero).to_contain_text("Say this")
                await expect(hero).to_contain_text("AI suggested")
                accept_btn = hero.get_by_role("button", name="Request retro auth review")
                await expect(accept_btn).to_be_visible()
                source = await hero.get_by_test_id("hero-source").get_attribute("title")
                log(t0, f"hero source: {source}")
                assert source and "Claims system" in source
                convo = page.get_by_role("complementary", name="Conversation")
                await expect(convo.locator("mark").first).to_be_visible(timeout=5_000)
                marked = await convo.locator("mark").all_text_contents()
                log(t0, f"marked in the conversation: {marked}")
                plan = page.get_by_test_id("tile-plan")
                await expect(plan).to_be_visible()
                await page.wait_for_timeout(2500)  # supporting cards land
                tiles = await page.locator(".shelf [data-testid^='tile-']").evaluate_all(
                    "(els) => els.map((e) => e.dataset.testid)"
                )
                log(t0, f"shelf: {tiles}; plan {await plan.locator('.tile-count').inner_text()}")
                await snap(page, shots / "p4-4-1-hero-light.png", t0)
                await set_theme(page, "dark")
                # Esc closed the menu; it did not dismiss the hero.
                await expect(hero).to_have_attribute("data-state", "suggestion")
                await snap(page, shots / "p4-4-1-hero-dark.png", t0)
                await hero.get_by_role("button", name="Show me why").click()
                await expect(hero.get_by_test_id("hero-why-more")).to_be_visible()
                await page.wait_for_timeout(400)
                await snap(page, shots / "p4-4-2-show-me-why-dark.png", t0)
                await hero.get_by_role("button", name="Hide why").click()
                await set_theme(page, "light")
                taken += [
                    "p4-4-1-hero-light.png",
                    "p4-4-1-hero-dark.png",
                    "p4-4-2-show-me-why-dark.png",
                ]

                # ---- 4. Enter accepts ---------------------------------------------------
                # Enter on a focused button presses that button, so nothing is focused.
                await page.evaluate("document.activeElement && document.activeElement.blur()")
                await page.keyboard.press("Enter")
                await expect(hero).to_have_attribute("data-state", "accepted", timeout=2_000)
                await page.wait_for_timeout(300)
                await snap(page, shots / "p4-4-3-accepted-light.png", t0)
                hist = page.get_by_role("list", name="Earlier this call")
                await expect(hist).to_contain_text("Request retro auth review")
                await expect(hist).to_contain_text("Simulated")
                await expect(plan.locator("li.is-done")).to_contain_text(
                    ["Take the next step"], timeout=10_000
                )
                await expect(hero).not_to_have_attribute("data-state", "accepted", timeout=5_000)
                after_state = await hero.get_attribute("data-state")
                log(t0, f"hero after accept: {after_state}")
                rows = await hist.locator("li").all_inner_texts()
                log(t0, f"earlier this call: {[r.replace(chr(10), ' | ') for r in rows]}")
                await snap(page, shots / "p4-4-4-after-accept-light.png", t0)
                await set_theme(page, "dark")
                await snap(page, shots / "p4-4-4-after-accept-dark.png", t0)
                taken += [
                    "p4-4-3-accepted-light.png",
                    "p4-4-4-after-accept-light.png",
                    "p4-4-4-after-accept-dark.png",
                ]

                await page.get_by_role("button", name="End call").click()
                await expect(page.get_by_role("region", name="Wrap up")).to_be_visible(
                    timeout=15_000
                )
                await caller.wait_for_participant_left(AGENT_NAME, timeout=15)
                await browser.close()
            await asyncio.wait_for(proc.wait(), 30)

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
        for attr in ("bot_launcher", "queue_service"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)

    print("assist_events rows:")
    for kind, card_id, data in events:
        print(f"  {kind:10} {card_id or '-':45} {data}")
    accepts = [(c, d) for k, c, d in events if k == "accept"]
    assert accepts == [
        (
            HERO_ID,
            {
                "kind": "next_step",
                "pack_id": "claims_status",
                "action_id": "request_retro_auth_review",
                "simulated": True,
            },
        )
    ], accepts
    assert claim_status == "denied"  # DC-9: nothing written back

    try:
        studio = DESIGN / "studio"
        pairs = [
            (DESIGN / "in-call-focus.png", "p4-4-1-hero-light.png", "focus"),
            (studio / "in-call-studio-light.png", "p4-4-1-hero-light.png", "studio-light"),
            (studio / "in-call-studio-dark.png", "p4-4-1-hero-dark.png", "studio-dark"),
        ]
        for ref, shot, name in pairs:
            out = f"p4-4-1-hero-vs-{name}.png"
            side_by_side(ref, shots / shot, shots / out, f"{shot} (assist column)")
            taken.append(out)
    except ImportError:
        print("Pillow not installed: no side by side sheets")
    print(f"SCREENSHOTS {shots}: {taken}")
