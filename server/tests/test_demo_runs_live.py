"""Live P5-4: ten scripted demo calls through the full loop, scored against the PRD
success measures (docs/demo.md).

Run locally with keys, a Postgres, a built portal (npm run build in portal/), and
Playwright's Chromium:
    PLAYWRIGHT_BROWSERS_PATH=... DEMO_RESULTS=/tmp/demo.jsonl uv run --with playwright \\
        pytest -m live server/tests/test_demo_runs_live.py -s -v

Needs DAILY_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL (a throwaway
schema per call is created and dropped). DEMO_RESULTS appends one JSON line per run;
DEMO_RUNS (optional, e.g. "1,4") picks runs. DEMO_SCREENSHOT_DIR keeps screenshots.

Each run is one call through the real server, bot, and portal, with the Settings
defaults (P5-7 STT, P5-8 voice, P5-9 LLMs):

1. A daily-python caller answers intake as the persona. Intake time runs from the bot
   joining to the call waiting in the queue. Each bot response time (caller line end
   to the bot's next audio) is kept too.
2. Agent Ana's portal (headless Chromium) goes available and presses Enter while the
   caller is talking: Accept to hearing the caller is Enter to the caller's audio
   first playing in the page (same probe as test_wrap_up_live.py).
3. The caller says the trigger. Trigger word end (Deepgram word timings on the played
   PCM) to the expected card on screen (MutationObserver) is the card time.
4. Verify hero runs (IN-13): Linda heard as "Yuen" gets Mark verified and her records
   unlock; Daniel (wrong DOB) gets Could not verify and records stay hidden.
5. End, the drafted summary lands in wrap up, Save and go available, "Call saved".

Full loop = all five steps. Run 1 first changes the greeting in Settings and checks
the bot says the new one (the demo script does the same).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
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
from harness.turns import audio_slice, transcribe, wait_bot_turn


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # its dataclasses look the module up
    spec.loader.exec_module(mod)
    return mod


# Shared live helpers: throwaway schema, portal page script (fake mic + caller audio
# probe), trigger word timing.
_p4 = _load("test_phase4_exit_live")
_wu = _load("test_wrap_up_live")
keys_present, schema = _p4.keys_present, _p4.schema
free_port, log, intake_row, queued_call = _p4.free_port, _p4.log, _p4.intake_row, _p4.queued_call
set_theme, trigger_audio, WATCH = _p4.set_theme, _p4.trigger_audio, _p4.WATCH
spelled = _p4._spelled_identity
PAGE_SCRIPT, CHROMIUM_ARGS, VIEWPORT = _wu.PAGE_SCRIPT, _wu.CHROMIUM_ARGS, _wu.VIEWPORT

pytestmark = pytest.mark.live

AGENT_NAME = "Agent Ana"
MAX_TURNS = 16
# Said by the caller while the agent presses Enter (no trigger words in it).
PICKUP_LINE = "Hello? Is someone there now? Okay, good, thank you for picking up."
DEMO_GREETING = (
    "Welcome to the Deepgram Vitals demo line. You're speaking with an AI assistant. "
    "I'll ask a few quick questions, and then a person will join to help you."
)
GREETING_MARK = "demo line"  # not in the default greeting
LOCKED = ".shelf [data-locked='true']"

MARIA = persona("maria_alvarez")
JAMES = persona("james_chen")
AISHA = persona("aisha_patel")
LINDA = persona("linda_nguyen")
DANIEL = persona("daniel_okafor")

# Linda as STT often hears her in the identity spike: "Yuen" (close match, IN-6).
LINDA_YUEN = Persona(
    "linda_yuen",
    "Linda Yuen",
    LINDA.dob,
    LINDA.member_id,
    dict(
        LINDA.lines,
        identity=(
            "Sure. My name is Linda Yuen. My date of birth is January thirtieth, nineteen "
            "fifty eight. And my member ID is E V M, one zero zero, two three four."
        ),
        fix_name="No, my name is Linda Yuen.",
        fix_both=(
            "No. My name is Linda Yuen, and my member ID is E V M, one zero zero, two three four."
        ),
    ),
)
LINDA_GENERAL = dict(
    LINDA.lines,
    identity=spelled(LINDA, "N G U Y E N"),
    reason=(
        "I got a paper in the mail from you, and I have a general question about what it means."
    ),
    claim_number="No, I don't have a number. It's just a question.",
    service_date="I'm not sure. It came in the mail last week.",
    claim_concern="Nothing is wrong, I just want to understand the paper.",
)


@dataclass(frozen=True)
class Run:
    n: int
    who: Persona  # the persona the script follows (Linda "Yuen" for the hard name)
    real: Persona  # who the caller really is
    lines: dict[str, str]
    pack: str
    trigger: str
    trigger_word: str
    selector: str  # the card that must show for the trigger
    identity_expected: tuple[str, ...] = ("verified",)
    verify: str | None = None  # "verified" | "not_verified": the agent's decision
    unlock_text: str | None = None  # on the shelf after Mark verified
    settings: dict = field(default_factory=dict)


def _hero(prefix: str) -> str:
    return f"[data-testid='hero'][data-card-id^='{prefix}']"


RUNS = [
    Run(
        1,
        MARIA,
        MARIA,
        dict(MARIA.lines, identity=spelled(MARIA, "A L V A R E Z")),
        "claims_status",
        "So why did my MRI claim get denied?",
        "denied",
        _hero("nbs:retro_auth_review:CLM-900001"),
        settings={"greeting_text": DEMO_GREETING},
    ),
    Run(
        2,
        JAMES,
        JAMES,
        dict(JAMES.lines, identity=spelled(JAMES, "C H E N")),
        "account_payments",
        "I looked at my bank account and I was charged twice for my premium.",
        "twice",
        _hero("nbs:duplicate_premium_refund:"),
    ),
    Run(
        3,
        AISHA,
        AISHA,
        dict(AISHA.lines, identity=spelled(AISHA, "P A T E L")),
        "claims_status",
        "Why is my heart test claim still pending?",
        "pending",
        _hero("nbs:provider_info_follow_up:CLM-900003"),
    ),
    Run(
        4,
        LINDA_YUEN,
        LINDA,
        LINDA_YUEN.lines,
        "claims_status",
        "So why was my dermatology claim denied?",
        "denied",
        LOCKED,
        identity_expected=("close_match", "not_verified"),
        verify="verified",
        unlock_text="CLM-900005",
    ),
    Run(
        5,
        DANIEL,
        DANIEL,
        dict(DANIEL.lines, identity=spelled(DANIEL, "O K A F O R")),
        "claims_status",
        "Has the prior authorization for my knee surgery been approved yet?",
        "authorization",
        LOCKED,
        identity_expected=("not_verified",),
        verify="not_verified",
    ),
    Run(
        6,
        LINDA,
        LINDA,
        LINDA_GENERAL,
        "general_question",
        "So what is an explanation of benefits, exactly?",
        "benefits",
        "[data-testid='tile-kb:KB-CL-010']",
    ),
    Run(
        7,
        MARIA,
        MARIA,
        dict(MARIA.lines, identity=spelled(MARIA, "A L V A R E Z")),
        "claims_status",
        "So why did my MRI claim get denied?",
        "denied",
        _hero("nbs:retro_auth_review:CLM-900001"),
    ),
    Run(
        8,
        JAMES,
        JAMES,
        dict(JAMES.lines, identity=spelled(JAMES, "C H E N")),
        "account_payments",
        "I looked at my bank account and I was charged twice for my premium.",
        "twice",
        _hero("nbs:duplicate_premium_refund:"),
    ),
    Run(
        9,
        AISHA,
        AISHA,
        dict(AISHA.lines, identity=spelled(AISHA, "P A T E L")),
        "claims_status",
        "Why is my heart test claim still pending?",
        "pending",
        _hero("nbs:provider_info_follow_up:CLM-900003"),
    ),
    Run(
        10,
        LINDA,
        LINDA,
        LINDA_GENERAL,
        "general_question",
        "So what is an explanation of benefits, exactly?",
        "benefits",
        "[data-testid='tile-kb:KB-CL-010']",
    ),
]


def _selected() -> list[Run]:
    want = os.getenv("DEMO_RUNS")
    if not want:
        return RUNS
    keep = {int(x) for x in want.split(",") if x.strip()}
    return [r for r in RUNS if r.n in keep]


async def run_intake(http, store, caller, rec, session_id, audio, after, who, gaps) -> dict | None:
    """Answer each bot turn with the script line for the next missing field. `gaps`
    gets the bot response time after each caller line (line end to bot audio)."""
    first = True
    for _ in range(MAX_TURNS):
        turn = await wait_bot_turn(rec, after)
        if turn is None:
            break
        if not first:
            gaps.append(round(turn[0] - after, 3))
        first = False
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


async def first_bot_line(rec, after: float) -> str:
    turn = await wait_bot_turn(rec, after)
    if turn is None:
        return ""
    return await transcribe(audio_slice(rec, *turn))


@pytest.mark.parametrize("run", _selected(), ids=[f"run{r.n}" for r in _selected()])
async def test_demo_run(run: Run, schema, tmp_path, monkeypatch):
    from playwright.async_api import async_playwright, expect

    shots = Path(os.getenv("DEMO_SCREENSHOT_DIR") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setenv(SERVER_URL_ENV, base)
    monkeypatch.setenv(DATABASE_SCHEMA_ENV, schema)
    monkeypatch.setenv("QUEUE_PERSIST", "1")  # wrap up lands on the calls row
    monkeypatch.setenv("HOLD_MUSIC", "off")  # music would read as bot speech
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.delenv("INTAKE_PACK", raising=False)
    monkeypatch.delenv("SETTINGS_PERSIST", raising=False)
    audio = {k: await synthesize(v, sample_rate=SAMPLE_RATE) for k, v in run.lines.items()}
    pickup_pcm = await synthesize(PICKUP_LINE, sample_rate=SAMPLE_RATE)
    trigger_pcm, trigger_offset = await trigger_audio(run.trigger, run.trigger_word)
    for attr in ("bot_launcher", "queue_service", "caller_history", "settings_store"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)
    launcher = BotLauncher()
    app.state.bot_launcher = launcher
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serve_task = asyncio.create_task(server.serve())
    daily = DailyRestClient.from_env()
    store = PostgresCallStore(os.environ["DATABASE_URL"], schema=schema)
    room_name = None
    t0 = time.time()
    r: dict = {
        "run": run.n,
        "persona": run.real.full_name
        + (f' (says "{run.who.full_name}")' if run.who != run.real else ""),
        "pack": run.pack,
        "identity_expected": list(run.identity_expected),
        "trigger": run.trigger,
        "full_loop": False,
    }
    gaps: list[float] = []
    try:
        while not server.started:
            await asyncio.sleep(0.05)
        async with httpx.AsyncClient(base_url=base, timeout=30) as http, async_playwright() as pw:
            settings = (await http.get("/settings")).json()["values"]
            if run.settings:
                # Settings saves the whole form, as the portal page does.
                resp = await http.put(
                    "/settings",
                    json={"values": {**settings, **run.settings}, "agent": AGENT_NAME},
                )
                assert resp.status_code == 200, resp.text
                settings = resp.json()["values"]
            r["settings"] = {
                k: settings.get(k)
                for k in (
                    "tts_model",
                    "tts_voice",
                    "stt_intake_model",
                    "stt_assist_model",
                    "llm_intake",
                    "llm_assist",
                    "llm_wrapup",
                )
            }
            browser = await pw.chromium.launch(args=CHROMIUM_ARGS)
            ctx = await browser.new_context(
                viewport=VIEWPORT, permissions=["microphone"], color_scheme="light"
            )
            await ctx.add_init_script(PAGE_SCRIPT)
            page = await ctx.new_page()
            page.on("console", lambda m: m.type == "error" and print(f"  [portal] {m.text}"))
            await page.goto(f"{base}/agent/")
            await page.get_by_label("Your name").fill(AGENT_NAME)
            await page.get_by_role("button", name="Sign in").click()
            toggle = page.get_by_role("switch", name="Available")
            await expect(toggle).to_be_enabled(timeout=10_000)

            resp = await http.post("/calls/browser")
            assert resp.status_code == 200, resp.text
            out = resp.json()
            session_id = out["session_id"]
            room_name = out["room_url"].rsplit("/", 1)[-1]
            proc = launcher.processes[session_id]
            log(t0, f"run {run.n}: {r['persona']} room {room_name}")

            async with HarnessParticipant("caller") as caller:
                await caller.join(out["room_url"], out["caller_token"])
                bot = await caller.wait_for_participant(BOT_NAME, timeout=120)
                rec = caller.recording_for(BOT_NAME)
                t_start = max(caller.joined_at, bot.joined_at)
                if run.settings.get("greeting_text"):
                    heard = await first_bot_line(rec, t_start)
                    r["greeting_heard"] = heard
                    log(t0, f"greeting heard: {heard!r}")
                queued = await run_intake(
                    http, store, caller, rec, session_id, audio, t_start, run.who, gaps
                )
                r["bot_response_secs"] = gaps
                r["bot_response_median_secs"] = statistics.median(gaps) if gaps else None
                assert queued is not None, "intake never enqueued the call"
                r["intake_secs"] = round(time.time() - t_start, 2)
                r["identity_result"] = queued["identity_result"]
                intake, _ = await intake_row(store, session_id)
                r["heard_name"] = (intake or {}).get("full_name")
                log(t0, f"enqueued after {r['intake_secs']}s: identity={r['identity_result']}")

                # ---- ring; Enter while the caller talks ------------------------------
                await toggle.click()
                await expect(page.get_by_role("dialog", name="Incoming call")).to_be_visible(
                    timeout=10_000
                )
                await page.wait_for_timeout(1000)  # key listener attaches just after paint
                caller_id = caller._client.participants()["local"]["id"]
                talking = asyncio.create_task(caller.play(pickup_pcm))
                await asyncio.sleep(0.3)
                await page.evaluate("document.activeElement && document.activeElement.blur()")
                accept_at = time.time()
                await page.keyboard.press("Enter")
                await expect(page.get_by_role("button", name="Mute")).to_be_enabled(timeout=30_000)
                heard_at = None
                end = time.time() + 15
                while heard_at is None and time.time() < end:
                    heard_at = await page.evaluate(
                        "(id) => window.__firstAudio[id] || null", caller_id
                    )
                    if heard_at is None:
                        await asyncio.sleep(0.05)
                assert heard_at is not None, "the portal never played the caller's audio"
                r["accept_to_caller_audio_secs"] = round(heard_at - accept_at, 3)
                marks = await page.evaluate(
                    "() => { const o = performance.timeOrigin; return performance"
                    ".getEntriesByType('mark').filter((m) => m.name.startsWith('vitals:'))"
                    ".map((m) => [m.name.slice(7), (o + m.startTime) / 1000]); }"
                )
                r["accept_timeline"] = {
                    n: round(t - accept_at, 2) for n, t in marks if t >= accept_at - 1
                }
                log(t0, f"caller heard {r['accept_to_caller_audio_secs']:.2f}s after Enter")
                await asyncio.wait_for(talking, 30)
                hero = page.get_by_test_id("hero")
                if run.verify:
                    await expect(hero).to_have_attribute("data-state", "verify", timeout=10_000)
                await caller.wait_for_participant(AGENT_NAME, timeout=15)
                await wait_bot_turn(rec, accept_at, start_timeout=10)

                # ---- trigger -> card on screen ----------------------------------------
                await page.evaluate(WATCH, run.selector)
                start, _ = await asyncio.wait_for(caller.play(trigger_pcm), 30)
                said_at = start + trigger_offset
                await page.wait_for_function(
                    "(sel) => window.__seen && window.__seen[sel]", arg=run.selector, timeout=15_000
                )
                seen = await page.evaluate("(sel) => window.__seen[sel]", run.selector)
                r["card"] = (seen.get("id") or "").removeprefix("tile-") or run.selector
                r["trigger_word_to_card_secs"] = round(seen["at"] / 1000 - said_at, 3)
                log(t0, f"card {r['card']} {r['trigger_word_to_card_secs']:.2f}s after the word")
                await page.wait_for_timeout(2500)  # supporting cards land
                r["shelf"] = await page.locator(".shelf > [data-testid^='tile-']").evaluate_all(
                    "(els) => els.map((e) => e.dataset.testid)"
                )
                await page.screenshot(path=str(shots / f"demo-run{run.n}-card-light.png"))

                # ---- the agent's identity decision (IN-13) ----------------------------
                if run.verify == "verified":
                    await hero.get_by_role("button", name="Mark verified").click()
                    await expect(page.locator(LOCKED)).to_have_count(0, timeout=5_000)
                    await expect(page.locator(".shelf")).to_contain_text(
                        run.unlock_text or "", timeout=5_000
                    )
                    r["agent_decision"] = "verified (records unlocked)"
                    await page.screenshot(path=str(shots / f"demo-run{run.n}-unlocked-light.png"))
                elif run.verify == "not_verified":
                    await hero.get_by_role("button", name="Could not verify").click()
                    await expect(page.locator(LOCKED).first).to_be_visible()
                    r["agent_decision"] = "could not verify (records stay locked)"
                log(t0, f"agent decision: {r.get('agent_decision', '-')}")

                # ---- End -> drafted summary -> Save ------------------------------------
                await asyncio.sleep(2.0)  # last finals land
                end_at = time.time()
                await page.get_by_role("button", name="End call").click()
                view = page.get_by_role("region", name="Wrap up")
                await expect(view).to_be_visible(timeout=15_000)
                box = view.get_by_label("Summary", exact=True)
                await expect(box).not_to_have_value("", timeout=40_000)
                r["end_to_draft_secs"] = round(time.time() - end_at, 2)
                r["summary"] = await box.input_value()
                await view.get_by_role("button", name="Save and go available").click()
                await expect(page.get_by_role("status")).to_contain_text(
                    "Call saved", timeout=10_000
                )
                r["wrap_up_saved"] = True
                log(t0, f"draft {r['end_to_draft_secs']}s after End; saved")
                await caller.wait_for_participant_left(AGENT_NAME, timeout=15)
                await browser.close()
            await asyncio.wait_for(proc.wait(), 30)
            cur = await store._execute(
                "SELECT wrap_up_reason, identity_decision FROM calls WHERE session_id = %s",
                (session_id,),
            )
            row = await cur.fetchone()
            r["wrap_up_reason"] = row[0] if row else None
            r["full_loop"] = True
    except BaseException as e:  # record the miss, then fail the run
        r["error"] = f"{type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}"
        raise
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
        for attr in ("bot_launcher", "queue_service", "caller_history", "settings_store"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)
        print("RESULT " + json.dumps(r))
        if os.getenv("DEMO_RESULTS"):
            with open(os.environ["DEMO_RESULTS"], "a") as f:
                f.write(json.dumps(r) + "\n")

    assert r["identity_result"] in run.identity_expected, r
    if run.settings.get("greeting_text"):
        assert GREETING_MARK in r["greeting_heard"].lower(), r["greeting_heard"]
