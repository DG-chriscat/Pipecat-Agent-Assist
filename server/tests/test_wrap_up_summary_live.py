"""Live P5-1 (AP-9): a Maria Alvarez call through wrap up with the drafted summary.

Run locally with keys, a Postgres, a built portal (npm run build in portal/), and
Playwright's Chromium:
    PLAYWRIGHT_BROWSERS_PATH=... uv run --with playwright \\
        pytest -m live server/tests/test_wrap_up_summary_live.py -s -v

Needs DAILY_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL (a throwaway
schema is created and dropped). P5_SCREENSHOT_DIR (optional) keeps the 1600x1000
screenshots of the wrap up view in light and dark.

One call, start to finish (same set up as test_assist_ui_live.py):
1. A daily-python caller finishes intake as Maria Alvarez; Agent Ana's portal accepts.
2. The agent says a call plan line; the caller says the trigger; the hero shows the
   retro authorization review and Enter accepts it (simulated action on CLM-900001).
3. The agent tells the caller what happens next; the agent presses End.
4. The bot drafts the summary and reason code at call end and posts them. The wrap
   up view fills the summary editor with the draft and preselects the suggested
   code (appeal_started). The test prints both, checks 3 to 5 sentences, the
   reference number, no date of birth or member ID.
5. The agent edits the summary and saves: the calls row has the draft, the saved
   summary, and the code; Ana is available again.
"""

from __future__ import annotations

import asyncio
import base64
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
from agent_assist_bot.wrap_up import sentence_count
from agent_assist_server.app import app
from agent_assist_server.bot_launcher import BotLauncher
from agent_assist_server.daily import DailyRestClient
from agent_assist_server.portal import DEFAULT_DIST
from harness.intake_script import MARIA_ALVAREZ as SCRIPT
from harness.intake_script import next_line
from harness.llm import llm_settings_store
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
AGENT_LINE = "Okay Maria, I pulled up your account, and I can see the claim right here."
CALLER_TRIGGER = "So why did my MRI claim get denied?"
AGENT_CLOSE = (
    "I've opened a retro authorization review for that claim. If it's approved, the "
    "claim is reprocessed and you'll get a letter within 30 days."
)
CALLER_THANKS = "Okay, thank you so much for your help."
EDIT = " Caller thanked the agent."
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
    d = Path(os.getenv("P5_SCREENSHOT_DIR") or tmp_path)
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
    await page.get_by_role("button", name="Agent menu").click()
    await page.get_by_role("menuitemradio", name=theme.capitalize()).click()
    await page.keyboard.press("Escape")
    await page.wait_for_function(f"document.documentElement.dataset.theme === '{theme}'")
    await page.wait_for_timeout(400)


async def snap(page, path: Path, t0: float) -> None:
    await page.screenshot(path=str(path), animations="disabled")
    log(t0, f"screenshot {path.name}")


async def speak(page, pcm: bytes) -> None:
    b64 = base64.b64encode(pcm).decode()
    await page.evaluate("([b, r]) => window.__speak(b, r)", [b64, SAMPLE_RATE])


