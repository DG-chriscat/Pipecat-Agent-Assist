"""Live P4-5: caller tone readings, the mood trend, and the tone tip on a real call.

Run locally with keys, a Postgres, a built portal (npm run build in portal/), and
Playwright's Chromium:
    PLAYWRIGHT_BROWSERS_PATH=... uv run --with playwright \\
        pytest -m live server/tests/test_tone_live.py -s -v

Needs DAILY_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL (a throwaway
schema is created and dropped). P4_SCREENSHOT_DIR (optional) keeps the 1600x1000
screenshots. The Pipecat Tone model must be downloadable (or already cached):
`python -m agent_assist_bot.tone_prefetch`.

Scripted call, Maria Lopez (same intake as test_assist_ui_live.py), the agent is the
real portal in headless Chromium:
1. Intake; every caller line is read by the intake reader and saved with its tone.
2. Agent Ana accepts; the portal gets the mood trend (intake readings) at once.
3. The agent says a line, the caller answers calmly, then the frustrated turn:
   "This is ridiculous. I have called three times already and nobody has fixed it."
4. The reading drops two steps: the portal shows a tone tip tile, the mood chip in
   the call island turns, and the caller panel draws one bar per caller turn.
   Screenshots in light and dark.
5. The caller's trigger line puts the hero up next to the tone tip (screenshots).
6. After the call: transcript_lines has one tone reading per caller line, the
   frustrated line reads Worried or Frustrated, and assist_events logs the tip.

`[tone] read_ms=` and RSS per bot process: the bot logs each read's time; the test
samples the bot process RSS (/proc) and prints its peak. Run with TONE_READER=0 and
`-k baseline` for the same call without the reader (RSS only; no tone asserted).
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
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
from harness.intake_script import MARIA_LOPEZ as SCRIPT
from harness.intake_script import next_line
from harness.participant import SAMPLE_RATE, HarnessParticipant
from harness.procstats import ProcSampler
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
CALLER_CALM = "Okay, thank you for looking into it."
CALLER_FRUSTRATED = (
    "Honestly, this is ridiculous. I have called three times already and nobody has fixed it."
)
CALLER_TRIGGER = "So why did my MRI claim get denied?"
INTAKE_LINES = dict(
    SCRIPT,
    identity=(
        "Sure. My name is Maria Lopez, that's L O P E Z. My date of birth is March "
        "twelfth, nineteen eighty four. And my member ID is four one eight, three zero two."
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
    await page.get_by_role("button", name="Agent menu").click()
    await page.get_by_role("menuitemradio", name=theme.capitalize()).click()
    await page.keyboard.press("Escape")
    await page.wait_for_function(f"document.documentElement.dataset.theme === '{theme}'")
    await page.wait_for_timeout(400)


async def snap(page, path: Path, t0: float) -> None:
    await page.screenshot(path=str(path), animations="disabled")
    log(t0, f"screenshot {path.name}")


async def run_call(schema, shots, monkeypatch, *, reader_on: bool) -> dict:
    from playwright.async_api import async_playwright, expect

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setenv(SERVER_URL_ENV, base)
    monkeypatch.setenv(DATABASE_SCHEMA_ENV, schema)
    monkeypatch.setenv("TONE_READER", "1" if reader_on else "0")
    monkeypatch.delenv("QUEUE_PERSIST", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    audio = {k: await synthesize(v, sample_rate=SAMPLE_RATE) for k, v in INTAKE_LINES.items()}
    agent_pcm = await synthesize(AGENT_LINE, sample_rate=SAMPLE_RATE, voice=VOICES["agent"])
    said = {
        k: await synthesize(v, sample_rate=SAMPLE_RATE, voice=VOICES["caller"])
        for k, v in (
            ("calm", CALLER_CALM),
            ("frustrated", CALLER_FRUSTRATED),
            ("trigger", CALLER_TRIGGER),
        )
    }
    launcher = BotLauncher()
    app.state.bot_launcher = launcher
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serve_task = asyncio.create_task(server.serve())
    daily = DailyRestClient.from_env()
    store = PostgresCallStore(os.environ["DATABASE_URL"], schema=schema)
    room_name = None
    sampler_task = None
    t0 = time.time()
    out: dict = {"taken": []}
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
            call = r.json()
            session_id = call["session_id"]
            room_name = call["room_url"].rsplit("/", 1)[-1]
            proc = launcher.processes[session_id]
            sampler = ProcSampler(proc.pid)
            sampler_task = asyncio.create_task(sampler.run(0.5))
            log(t0, f"room {room_name}, bot pid {proc.pid}, reader {'on' if reader_on else 'off'}")

            async with HarnessParticipant("caller") as caller:
                await caller.join(call["room_url"], call["caller_token"])
                bot = await caller.wait_for_participant(BOT_NAME, timeout=120)
                rec = caller.recording_for(BOT_NAME)
                t_start = max(caller.joined_at, bot.joined_at)
                queued = await run_intake(http, store, caller, rec, session_id, audio, t_start)
                assert queued is not None, "intake never enqueued the call"
                log(t0, f"enqueued: identity={queued['identity_result']}")

                # ---- accept in the portal ---------------------------------------------
                await toggle.click()
                await expect(page.get_by_role("dialog", name="Incoming call")).to_be_visible(
                    timeout=5_000
                )
                accept_at = time.time()
                await page.keyboard.press("Enter")
                await expect(page.get_by_role("button", name="Mute")).to_be_enabled(timeout=30_000)
                await caller.wait_for_participant(AGENT_NAME, timeout=15)
                await wait_bot_turn(rec, accept_at, start_timeout=10)
                chip = page.get_by_test_id("mood-chip")
                mood = page.get_by_test_id("mood")
                log(t0, f"accepted; mood chip: {await chip.inner_text()!r}")
                out["chip_after_accept"] = await chip.inner_text()

                # ---- agent line, calm answer, then the frustrated turn -----------------
                b64 = base64.b64encode(agent_pcm).decode()
                await page.evaluate("([b, r]) => window.__speak(b, r)", [b64, SAMPLE_RATE])
                await asyncio.sleep(1.0)
                await asyncio.wait_for(caller.play(said["calm"]), 60)
                await asyncio.sleep(1.5)
                frustrated_at = time.time()
                await asyncio.wait_for(caller.play(said["frustrated"]), 60)
                log(t0, f"caller: {CALLER_FRUSTRATED}")
                tip = page.locator("[data-kind='tone_tip']")
                if reader_on:
                    await expect(tip).to_be_visible(timeout=15_000)
                    out["tip_secs"] = time.time() - frustrated_at
                    # Read the chip at once: the mood message comes just before the tip,
                    # and Deepgram may split the turn into more lines that read calmer.
                    out["chip"] = await chip.inner_text()
                    out["chip_class"] = await chip.get_attribute("class")
                    out["bars"] = await mood.locator(".bars i").count()
                    out["mood_label"] = await mood.get_attribute("aria-label")
                    out["tip_text"] = await tip.inner_text()
                    await snap(page, shots / "p4-5-1-tone-tip-light.png", t0)
                    log(t0, f"tone tip on screen {out['tip_secs']:.2f}s after the turn started")
                    log(t0, f"chip {out['chip']!r} ({out['chip_class']}), bars {out['bars']}")
                    await set_theme(page, "dark")
                    await snap(page, shots / "p4-5-1-tone-tip-dark.png", t0)
                    await set_theme(page, "light")
                    out["taken"] += ["p4-5-1-tone-tip-light.png", "p4-5-1-tone-tip-dark.png"]
                    await page.wait_for_timeout(3_000)
                    out["chip_later"] = await chip.inner_text()
                else:
                    await page.wait_for_timeout(4_000)
                    out["chip"] = await chip.inner_text()
                    out["tip_count"] = await tip.count()

                # ---- the trigger: hero next to the tip ----------------------------------
                await asyncio.sleep(1.0)
                await asyncio.wait_for(caller.play(said["trigger"]), 60)
                hero = page.get_by_test_id("hero")
                await expect(hero).to_have_attribute("data-state", "suggestion", timeout=15_000)
                await page.wait_for_timeout(2500)
                if reader_on:
                    await snap(page, shots / "p4-5-2-hero-and-tip-light.png", t0)
                    await set_theme(page, "dark")
                    await snap(page, shots / "p4-5-2-hero-and-tip-dark.png", t0)
                    await set_theme(page, "light")
                    out["taken"] += [
                        "p4-5-2-hero-and-tip-light.png",
                        "p4-5-2-hero-and-tip-dark.png",
                    ]
                out["call_rss"] = sampler.summary(accept_at)

                await page.get_by_role("button", name="End call").click()
                await expect(page.get_by_role("region", name="Wrap up")).to_be_visible(
                    timeout=15_000
                )
                await caller.wait_for_participant_left(AGENT_NAME, timeout=15)
                await browser.close()
            await asyncio.wait_for(proc.wait(), 30)
            out["rss"] = sampler.summary(t_start)

            cur = await store._execute(
                "SELECT t.speaker_role, t.text, t.tone, t.tone_reading FROM transcript_lines t "
                "JOIN calls c USING (call_id) WHERE c.session_id = %s ORDER BY t.ts, t.line_id",
                (session_id,),
            )
            out["lines"] = await cur.fetchall()
            cur = await store._execute(
                "SELECT e.kind, e.card_id, e.data FROM assist_events e "
                "JOIN calls c USING (call_id) WHERE c.session_id = %s ORDER BY e.event_id",
                (session_id,),
            )
            out["events"] = await cur.fetchall()
    finally:
        if sampler_task is not None:
            sampler_task.cancel()
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
    return out


def report(out: dict) -> None:
    print("\nTONE READINGS (transcript_lines, in order):")
    for role, text, tone, reading in out["lines"]:
        if role != "caller":
            print(f"  agent  {text!r}")
            continue
        if reading:
            labels = ", ".join(f"{lab}={c:.2f}" for lab, c in reading["labels"])
            print(
                f"  caller {text!r}\n"
                f"         tone={tone} mood={reading['mood']} (step {reading['step']}) "
                f"[{labels}] sarcasm={reading['sarcasm']:.2f}"
            )
        else:
            print(f"  caller {text!r}\n         tone=None")
    tone_events = [(k, c, d) for k, c, d in out["events"] if c and c.startswith("tone:")]
    print("tone tip assist_events:")
    for kind, card_id, data in tone_events:
        print(f"  {kind:10} {card_id:22} {json.dumps(data)}")
    print("BOT PROCESS " + json.dumps({"whole_call": out["rss"], "after_accept": out["call_rss"]}))
    print("SCREENSHOTS " + json.dumps(out["taken"]))


async def test_tone_on_a_live_call(schema, shots, monkeypatch):
    out = await run_call(schema, shots, monkeypatch, reader_on=True)
    report(out)
    print(
        "PORTAL "
        + json.dumps(
            {
                "chip_after_accept": out["chip_after_accept"],
                "chip": out["chip"],
                "chip_class": out["chip_class"],
                "chip_3s_later": out["chip_later"],
                "bars": out["bars"],
                "mood_aria": out["mood_label"],
                "tip_secs": round(out["tip_secs"], 2),
                "tip_text": out["tip_text"],
            }
        )
    )

    caller = [(t, tone, r) for role, t, tone, r in out["lines"] if role == "caller"]
    assert caller and all(r is not None and tone == r["emotion"] for _, tone, r in caller), caller
    agents = [(tone, r) for role, _, tone, r in out["lines"] if role == "agent"]
    assert all(tone is None and r is None for tone, r in agents)
    angry = [r for t, _, r in caller if "ridiculous" in t.lower()]
    assert angry and angry[0]["step"] >= 3, angry
    sent = [c for k, c, _ in out["events"] if k == "card_sent" and c.startswith("tone:")]
    assert sent, "no tone tip card was sent"
    assert out["bars"] >= 3
    chip = out["chip"].lower()  # the chip is uppercase in CSS
    assert "worried" in chip or "frustrated" in chip, out["chip"]
    assert "chip-warn" in out["chip_class"] or "chip-bad" in out["chip_class"]


async def test_tone_baseline_reader_off(schema, shots, monkeypatch):
    """Same call with TONE_READER=0: no readings, no tip, the chip stays a dash."""
    out = await run_call(schema, shots, monkeypatch, reader_on=False)
    report(out)
    assert all(r is None for _, _, _, r in out["lines"])
    assert out["tip_count"] == 0
    assert out["chip"].lower() == "mood –"
