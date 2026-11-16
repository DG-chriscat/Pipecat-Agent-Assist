"""Live P3-4: after accept, the bot streams a labeled transcript to the agent's portal only.

Run locally with keys, a Postgres, a built portal (npm run build in portal/), and
Playwright's Chromium:
    PLAYWRIGHT_BROWSERS_PATH=... uv run --with playwright \\
        pytest -m live server/tests/test_live_transcript_live.py -s -v

Needs DAILY_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL.
P3_SCREENSHOT_DIR (optional) keeps the 1600x1000 mid call screenshot.

Same setup as test_portal_call_live.py: the real server starts the bot, a daily-python
caller (S1 harness) finishes intake as Maria Lopez, the built portal in headless
Chromium goes available and accepts with Enter. Then caller and agent alternate 6
turns. The caller talks through the harness; the agent talks through the portal's own
microphone: getUserMedia is stubbed with a WebAudio stream so the test can play
Deepgram TTS into it on cue (a real browser send path, scripted words).

Asserts:
- every final line in the portal has the role of the person who said it (matched by
  words and time only, never by the label);
- every turn produced at least one final line;
- the caller's Daily client never received a transcript message (nor any RTVI user
  transcription after accept);
- the final lines are saved to transcript_lines for the call.
Prints each line with its latency: end of speech to the final line on screen.
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
from dataclasses import dataclass
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
from harness.audio import find_speech_segments
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
GAP_SECS = 1.2
SETTLE_SECS = 4.0  # wait for a turn's last final line before the next turn

# Alternating turns after accept. Words differ between roles so a mislabeled line
# shows up as the wrong person's words.
TURNS: list[tuple[str, str]] = [
    ("caller", "Hi, I already called last week about my MRI claim."),
    ("agent", "Thanks for holding, Maria. I can see that claim right here."),
    ("caller", "Nobody told me it would get denied. I have the referral."),
    ("agent", "It looks like the prior authorization was not on file."),
    ("caller", "So what do I do now? I cannot pay twelve hundred dollars."),
    ("agent", "Okay, there are two ways we can handle this together."),
]

# Replaces the portal's microphone with a WebAudio stream the test can speak into.
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
    const start = Date.now();
    src.onended = () => resolve({ start: start / 1000, end: Date.now() / 1000 });
    src.start();
  });
})();
"""

# Records the moment each transcript row in the portal turns final, and every interim
# text a row shows before that.
WATCH_FINALS = """
() => {
  window.__finals = [];
  window.__interims = [];
  const seen = new Set();
  const scan = () => {
    document.querySelectorAll('li.tr[aria-busy="true"]').forEach((li) => {
      const key = 'i|' + li.dataset.lineId + '|' + li.querySelector('p').textContent;
      if (seen.has(key)) return;
      seen.add(key);
      window.__interims.push({line_id: li.dataset.lineId, text: li.querySelector('p').textContent});
    });
    document.querySelectorAll('li.tr[aria-busy="false"]').forEach((li) => {
      const id = li.dataset.lineId;
      const text = li.querySelector('p').textContent;
      const key = id + '|' + text;
      if (seen.has(key)) return;
      seen.add(key);
      window.__finals.push({
        line_id: id, role: li.classList.contains('tr-caller') ? 'caller' : 'agent',
        label: li.querySelector('.who').textContent, text, t: Date.now() / 1000,
      });
    });
  };
  new MutationObserver(scan).observe(document.body, {subtree: true, childList: true,
    characterData: true, attributes: true, attributeFilter: ['aria-busy']});
  scan();
}
"""


@dataclass
class Spoken:
    role: str
    text: str
    start: float
    end: float
    trailing_silence: float

    @property
    def speech_end(self) -> float:
        return self.end - self.trailing_silence


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


