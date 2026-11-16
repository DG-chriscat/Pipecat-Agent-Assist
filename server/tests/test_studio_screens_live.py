"""Live P4-0: the Studio restyle, light and dark, on a real call (AP-16 to AP-18).

Run locally with keys, a Postgres, a built portal (npm run build in portal/), and
Playwright's Chromium (Pillow for the side by side sheets):
    PLAYWRIGHT_BROWSERS_PATH=... uv run --with playwright --with pillow \\
        pytest -m live server/tests/test_studio_screens_live.py -s -v

Needs DAILY_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL (a throwaway
schema is created and dropped). P4_SCREENSHOT_DIR (optional) keeps the 1600x1000
screenshots and side by side sheets with docs/design/studio/in-call-studio-*.png.

One call, start to finish, same setup as test_live_transcript_live.py:
1. Sign in: light on a light OS, dark on a dark OS (first visit follows
   prefers-color-scheme, AP-16).
2. A daily-python caller finishes intake as Maria Lopez; Agent Ana's portal goes
   available and rings. Light screenshot, then the theme switch in the rail avatar
   menu goes dark (Esc closes the menu and must not decline the call), dark screenshot.
3. Enter accepts. The caller panel shows Identity 3 of 3 and a masked member ID.
   Caller and agent alternate 4 turns; in call screenshots in dark and light.
4. Reveal member ID: the value shows and the reveal lands on calls.phi_reveals (AP-17).
5. End: the wrap up view, light and dark.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
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
from harness.intake_script import MARIA_LOPEZ as INTAKE
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
STUDIO = Path(__file__).resolve().parents[2] / "docs" / "design" / "studio"
TURNS: list[tuple[str, str]] = [
    ("caller", "Hi, I already called last week about my MRI claim."),
    ("agent", "Thanks for holding, Maria. I can see that claim right here."),
    ("caller", "Nobody told me it would get denied. I have the referral."),
    ("agent", "It looks like the prior authorization was not on file."),
]

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
    """The real switch: rail avatar menu, then Esc closes the menu."""
    await page.get_by_role("button", name="Agent menu").click()
    await page.get_by_role("menuitemradio", name=theme.capitalize()).click()
    await page.keyboard.press("Escape")
    await page.wait_for_function(f"document.documentElement.dataset.theme === '{theme}'")
    await page.wait_for_timeout(400)  # 200 ms color crossfade


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
    draw.text((pad, 18), f"{ref.relative_to(STUDIO.parents[2])} (reference)", fill=(29, 29, 29))
    draw.text((1600 + pad * 2, 18), f"live: {label}", fill=(29, 29, 29))
    sheet.save(out)


async def wait_final_lines(page, n: int, timeout: float = 20.0) -> list[str]:
    end = time.time() + timeout
    rows: list[str] = []
    while time.time() < end:
        rows = await page.evaluate(
            "() => [...document.querySelectorAll('li.tr[aria-busy=\"false\"] p')]"
            ".map((p) => p.textContent)"
        )
        if len(rows) >= n:
            return rows
        await asyncio.sleep(0.2)
    return rows


async def test_studio_screens_light_and_dark(schema, shots, monkeypatch):
    from playwright.async_api import async_playwright, expect

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setenv(SERVER_URL_ENV, base)
    monkeypatch.setenv(DATABASE_SCHEMA_ENV, schema)
    monkeypatch.setenv("QUEUE_PERSIST", "1")  # the PHI reveal lands on the calls row
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    intake_audio = {k: await synthesize(v, sample_rate=SAMPLE_RATE) for k, v in INTAKE.items()}
    turn_audio = [
        await synthesize(text, sample_rate=SAMPLE_RATE, voice=VOICES[role]) for role, text in TURNS
    ]
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

            # ---- 1. sign in, both themes from the OS preference ---------------------
            for scheme in ("dark", "light"):
                c = await browser.new_context(viewport=VIEWPORT, color_scheme=scheme)
                p = await c.new_page()
                await p.goto(f"{base}/agent/")
                await expect(p.get_by_role("heading", name="Sign in")).to_be_visible()
                assert await p.evaluate("document.documentElement.dataset.theme") == scheme
                await p.wait_for_timeout(500)
                await snap(p, shots / f"p4-0-1-sign-in-{scheme}.png", t0)
                taken.append(f"p4-0-1-sign-in-{scheme}.png")
                await c.close()

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
            assert await page.evaluate("document.documentElement.dataset.theme") == "light"
            log(t0, "portal signed in (light)")

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
                queued = await run_intake(
                    http, store, caller, rec, session_id, intake_audio, t_start
                )
                assert queued is not None, "intake never enqueued the call"
                checks = queued["summary"].get("identity_checks")
                log(t0, f"enqueued: identity={queued['identity_result']} checks={checks}")
                assert queued["identity_result"] == "verified"
                assert checks == ["full_name", "dob", "member_id"]

                # ---- 2. incoming call, light then dark --------------------------------
                await toggle.click()
                card = page.get_by_role("dialog", name="Incoming call")
                await expect(card).to_be_visible(timeout=5_000)
                await page.wait_for_timeout(800)
                await snap(page, shots / "p4-0-2-incoming-call-light.png", t0)
                await set_theme(page, "dark")
                # Esc closed the menu; the call is still ringing (not declined).
                await expect(card).to_be_visible()
                await snap(page, shots / "p4-0-2-incoming-call-dark.png", t0)
                taken += ["p4-0-2-incoming-call-light.png", "p4-0-2-incoming-call-dark.png"]

                accept_at = time.time()
                await page.keyboard.press("Enter")
                await expect(page.get_by_role("button", name="Mute")).to_be_enabled(timeout=30_000)
                log(t0, f"accepted; joined {time.time() - accept_at:.2f}s after Enter")
                panel = page.get_by_role("complementary", name="Caller")
                badge = panel.get_by_test_id("identity-badge")
                await expect(badge).to_have_text("Identity 3 of 3")
                await expect(panel.get_by_test_id("phi-member_id")).to_have_text("••••31")
                await expect(panel.get_by_test_id("caller-avatar")).to_have_class(
                    re.compile("is-verified")
                )
                await expect(panel.get_by_text("Evergreen HMO Silver")).to_be_visible(
                    timeout=10_000
                )
                await caller.wait_for_participant(AGENT_NAME, timeout=15)
                await wait_bot_turn(rec, accept_at, start_timeout=10)

                # ---- 3. conversation --------------------------------------------------
                for i, ((role, text), pcm) in enumerate(zip(TURNS, turn_audio, strict=True), 1):
                    if role == "caller":
                        await asyncio.wait_for(caller.play(pcm), 60)
                    else:
                        b64 = base64.b64encode(pcm).decode()
                        await page.evaluate("([b, r]) => window.__speak(b, r)", [b64, SAMPLE_RATE])
                    log(t0, f"turn {i} {role}: {text}")
                    await asyncio.sleep(1.5)
                lines = await wait_final_lines(page, len(TURNS))
                log(t0, f"final lines on screen: {lines}")
                assert len(lines) >= len(TURNS)
                await page.wait_for_timeout(1500)
                await snap(page, shots / "p4-0-3-in-call-dark.png", t0)
                await set_theme(page, "light")
                await snap(page, shots / "p4-0-3-in-call-light.png", t0)
                taken += ["p4-0-3-in-call-dark.png", "p4-0-3-in-call-light.png"]

                # ---- 4. reveal (AP-17) -------------------------------------------------
                await panel.get_by_role("button", name="Reveal member ID").click()
                await expect(panel.get_by_test_id("phi-member_id")).to_have_text("418302")
                await snap(page, shots / "p4-0-4-member-id-revealed-light.png", t0)
                taken.append("p4-0-4-member-id-revealed-light.png")
                reveals = None
                end = time.time() + 10
                while time.time() < end and not reveals:
                    cur = await store._execute(
                        "SELECT phi_reveals FROM calls WHERE session_id = %s", (session_id,)
                    )
                    row = await cur.fetchone()
                    reveals = row[0] if row else None
                    await asyncio.sleep(0.3)
                log(t0, f"calls.phi_reveals: {reveals}")
                assert reveals and reveals[0]["field"] == "member_id"
                assert "418302" not in str(reveals)

                # ---- 5. wrap up --------------------------------------------------------
                await page.get_by_role("button", name="End call").click()
                view = page.get_by_role("region", name="Wrap up")
                await expect(view.get_by_role("radio").first).to_be_visible(timeout=15_000)
                await page.wait_for_timeout(500)
                await snap(page, shots / "p4-0-5-wrap-up-light.png", t0)
                await set_theme(page, "dark")
                await snap(page, shots / "p4-0-5-wrap-up-dark.png", t0)
                taken += ["p4-0-5-wrap-up-light.png", "p4-0-5-wrap-up-dark.png"]
                await caller.wait_for_participant_left(AGENT_NAME, timeout=15)
                await browser.close()
            await asyncio.wait_for(proc.wait(), 30)
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

    try:
        for theme in ("light", "dark"):
            side_by_side(
                STUDIO / f"in-call-studio-{theme}.png",
                shots / f"p4-0-3-in-call-{theme}.png",
                shots / f"p4-0-3-in-call-{theme}-vs-design.png",
                f"in call, {theme}",
            )
            taken.append(f"p4-0-3-in-call-{theme}-vs-design.png")
    except ImportError:
        print("Pillow not installed: no side by side sheets")
    print(f"SCREENSHOTS {shots}: {taken}")
