"""Live Phase 4 exit (P4-6): each pack's trigger shows its card in the portal within 3 s
(DC-6), and the Ask bar answers "Show the last 3 claims" (AP-8).

Run locally with keys, a Postgres, a built portal (npm run build in portal/), and
Playwright's Chromium:
    PLAYWRIGHT_BROWSERS_PATH=... uv run --with playwright \\
        pytest -m live server/tests/test_phase4_exit_live.py -s -v

Needs DAILY_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL (a throwaway
schema per call is created and dropped). P4_SCREENSHOT_DIR (optional) keeps the
1600x1000 screenshots; P4_RESULTS (optional) appends one JSON line per call.

Three scripted calls, one per pack, each through the real server, bot, and portal:

- claims: Maria Alvarez, claims_status. Caller: "So why did my MRI claim get denied?"
  Expected: hero `nbs:retro_auth_review:CLM-900001`.
- payments: James Chen, account_payments. Caller: "...I was charged twice for my
  premium." Expected: hero `nbs:duplicate_premium_refund:<second draft>`.
- general: Linda Nguyen with a KB question, general_question. Caller: "So what is an
  explanation of benefits, exactly?" Expected: article tile `kb:KB-CL-010`.

For each: a daily-python caller finishes intake, Agent Ana's portal (headless Chromium)
accepts with Enter, then the caller says the trigger. A MutationObserver in the page
stamps the moment the expected card is on screen (`Date.now()`). The trigger word's end
in the played audio comes from Deepgram word timings on the exact PCM played. Both
clocks are this host's wall clock. DC-6 passes when card on screen minus trigger word
end is under 3 s.

On the claims call the agent then presses Cmd+K, types "Show the last 3 claims", and
Enter: the answer is a Claims card with its source, timed from Enter to on screen.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
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
from harness.intake_script import Persona, next_line, persona
from harness.participant import SAMPLE_RATE, HarnessParticipant
from harness.tts import synthesize
from harness.turns import wait_bot_turn

pytestmark = pytest.mark.live

REQUIRED = ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL")
AGENT_NAME = "Agent Ana"
MAX_TURNS = 14
DC6_SECS = 3.0
VIEWPORT = {"width": 1600, "height": 1000}
CHROMIUM_ARGS = [
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
]
VOICE_CALLER = "aura-2-arcas-en"
ASK_TEXT = "Show the last 3 claims"
ASK_SEL = "[data-testid^='tile-ask:']"

# Watches the assist column and stamps when a card matching a selector first shows.
WATCH = """
(sel) => {
  window.__seen = window.__seen || {};
  const check = () => {
    if (window.__seen[sel]) return;
    const el = document.querySelector(sel);
    if (el) window.__seen[sel] = { at: Date.now(), id: el.dataset.cardId || el.dataset.testid };
  };
  check();
  new MutationObserver(check).observe(document.body, {
    subtree: true, childList: true, attributes: true,
    attributeFilter: ['data-state', 'data-card-id', 'data-testid'],
  });
}
"""

FAKE_MIC = """
(() => {
  const orig = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = async (c) => {
    if (c && c.audio && !c.video) {
      const ctx = new AudioContext();
      const dest = ctx.createMediaStreamDestination();
      if (ctx.state !== 'running') await ctx.resume();
      return new MediaStream([dest.stream.getAudioTracks()[0].clone()]);
    }
    return orig(c);
  };
})();
"""


def _spelled_identity(p: Persona, spelled_last: str) -> str:
    """Spell the surname: intake STT mishearing names is the identity side card, not
    what this test measures (same as test_assist_live.py)."""
    return p.lines["identity"].replace(p.full_name, f"{p.full_name}, that's {spelled_last}", 1)


@dataclass(frozen=True)
class Scenario:
    key: str
    who: Persona
    lines: dict[str, str]
    pack: str
    trigger: str
    trigger_word: str
    # CSS selector of the expected card on screen, and the card id prefix it must have.
    selector: str
    card_prefix: str


MARIA = persona("maria_alvarez")
JAMES = persona("james_chen")
LINDA = persona("linda_nguyen")

SCENARIOS = [
    Scenario(
        key="claims",
        who=MARIA,
        lines=dict(MARIA.lines, identity=_spelled_identity(MARIA, "A L V A R E Z")),
        pack="claims_status",
        trigger="So why did my MRI claim get denied?",
        trigger_word="denied",
        selector="[data-testid='hero'][data-card-id^='nbs:retro_auth_review:']",
        card_prefix="nbs:retro_auth_review:CLM-900001",
    ),
    Scenario(
        key="payments",
        who=JAMES,
        lines=dict(JAMES.lines, identity=_spelled_identity(JAMES, "C H E N")),
        pack="account_payments",
        trigger="I looked at my bank account and I was charged twice for my premium.",
        trigger_word="twice",
        selector="[data-testid='hero'][data-card-id^='nbs:duplicate_premium_refund:']",
        card_prefix="nbs:duplicate_premium_refund:PAY-",
    ),
    Scenario(
        key="general",
        who=LINDA,
        lines=dict(
            LINDA.lines,
            identity=_spelled_identity(LINDA, "N G U Y E N"),
            reason=(
                "I got a paper in the mail from you, and I have a general question about "
                "what it means."
            ),
            claim_number="No, I don't have a number. It's just a question.",
            service_date="I'm not sure. It came in the mail last week.",
            claim_concern="Nothing is wrong, I just want to understand the paper.",
        ),
        pack="general_question",
        trigger="So what is an explanation of benefits, exactly?",
        trigger_word="benefits",
        selector="[data-testid='tile-kb:KB-CL-010']",
        card_prefix="kb:KB-CL-010",
    ),
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
    d = Path(os.getenv("P4_SCREENSHOT_DIR") or tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    return d


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def log(t0: float, msg: str) -> None:
    print(f"[{time.time() - t0:7.2f}s] {msg}", flush=True)


async def word_end(pcm: bytes, word: str) -> float:
    """Seconds into `pcm` where `word` ends, from Deepgram prerecorded word timings."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.deepgram.com/v1/listen",
            params={"model": "nova-3", "encoding": "linear16", "sample_rate": SAMPLE_RATE},
            headers={"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}"},
            content=pcm,
        )
    resp.raise_for_status()
    words = resp.json()["results"]["channels"][0]["alternatives"][0]["words"]
    hit = next(w for w in words if w["word"].strip(".,?!").lower() == word)
    return float(hit["end"])


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