def trailing_silence(pcm: bytes) -> float:
    chunk = SAMPLE_RATE * 2 * 20 // 1000
    chunks = [(i / 2 / SAMPLE_RATE, pcm[i : i + chunk]) for i in range(0, len(pcm), chunk)]
    segs = find_speech_segments(chunks, threshold=300.0, sample_rate=SAMPLE_RATE)
    return len(pcm) / 2 / SAMPLE_RATE - segs[-1][1] if segs else 0.0


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z']+", text.lower()))


def attribute(line: dict, spoken: list[Spoken]) -> Spoken | None:
    """The turn this line came from, by words and arrival time only (never its label)."""
    best, best_score = None, 0.0
    for s in spoken:
        if not (s.start - 0.5 <= line["t"] <= s.end + SETTLE_SECS):
            continue
        score = len(words(line["text"]) & words(s.text)) / max(len(words(line["text"])), 1)
        if score > best_score:
            best, best_score = s, score
    return best


def is_transcript_message(msg) -> bool:
    if not isinstance(msg, dict):
        return False
    data = msg.get("data")
    return (
        msg.get("type") == "server-message"
        and isinstance(data, dict)
        and (data.get("type") == "transcript")
    )


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


async def test_live_transcript_reaches_the_agent_only(schema, shots, monkeypatch):
    from playwright.async_api import async_playwright, expect

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setenv(SERVER_URL_ENV, base)
    # Hold music (QH-5) would read as bot speech to the RMS turn detection.
    monkeypatch.setenv("HOLD_MUSIC", "off")
    monkeypatch.setenv(DATABASE_SCHEMA_ENV, schema)
    monkeypatch.delenv("QUEUE_PERSIST", raising=False)
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
    spoken: list[Spoken] = []
    finals: list[dict] = []
    interims: list[dict] = []
    t0 = time.time()
    try:
        while not server.started:
            await asyncio.sleep(0.05)
        async with httpx.AsyncClient(base_url=base, timeout=30) as http, async_playwright() as pw:
            browser = await pw.chromium.launch(args=CHROMIUM_ARGS)
            ctx = await browser.new_context(viewport=VIEWPORT, permissions=["microphone"])
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

                await toggle.click()
                await expect(page.get_by_role("dialog", name="Incoming call")).to_be_visible(
                    timeout=5_000
                )
                # The card's key listener attaches in an effect just after it paints.
                await page.wait_for_timeout(1000)
                accept_at = time.time()
                await page.keyboard.press("Enter")
                await expect(page.get_by_role("button", name="Mute")).to_be_enabled(timeout=30_000)
                log(t0, f"accepted; portal joined {time.time() - accept_at:.2f}s after Enter")
                await caller.wait_for_participant(AGENT_NAME, timeout=15)
                # "Connecting you now." plays, then the bot is quiet.
                await wait_bot_turn(rec, accept_at, start_timeout=10)
                listen_from = time.time()
                await page.evaluate(WATCH_FINALS)
                log(t0, "bot said its accept line; turns start")

                # ---- 6 alternating turns -------------------------------------------
                for i, ((role, text), pcm) in enumerate(zip(TURNS, turn_audio, strict=True), 1):
                    if role == "caller":
                        start, end = await asyncio.wait_for(caller.play(pcm), 60)
                    else:
                        b64 = base64.b64encode(pcm).decode()
                        play = asyncio.create_task(
                            page.evaluate("([b, r]) => window.__speak(b, r)", [b64, SAMPLE_RATE])
                        )
                        if i == len(TURNS):
                            # Mid sentence: the agent's line is still interim (caret). Wait
                            # for a few words; freeze the blinking caret in its "on" state.
                            busy = page.locator('li.tr-agent[aria-busy="true"]').filter(
                                has_text=re.compile(r"\w+ \w+ \w+")
                            )
                            await expect(busy).to_have_count(1, timeout=5_000)
                            shown = await page.evaluate(
                                "[...document.querySelectorAll('li.tr[aria-busy=\"true\"] p')]"
                                ".map((p) => p.textContent)"
                            )
                            await page.screenshot(
                                path=str(shots / "p3-4-live-transcript.png"), animations="disabled"
                            )
                            log(t0, f"mid call screenshot: interim {shown!r}")
                        played = await play
                        start, end = played["start"], played["end"]
                    spoken.append(Spoken(role, text, start, end, trailing_silence(pcm)))
                    log(t0, f"turn {i} {role:6} played {end - start:.2f}s: {text}")
                    await asyncio.sleep(SETTLE_SECS if i == len(TURNS) else GAP_SECS)
                await page.wait_for_timeout(1500)
                finals = await page.evaluate("window.__finals")
                interims = await page.evaluate("window.__interims")
                await page.screenshot(path=str(shots / "p3-4-live-transcript-end.png"))

                # ---- end: agent leaves, then the caller hangs up -----------------------
                await page.get_by_role("button", name="End call").click()
                await caller.wait_for_participant_left(AGENT_NAME, timeout=15)
                caller_messages = list(caller.app_messages)
                await browser.close()

            await asyncio.wait_for(proc.wait(), 30)
            log(t0, f"caller left; bot exited returncode={proc.returncode}")
            cur = await store._execute(
                "SELECT t.speaker_role, t.text FROM transcript_lines t "
                "JOIN calls c USING (call_id) WHERE c.session_id = %s ORDER BY t.ts",
                (session_id,),
            )
            saved = await cur.fetchall()
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

    # ---- report ------------------------------------------------------------------------
    t_first = spoken[0].start
    print("\nFinal lines in the portal (arrival order):")
    rows = []
    for line in finals:
        src = attribute(line, spoken)
        rows.append((line, src))
        lat = f"{line['t'] - src.speech_end:5.2f}s" if src else "   - "
        print(
            f"  +{line['t'] - t_first:6.2f}s [{line['label']:6}] {line['line_id']:9} "
            f"{line['text']!r}  <- {src.role if src else '?'}  latency {lat}"
        )
    per_turn = []
    for i, s in enumerate(spoken, 1):
        mine = [ln for ln, src in rows if src is s]
        lat = max(ln["t"] for ln in mine) - s.speech_end if mine else None
        per_turn.append(lat)
        print(f"  turn {i} {s.role:6} end-of-speech -> last final on screen: {lat}")
    print(f"interim updates rendered in the portal: {len(interims)}")
    for x in interims:
        print(f"  ~ {x['line_id']:9} {x['text']!r}")
    caller_rtvi = [m for _, _, m in caller_messages if isinstance(m, dict) and "label" in m]
    after_accept = [(t, m) for t, _, m in caller_messages if t >= listen_from]
    print(f"caller app messages: {len(caller_messages)} total, {len(after_accept)} after accept")
    print(f"caller RTVI message types: {sorted({m.get('type') for m in caller_rtvi})}")
    print(f"saved transcript_lines: {len(saved)}")
    for role, text in saved:
        print(f"  [{role}] {text}")
    print(
        "RESULT "
        + json.dumps(
            {
                "lines": len(finals),
                "per_turn_latency_secs": [round(x, 3) if x else None for x in per_turn],
                "saved": len(saved),
                "caller_transcript_messages": sum(
                    is_transcript_message(m) for _, _, m in caller_messages
                ),
            }
        )
    )
    print(f"SCREENSHOTS {shots}")

    # ---- assertions ----------------------------------------------------------------------
    assert finals, "no final transcript lines reached the portal"
    for line, src in rows:
        assert src is not None, f"line not from any turn: {line}"
        assert line["role"] == src.role, f"wrong role: {line} came from {src.role}"
        assert line["label"] == ("Caller" if src.role == "caller" else "You")
    assert all(x is not None for x in per_turn), f"a turn produced no final line: {per_turn}"
    assert interims, "no interim line was rendered before its final"
    assert not any(is_transcript_message(m) for _, _, m in caller_messages)
    assert not [
        m for t, m in after_accept if isinstance(m, dict) and m.get("type") == "user-transcription"
    ], "the caller got RTVI user transcription after accept"
    assert [(r, t) for r, t in saved] == [(ln["role"], ln["text"]) for ln in finals]
