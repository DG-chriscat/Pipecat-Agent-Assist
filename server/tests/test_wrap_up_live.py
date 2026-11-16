"""Live P3-5, the Phase 3 exit: ring to wrap up, with an agent drop in the middle.

Run locally with keys, a Postgres, a built portal (npm run build in portal/), and
Playwright's Chromium:
    PLAYWRIGHT_BROWSERS_PATH=... uv run --with playwright \\
        pytest -m live server/tests/test_wrap_up_live.py -s -v

Needs DAILY_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL (a throwaway
schema is created and dropped). P3_SCREENSHOT_DIR (optional) keeps the 1600x1000
screenshots and the side by side comparisons with docs/design/in-call-focus.png.

One call, start to finish:
1. The real server starts the bot (POST /calls/browser); a daily-python caller (S1
   harness) finishes intake as Maria Alvarez; the bot enqueues the call.
2. Agent Ana's portal (headless Chromium) goes available, the card rings, Enter
   accepts. The caller is already talking; the page measures when the caller's own
   audio track first carries sound (accept to hearing the caller, target < 2 s).
3. Live transcript: a caller line and an agent line (fake mic) show up labeled.
4. QH-9: Ana's tab closes without End. The bot reports the agent left; the server
   puts the call back at the front of the queue and sets Ana not available; the bot
   speaks to the caller again.
5. Agent Ben's portal rings with the same call and accepts (second timing sample).
6. Ben presses End: the wrap up view shows; the bot says goodbye and leaves; the
   room closes. Ben picks a reason code, adds notes, and saves: the calls row has the
   wrap up, and Ben is available again.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import os
import re
import socket
import statistics
import time
import uuid
from pathlib import Path

import httpx
import pytest
import uvicorn
from agent_assist_bot.bot import BOT_NAME
from agent_assist_bot.calls import DATABASE_SCHEMA_ENV, PostgresCallStore
from agent_assist_bot.handoff import SERVER_URL_ENV
from agent_assist_bot.hold import GOODBYE_LINE, REQUEUED_LINE
from agent_assist_server import queue_api
from agent_assist_server.app import app
from agent_assist_server.bot_launcher import BotLauncher
from agent_assist_server.daily import DailyRestClient
from agent_assist_server.portal import DEFAULT_DIST
from harness.intake_script import MARIA_ALVAREZ as INTAKE
from harness.intake_script import next_line
from harness.participant import SAMPLE_RATE, HarnessParticipant
from harness.tts import synthesize
from harness.turns import audio_slice, transcribe, wait_bot_turn

pytestmark = pytest.mark.live

REQUIRED = ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL")
ANA, BEN = "Agent Ana", "Agent Ben"
MAX_TURNS = 14
VIEWPORT = {"width": 1600, "height": 1000}
CHROMIUM_ARGS = [
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
]
REFERENCE = Path(__file__).resolve().parents[2] / "docs" / "design" / "in-call-focus.png"
ACCEPT_TO_AUDIO_TARGET_SECS = 2.0  # the card's target, checked on the median
ACCEPT_TO_AUDIO_CEILING_SECS = 3.0  # any single accept (shared host, other live runs)

CALLER_LINES = {
    "ana": "Hi, I already called last week about my MRI claim, and nobody told me it "
    "would get denied. I have the referral right here.",
    "ben": "Hello? Okay, good. So what do I do now? I cannot pay twelve hundred dollars "
    "for that scan.",
}
AGENT_LINE = "Thanks for holding, Maria. I can see that claim right here."

# Replaces the portal's microphone with a WebAudio stream the test can speak into, and
# measures, per remote participant, when their audio element first carries sound.
PAGE_SCRIPT = """
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
    const start = Date.now();
    src.onended = () => resolve({ start: start / 1000, end: Date.now() / 1000 });
    src.start();
  });
  // First time each remote participant's audio is above a speech floor.
  window.__firstAudio = {};
  const probes = new Map();
  setInterval(() => {
    document.querySelectorAll('audio[data-participant]').forEach((el) => {
      const id = el.dataset.participant;
      if (probes.has(id) || !el.srcObject) return;
      ensure();
      const src = ctx.createMediaStreamSource(el.srcObject);
      const an = ctx.createAnalyser();
      an.fftSize = 1024;
      src.connect(an);
      probes.set(id, an);
    });
    if (ctx && ctx.state !== 'running') ctx.resume();
    const data = new Float32Array(1024);
    probes.forEach((an, id) => {
      if (window.__firstAudio[id]) return;
      an.getFloatTimeDomainData(data);
      let s = 0;
      for (let i = 0; i < data.length; i++) s += data[i] * data[i];
      if (Math.sqrt(s / data.length) > 0.01) window.__firstAudio[id] = Date.now() / 1000;
    });
  }, 20);
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