async def run_intake(http, store, caller, rec, session_id, audio, after, who) -> dict | None:
    for _ in range(MAX_TURNS):
        turn = await wait_bot_turn(rec, after)
        if turn is None:
            break
        if q := await queued_call(http, session_id):
            return q
        key = next_line(*await intake_row(store, session_id), who)
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


async def snap_both(page, shots: Path, name: str, t0: float) -> list[str]:
    """Light then dark screenshots; leaves the page in light."""
    out = []
    for theme in ("light", "dark"):
        if theme == "dark":
            await set_theme(page, "dark")
        path = shots / f"{name}-{theme}.png"
        await page.screenshot(path=str(path), animations="disabled")
        out.append(path.name)
        log(t0, f"screenshot {path.name}")
    await set_theme(page, "light")
    return out


@pytest.mark.parametrize("sc", SCENARIOS, ids=[s.key for s in SCENARIOS])
async def test_pack_trigger_shows_its_card_within_3s(sc: Scenario, schema, shots, monkeypatch):
    from playwright.async_api import async_playwright, expect

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setenv(SERVER_URL_ENV, base)
    monkeypatch.setenv(DATABASE_SCHEMA_ENV, schema)
    # Hold music would read as bot speech to the RMS turn detection.
    monkeypatch.setenv("HOLD_MUSIC", "off")
    monkeypatch.delenv("QUEUE_PERSIST", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("INTAKE_PACK", raising=False)
    audio = {k: await synthesize(v, sample_rate=SAMPLE_RATE) for k, v in sc.lines.items()}
    trigger_pcm = await synthesize(sc.trigger, sample_rate=SAMPLE_RATE, voice=VOICE_CALLER)
    trigger_offset = await word_end(trigger_pcm, sc.trigger_word)
    launcher = BotLauncher()
    app.state.bot_launcher = launcher
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serve_task = asyncio.create_task(server.serve())
    daily = DailyRestClient.from_env()
    store = PostgresCallStore(os.environ["DATABASE_URL"], schema=schema)
    room_name = None
    t0 = time.time()
    result: dict = {"call": sc.key, "persona": sc.who.full_name, "pack": sc.pack}
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
            log(t0, f"{sc.key}: room {room_name}")

            async with HarnessParticipant("caller") as caller:
                await caller.join(out["room_url"], out["caller_token"])
                bot = await caller.wait_for_participant(BOT_NAME, timeout=120)
                rec = caller.recording_for(BOT_NAME)
                t_start = max(caller.joined_at, bot.joined_at)
                queued = await run_intake(
                    http, store, caller, rec, session_id, audio, t_start, sc.who
                )
                assert queued is not None, "intake never enqueued the call"
                log(t0, f"enqueued: identity={queued['identity_result']}")
                assert queued["identity_result"] == "verified"

                # ---- accept in the portal ---------------------------------------------
                await toggle.click()
                await expect(page.get_by_role("dialog", name="Incoming call")).to_be_visible(
                    timeout=5_000
                )
                accept_at = time.time()
                await page.keyboard.press("Enter")
                await expect(page.get_by_role("button", name="Mute")).to_be_enabled(timeout=30_000)
                hero = page.get_by_test_id("hero")
                await expect(hero).to_have_attribute("data-state", "listening")
                await caller.wait_for_participant(AGENT_NAME, timeout=15)
                await wait_bot_turn(rec, accept_at, start_timeout=10)
                plan = page.get_by_test_id("tile-plan")
                await expect(plan).to_be_visible(timeout=10_000)
                log(t0, "accepted; call plan on screen")

                # ---- the caller's trigger; the card on screen ---------------------------
                await page.evaluate(WATCH, sc.selector)
                start, end = await asyncio.wait_for(caller.play(trigger_pcm), 30)
                said_at = start + trigger_offset
                log(t0, f"caller: {sc.trigger!r} ({sc.trigger_word!r} at +{trigger_offset:.2f}s)")
                await page.wait_for_function(
                    "(sel) => window.__seen && window.__seen[sel]", arg=sc.selector, timeout=15_000
                )
                seen = await page.evaluate("(sel) => window.__seen[sel]", sc.selector)
                card_id = seen["id"].removeprefix("tile-")
                secs = seen["at"] / 1000 - said_at
                result |= {
                    "card": card_id,
                    "trigger_word": sc.trigger_word,
                    "trigger_word_to_card_secs": round(secs, 3),
                    "utterance_end_to_card_secs": round(seen["at"] / 1000 - end, 3),
                    "pass": secs < DC6_SECS,
                }
                log(t0, f"card {card_id} on screen {secs:.2f}s after the trigger word")
                await page.wait_for_timeout(2500)  # supporting cards land
                tiles = await page.locator(".shelf [data-testid^='tile-']").evaluate_all(
                    "(els) => els.map((e) => e.dataset.testid)"
                )
                marked = (
                    await page.get_by_role("complementary", name="Conversation")
                    .locator("mark")
                    .all_text_contents()
                )
                result |= {"shelf": tiles, "marked": marked}
                log(t0, f"shelf: {tiles}; marked: {marked}")
                taken += await snap_both(page, shots, f"p4-6-{sc.key}-card", t0)

                # ---- Ask bar on the claims call ------------------------------------------
                if sc.key == "claims":
                    ask = page.get_by_role("textbox", name="Ask")
                    await page.evaluate("document.activeElement && document.activeElement.blur()")
                    await page.keyboard.press("Meta+K")
                    await expect(ask).to_be_focused()
                    await ask.fill(ASK_TEXT)
                    await page.evaluate(WATCH, ASK_SEL)
                    ask_at = time.time()
                    await page.keyboard.press("Enter")
                    await page.wait_for_function(
                        "(sel) => window.__seen && window.__seen[sel]", arg=ASK_SEL, timeout=15_000
                    )
                    seen = await page.evaluate("(sel) => window.__seen[sel]", ASK_SEL)
                    ask_tile = page.locator(f"[data-testid='{seen['id']}']")
                    await expect(ask_tile).to_contain_text(f"You asked “{ASK_TEXT}”")
                    await expect(ask_tile.get_by_test_id("tile-source")).to_contain_text(
                        "Claims system"
                    )
                    ask_ids = await ask_tile.get_by_test_id("tile-source").get_attribute("title")
                    big = await ask_tile.get_by_test_id("tile-big").inner_text()
                    result["ask"] = {
                        "text": ASK_TEXT,
                        "card": seen["id"].removeprefix("tile-"),
                        "enter_to_card_secs": round(seen["at"] / 1000 - ask_at, 3),
                        "big": big,
                        "records": ask_ids,
                    }
                    log(t0, f"ask answered: {result['ask']}")
                    await page.wait_for_timeout(400)
                    taken += await snap_both(page, shots, "p4-6-ask-claims", t0)

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
    for kind, cid, data in events:
        print(f"  {kind:10} {cid or '-':45} {json.dumps(data)}")
    sent = {cid: data for kind, cid, data in events if kind == "card_sent"}
    if card_id in sent:
        result["bot_line_to_send_ms"] = sent[card_id]["latency_ms"]
    result["pack_seen"] = sorted({d.get("pack_id") for d in sent.values()})
    result["screenshots"] = taken
    print("RESULT " + json.dumps(result))
    if os.getenv("P4_RESULTS"):
        with open(os.environ["P4_RESULTS"], "a") as f:
            f.write(json.dumps(result) + "\n")

    assert card_id.startswith(sc.card_prefix), card_id
    assert sent.get(card_id, {}).get("pack_id") == sc.pack, sent.get(card_id)
    assert result["trigger_word_to_card_secs"] < DC6_SECS, result
    if sc.key == "claims":
        ask_card = result["ask"]["card"]
        assert sent[ask_card]["ask"] is True
        assert "CLM-900001" in (result["ask"]["records"] or "")