async def test_maria_call_through_wrap_up_with_the_drafted_summary(schema, shots, monkeypatch):
    from playwright.async_api import async_playwright, expect

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setenv(SERVER_URL_ENV, base)
    monkeypatch.setenv(DATABASE_SCHEMA_ENV, schema)
    monkeypatch.setenv("QUEUE_PERSIST", "1")  # the wrap up lands on the calls row
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    audio = {k: await synthesize(v, sample_rate=SAMPLE_RATE) for k, v in INTAKE_LINES.items()}
    agent_pcm = await synthesize(AGENT_LINE, sample_rate=SAMPLE_RATE, voice=VOICES["agent"])
    close_pcm = await synthesize(AGENT_CLOSE, sample_rate=SAMPLE_RATE, voice=VOICES["agent"])
    trigger_pcm = await synthesize(CALLER_TRIGGER, sample_rate=SAMPLE_RATE, voice=VOICES["caller"])
    thanks_pcm = await synthesize(CALLER_THANKS, sample_rate=SAMPLE_RATE, voice=VOICES["caller"])
    launcher = BotLauncher()
    app.state.bot_launcher = launcher
    # P5-9: LLM_WRAPUP / LLM_ASSIST / LLM_INTAKE set the call's Settings; unset: defaults.
    app.state.settings_store = llm_settings_store()
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

                # ---- 1. accept ---------------------------------------------------------
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

                # ---- 2. plan line, trigger, accept the hero ----------------------------
                await speak(page, agent_pcm)
                log(t0, f"agent: {AGENT_LINE}")
                await asyncio.sleep(1.5)
                await asyncio.wait_for(caller.play(trigger_pcm), 60)
                log(t0, f"caller: {CALLER_TRIGGER}")
                await expect(hero).to_have_attribute("data-state", "suggestion", timeout=15_000)
                await expect(hero.get_by_role("heading")).to_contain_text(
                    "retro authorization review"
                )
                await page.evaluate("document.activeElement && document.activeElement.blur()")
                await page.keyboard.press("Enter")
                await expect(hero).to_have_attribute("data-state", "accepted", timeout=2_000)
                plan = page.get_by_test_id("tile-plan")
                await expect(plan.locator("li.is-done")).to_contain_text(
                    ["Take the next step"], timeout=10_000
                )
                log(t0, "hero accepted: Request retro auth review (simulated)")

                # ---- 3. close the call and End -----------------------------------------
                await speak(page, close_pcm)
                log(t0, f"agent: {AGENT_CLOSE}")
                await asyncio.sleep(1.5)
                await asyncio.wait_for(caller.play(thanks_pcm), 60)
                log(t0, f"caller: {CALLER_THANKS}")
                await asyncio.sleep(2.5)  # last finals land
                end_at = time.time()
                await page.get_by_role("button", name="End call").click()
                view = page.get_by_role("region", name="Wrap up")
                await expect(view).to_be_visible(timeout=15_000)
                log(t0, f"wrap up view {time.time() - end_at:.2f}s after End")

                # ---- 4. the draft lands ------------------------------------------------
                box = view.get_by_label("Summary", exact=True)
                await expect(box).not_to_have_value("", timeout=40_000)
                drafted_in = time.time() - end_at
                summary = await box.input_value()
                picked = view.get_by_role("radio", checked=True)
                await expect(picked).to_have_count(1)
                picked_label = (await picked.inner_text()).replace("\n", " ")
                log(t0, f"draft in the editor {drafted_in:.2f}s after End")
                print(f"DRAFTED SUMMARY: {summary}")
                print(f"SUGGESTED REASON CODE: {picked_label}")
                await expect(picked).to_contain_text("Appeal or retro review started")
                await expect(picked).to_contain_text("Suggested")
                await page.wait_for_timeout(300)
                await snap(page, shots / "p5-1-1-wrap-up-draft-light.png", t0)
                await set_theme(page, "dark")
                await snap(page, shots / "p5-1-1-wrap-up-draft-dark.png", t0)
                await set_theme(page, "light")
                taken += ["p5-1-1-wrap-up-draft-light.png", "p5-1-1-wrap-up-draft-dark.png"]

                # ---- 5. edit and save --------------------------------------------------
                await box.fill(summary + EDIT)
                saved_text = await box.input_value()
                await view.get_by_role("button", name="Save and go available").click()
                await expect(page.get_by_role("status")).to_contain_text(
                    "Call saved", timeout=10_000
                )
                await expect(toggle).to_have_attribute("aria-checked", "true")
                log(t0, "saved; Ana is available")
                await caller.wait_for_participant_left(AGENT_NAME, timeout=15)
                await browser.close()
            await asyncio.wait_for(proc.wait(), 30)

            row = None
            end = time.time() + 10
            while time.time() < end:
                cur = await store._execute(
                    "SELECT wrap_up_reason, wrap_up_summary, wrap_up_draft, intake "
                    "FROM calls WHERE session_id = %s",
                    (session_id,),
                )
                row = await cur.fetchone()
                if row and row[1]:
                    break
                await asyncio.sleep(0.3)
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

    assert row is not None, "no calls row"
    reason, saved_summary, draft, intake = row
    draft = draft if isinstance(draft, dict) else json.loads(draft)
    print("calls row:")
    print(f"  wrap_up_reason  = {reason}")
    print(f"  wrap_up_summary = {saved_summary}")
    print(f"  wrap_up_draft   = {json.dumps(draft)}")
    assert reason == "appeal_started"
    assert saved_summary == saved_text.strip()
    assert saved_summary.endswith(EDIT.strip())
    assert draft["summary"] == summary
    assert draft["reason_code"] == "appeal_started"
    assert draft["pack_id"] == "claims_status"
    assert draft["source"] == "llm", draft
    assert 3 <= sentence_count(summary) <= 5
    assert "CLM-900001" in summary
    assert not re.search(r"\bCLM-\d{1,5}\b", summary), "partial claim id in the summary"
    for private in ("EVM100231", "1984-03-12", "March 12"):
        assert private not in summary
    print(f"SCREENSHOTS {shots}: {taken}")