async def agent_state(http: httpx.AsyncClient, name: str) -> dict:
    return next(a for a in (await http.get("/queue")).json()["agents"] if a["name"] == name)


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


async def open_portal(browser, base: str, name: str):
    from playwright.async_api import expect

    ctx = await browser.new_context(viewport=VIEWPORT, permissions=["microphone"])
    await ctx.add_init_script(PAGE_SCRIPT)
    page = await ctx.new_page()
    page.on("console", lambda m: m.type == "error" and print(f"  [{name}] {m.text}"))
    await page.goto(f"{base}/agent/")
    await page.get_by_label("Your name").fill(name)
    await page.get_by_role("button", name="Sign in").click()
    toggle = page.get_by_role("switch", name="Available")
    await expect(toggle).to_be_enabled(timeout=10_000)
    return ctx, page, toggle


async def ring_and_accept(page, toggle, caller, line_pcm, shots, shot_name, t0, timings, tag):
    """Go available, wait for the card, then press Enter while the caller is talking.
    Returns the caller's play task. Records accept -> summary, joined, and caller audio."""
    from playwright.async_api import expect

    await toggle.click()
    card = page.get_by_role("dialog", name="Incoming call")
    await expect(card).to_be_visible(timeout=10_000)
    await page.wait_for_timeout(1000)  # key listener attaches just after paint
    await page.screenshot(path=str(shots / shot_name), animations="disabled")
    caller_id = caller._client.participants()["local"]["id"]
    talking = asyncio.create_task(caller.play(line_pcm))
    await asyncio.sleep(0.3)  # the caller is mid sentence when the agent picks up
    accept_at = time.time()
    await page.keyboard.press("Enter")
    await expect(page.get_by_role("complementary", name="Caller")).to_be_visible(timeout=10_000)
    timings[f"{tag}_accept_to_summary_secs"] = time.time() - accept_at
    await expect(page.get_by_role("button", name="Mute")).to_be_enabled(timeout=30_000)
    timings[f"{tag}_accept_to_joined_secs"] = time.time() - accept_at
    heard = None
    end = time.time() + 15
    while heard is None and time.time() < end:
        heard = await page.evaluate("(id) => window.__firstAudio[id] || null", caller_id)
        if heard is None:
            await asyncio.sleep(0.05)
    assert heard is not None, "the portal never played the caller's audio"
    timings[f"{tag}_accept_to_caller_audio_secs"] = heard - accept_at
    marks = await page.evaluate(
        "() => { const o = performance.timeOrigin; return performance.getEntriesByType('mark')"
        ".filter((m) => m.name.startsWith('vitals:'))"
        ".map((m) => [m.name.slice(7), (o + m.startTime) / 1000]); }"
    )
    steps = ", ".join(f"{n} +{t - accept_at:.2f}s" for n, t in marks if t >= accept_at - 1)
    log(t0, f"{tag}: portal timeline after Enter: {steps}, caller audio +{heard - accept_at:.2f}s")
    await page.evaluate("() => performance.clearMarks()")
    log(
        t0,
        f"{tag}: summary {timings[f'{tag}_accept_to_summary_secs']:.2f}s, joined "
        f"{timings[f'{tag}_accept_to_joined_secs']:.2f}s, caller audio heard "
        f"{timings[f'{tag}_accept_to_caller_audio_secs']:.2f}s after Enter",
    )
    return talking


async def wait_final_line(page, role: str, words: str, timeout: float = 20.0) -> str:
    """Wait for a final transcript row of this role that shares words with `words`."""
    want = set(re.findall(r"[a-z']+", words.lower()))
    end = time.time() + timeout
    while time.time() < end:
        rows = await page.evaluate(
            "(r) => [...document.querySelectorAll('li.tr-' + r + '[aria-busy=\"false\"] p')]"
            ".map((p) => p.textContent)",
            role,
        )
        for text in rows:
            if len(set(re.findall(r"[a-z']+", text.lower())) & want) >= 3:
                return text
        await asyncio.sleep(0.2)
    raise AssertionError(f"no final {role} line like {words!r}")


def side_by_side(shot: Path, out: Path, label: str) -> None:
    """Reference mockup on the left, the live screenshot on the right."""
    from PIL import Image, ImageDraw

    ref = Image.open(REFERENCE).convert("RGB").resize((1600, 1000))
    live = Image.open(shot).convert("RGB").resize((1600, 1000))
    pad, head = 24, 56
    sheet = Image.new("RGB", (1600 * 2 + pad * 3, 1000 + head + pad), (11, 11, 12))
    sheet.paste(ref, (pad, head))
    sheet.paste(live, (1600 + pad * 2, head))
    draw = ImageDraw.Draw(sheet)
    draw.text((pad, 18), "docs/design/in-call-focus.png (reference)", fill=(252, 250, 248))
    draw.text((1600 + pad * 2, 18), f"live: {label}", fill=(252, 250, 248))
    sheet.save(out)


async def test_live_ring_to_wrap_up_with_agent_drop(schema, shots, monkeypatch):
    from playwright.async_api import async_playwright, expect

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setenv(SERVER_URL_ENV, base)
    monkeypatch.setenv(DATABASE_SCHEMA_ENV, schema)
    monkeypatch.setenv("QUEUE_PERSIST", "1")  # wrap up lands on the calls row
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    intake_audio = {k: await synthesize(v, sample_rate=SAMPLE_RATE) for k, v in INTAKE.items()}
    caller_audio = {
        k: await synthesize(v, sample_rate=SAMPLE_RATE, voice="aura-2-arcas-en")
        for k, v in CALLER_LINES.items()
    }
    agent_pcm = await synthesize(AGENT_LINE, sample_rate=SAMPLE_RATE, voice="aura-2-thalia-en")
    launcher = BotLauncher()
    app.state.bot_launcher = launcher
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serve_task = asyncio.create_task(server.serve())
    daily = DailyRestClient.from_env()
    store = PostgresCallStore(os.environ["DATABASE_URL"], schema=schema)
    room_name = None
    timings: dict[str, float] = {}
    heard: dict[str, str] = {}
    t0 = time.time()
    try:
        while not server.started:
            await asyncio.sleep(0.05)
        async with httpx.AsyncClient(base_url=base, timeout=30) as http, async_playwright() as pw:
            browser = await pw.chromium.launch(args=CHROMIUM_ARGS)
            ana_ctx, ana, ana_toggle = await open_portal(browser, base, ANA)
            log(t0, "Ana's portal signed in")

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
                rec = caller.recording_for(BOT_NAME)
                t_start = max(caller.joined_at, bot.joined_at)
                log(t0, "caller and bot in the room; intake starts")
                queued = await run_intake(
                    http, store, caller, rec, session_id, intake_audio, t_start
                )
                assert queued is not None, "intake never enqueued the call"
                log(t0, f"call enqueued: identity={queued['identity_result']}")

                # ---- 1. Ana: ring -> accept, caller audio timing -----------------------
                talking = await ring_and_accept(
                    ana,
                    ana_toggle,
                    caller,
                    caller_audio["ana"],
                    shots,
                    "p3-5-1-ringing.png",
                    t0,
                    timings,
                    "ana",
                )
                await caller.wait_for_participant(ANA, timeout=15)
                await talking
                line = await wait_final_line(ana, "caller", CALLER_LINES["ana"])
                log(t0, f"Ana's transcript: [Caller] {line!r}")
                played = await ana.evaluate(
                    "([b, r]) => window.__speak(b, r)",
                    [base64.b64encode(agent_pcm).decode(), SAMPLE_RATE],
                )
                line = await wait_final_line(ana, "agent", AGENT_LINE)
                timings["agent_line_to_screen_secs"] = time.time() - played["end"]
                log(t0, f"Ana's transcript: [You] {line!r}")
                await ana.wait_for_timeout(600)
                await ana.screenshot(path=str(shots / "p3-5-2-in-call.png"), animations="disabled")
                assert (await agent_state(http, ANA))["state"] == "on_call"

                # ---- 2. QH-9: Ana's tab goes away without End --------------------------
                drop_at = time.time()
                await ana_ctx.close()
                log(t0, "Ana's tab closed without End")
                gone = await caller.wait_for_participant_left(ANA, timeout=60)
                timings["drop_to_room_leave_secs"] = gone.left_at - drop_at
                requeued = None
                end = time.time() + 30
                while requeued is None and time.time() < end:
                    requeued = await queued_call(http, session_id)
                    if requeued is None:
                        await asyncio.sleep(0.1)
                assert requeued is not None, "the call never went back to the queue"
                timings["room_leave_to_requeued_secs"] = time.time() - gone.left_at
                snap = (await http.get("/queue")).json()
                assert snap["queue"][0]["session_id"] == session_id, "not at the front"
                assert (await agent_state(http, ANA))["state"] == "offline"
                log(
                    t0,
                    f"Ana left the room {timings['drop_to_room_leave_secs']:.2f}s after the tab "
                    f"closed; call re-queued at the front "
                    f"{timings['room_leave_to_requeued_secs']:.2f}s later; Ana not available",
                )
                turn = await wait_bot_turn(rec, gone.left_at, start_timeout=20)
                assert turn is not None, "the bot did not speak again after the drop"
                heard["requeued"] = await transcribe(audio_slice(rec, *turn))
                log(t0, f"bot to the caller after the drop: {heard['requeued']!r}")
                assert "front of the line" in heard["requeued"].lower()
                assert proc.returncode is None

                # ---- 3. Ben takes the re-queued call -----------------------------------
                ben_ctx, ben, ben_toggle = await open_portal(browser, base, BEN)
                talking = await ring_and_accept(
                    ben,
                    ben_toggle,
                    caller,
                    caller_audio["ben"],
                    shots,
                    "p3-5-3-ringing-after-drop.png",
                    t0,
                    timings,
                    "ben",
                )
                await caller.wait_for_participant(BEN, timeout=15)
                await talking
                line = await wait_final_line(ben, "caller", CALLER_LINES["ben"])
                log(t0, f"Ben's transcript: [Caller] {line!r}")
                await ben.wait_for_timeout(600)
                await ben.screenshot(
                    path=str(shots / "p3-5-4-in-call-after-drop.png"), animations="disabled"
                )

                # ---- 4. Ben ends: wrap up view, goodbye, room closes -------------------
                end_at = time.time()
                await ben.get_by_role("button", name="End call").click()
                view = ben.get_by_role("region", name="Wrap up")
                await expect(view).to_be_visible(timeout=10_000)
                await expect(view.get_by_text("You ended the call")).to_be_visible(timeout=10_000)
                timings["end_to_wrap_up_view_secs"] = time.time() - end_at
                assert (await agent_state(http, BEN))["state"] == "wrap_up"
                log(t0, f"wrap up view {timings['end_to_wrap_up_view_secs']:.2f}s after End")
                turn = await wait_bot_turn(rec, end_at, start_timeout=10)
                assert turn is not None, "the bot did not say goodbye"
                heard["goodbye"] = await transcribe(audio_slice(rec, *turn))
                log(t0, f"bot to the caller after End: {heard['goodbye']!r}")
                await asyncio.wait_for(proc.wait(), 30)
                timings["end_to_bot_exit_secs"] = time.time() - end_at
                log(t0, f"bot exited returncode={proc.returncode}")

                await view.get_by_role("radio", name="Appeal or retro review started").click()
                await view.get_by_label("Notes").fill(
                    "Denied for no prior auth on file. Referral was in place; started a "
                    "retro authorization review. Caller expects a call back in 5 to 7 days."
                )
                await ben.screenshot(path=str(shots / "p3-5-5-wrap-up.png"), animations="disabled")
                save_at = time.time()
                await view.get_by_role("button", name=re.compile("Save")).click()
                await expect(view).to_be_hidden(timeout=10_000)
                await expect(ben_toggle).to_have_attribute("aria-checked", "true")
                timings["save_to_available_secs"] = time.time() - save_at
                assert (await agent_state(http, BEN))["state"] == "available"
                await expect(ben.get_by_text("Call saved. You are available.")).to_be_visible()
                shot = shots / "p3-5-6-available.png"
                await ben.screenshot(path=str(shot), animations="disabled")
                log(t0, f"saved; Ben available {timings['save_to_available_secs']:.2f}s later")
                await ben_ctx.close()
                await browser.close()

            # The write-through runs off the hot path; give it a moment.
            row = None
            end = time.time() + 10
            while time.time() < end:
                cur = await store._execute(
                    "SELECT c.status, c.ended_by, c.wrap_up_reason, c.wrap_up_notes, "
                    "c.wrapped_up_at IS NOT NULL, a.name, "
                    "(SELECT count(*) FROM transcript_lines t WHERE t.call_id = c.call_id) "
                    "FROM calls c LEFT JOIN agents a USING (agent_id) WHERE c.session_id = %s",
                    (session_id,),
                )
                row = await cur.fetchone()
                if row and row[2]:
                    break
                await asyncio.sleep(0.3)
            log(t0, f"calls row: {row}")
            room_gone = False
            end = time.time() + queue_api.ROOM_CLOSE_DELAY_SECS + 10
            while not room_gone and time.time() < end:
                async with httpx.AsyncClient(timeout=10) as raw:
                    r = await raw.get(
                        f"https://api.daily.co/v1/rooms/{room_name}",
                        headers={"Authorization": f"Bearer {os.environ['DAILY_API_KEY']}"},
                    )
                room_gone = r.status_code == 404
                if not room_gone:
                    await asyncio.sleep(1)
            log(t0, f"room deleted after End: {room_gone}")
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
        for name, label in [
            ("p3-5-1-ringing.png", "ringing"),
            ("p3-5-2-in-call.png", "in call, live transcript"),
            ("p3-5-4-in-call-after-drop.png", "in call after the agent drop"),
            ("p3-5-5-wrap-up.png", "wrap up"),
            ("p3-5-6-available.png", "saved, available"),
        ]:
            side_by_side(shots / name, shots / name.replace(".png", "-vs-design.png"), label)
    except ImportError:
        print("Pillow not installed: no side by side images")

    samples = [timings[f"{who}_accept_to_caller_audio_secs"] for who in ("ana", "ben")]
    print(
        "TIMINGS "
        + json.dumps({k: round(v, 2) for k, v in timings.items()}, indent=2, sort_keys=True)
    )
    print(
        f"ACCEPT TO HEARING THE CALLER: {[round(s, 2) for s in samples]} "
        f"(median {statistics.median(samples):.2f}s, target < {ACCEPT_TO_AUDIO_TARGET_SECS}s)"
    )
    print("HEARD " + json.dumps(heard, indent=2))
    print(f"SCREENSHOTS {shots}")

    status, ended_by, reason, notes, wrapped, agent_name, lines = row
    assert (status, ended_by, reason, wrapped, agent_name) == (
        "ended",
        "agent",
        "appeal_started",
        True,
        BEN,
    )
    assert notes.startswith("Denied for no prior auth")
    assert lines >= 3, "transcript lines were not saved"
    # The caller's recording is transcribed by STT; "goodbye" came back as "good guy" once
    # (run 7, bot log shows GOODBYE_LINE). Match the start of the line instead.
    assert "thanks for calling" in heard["goodbye"].lower()
    assert REQUEUED_LINE and GOODBYE_LINE  # the lines the bot spoke (see HEARD above)
    assert room_gone, "the room was not closed after End"
    # Target: median under 2 s; no single accept over the 3 s ceiling (shared 2 CPU host).
    assert statistics.median(samples) < ACCEPT_TO_AUDIO_TARGET_SECS, samples
    assert all(s < ACCEPT_TO_AUDIO_CEILING_SECS for s in samples), samples
