"""Live P5-11: Maria Lopez's caller talk track end to end, every expect checkpoint.

Run locally with keys, a Postgres, a built portal (npm run build in portal/), and
Playwright's Chromium:
    PLAYWRIGHT_BROWSERS_PATH=... uv run --with playwright \\
        pytest -m live server/tests/test_talk_track_live.py -s -v

Needs DAILY_API_KEY, DEEPGRAM_API_KEY, ANTHROPIC_API_KEY, DATABASE_URL (a throwaway
schema per call is created and dropped). TALK_TRACK_SCREENSHOT_DIR keeps screenshots;
TALK_TRACK_RESULTS appends the JSON result line.

The harness caller says every beat of Maria's talk track from demo_personas.yaml,
optional beats included, in order: the intake lines to the bot (picked per bot turn
from the calls row, as in the P5-4 runs), then each agent and closing beat. Agent Ana
(headless Chromium, fake microphone) is a test agent, not a script: she accepts the
call with Enter, answers in a few short lines so the agent side of the transcript
exists, types the Ask bar question from the ask beat, presses Accept on the hero in
the closing beat (the talk track's rule), then End and Save.

After each beat every expect item of that beat must show on the agent's screen. Each
item maps to a check below (a test fails if an item has none). The time from the end
of the beat's caller line to the check passing is recorded per item.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from agent_assist_bot.bot import BOT_NAME
from agent_assist_bot.calls import DATABASE_SCHEMA_ENV, PostgresCallStore
from agent_assist_bot.handoff import SERVER_URL_ENV
from agent_assist_bot.packs import load_packs
from agent_assist_server import demo_personas
from agent_assist_server.app import app
from agent_assist_server.bot_launcher import BotLauncher
from agent_assist_server.daily import DailyRestClient
from harness.intake_script import Persona, persona
from harness.participant import SAMPLE_RATE, HarnessParticipant
from harness.tts import synthesize


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_dr = _load("test_demo_runs_live")
keys_present, schema = _dr.keys_present, _dr.schema
free_port, log, run_intake = _dr.free_port, _dr.log, _dr.run_intake
PAGE_SCRIPT, CHROMIUM_ARGS, VIEWPORT = _dr.PAGE_SCRIPT, _dr.CHROMIUM_ARGS, _dr.VIEWPORT
AGENT_NAME, PICKUP_LINE = _dr.AGENT_NAME, _dr.PICKUP_LINE
wait_bot_turn = _dr.wait_bot_turn

pytestmark = pytest.mark.live

KEY = "maria_lopez"
MARIA = next(p for p in demo_personas.load() if p["key"] == KEY)
VOICE_CALLER = "aura-2-arcas-en"
VOICE_AGENT = "aura-2-thalia-en"
CHECK_SECS = 20.0

# Agent Ana's own lines (the agent stays unscripted in a real demo; these only give the
# transcript an agent side). AGENT_GREETING when she picks up; AGENT_SAYS[i] after the
# caller's beat with this index (0 based).
AGENT_GREETING = -1
AGENT_SAYS = {
    AGENT_GREETING: "Hi Maria, thanks for holding. Let me pull up your claim.",
    10: "The reason is there was no authorization on file, so it was denied because of that.",
}


def _intake_lines() -> dict[str, str]:
    """The harness intake keys, from the talk track's intake beats."""
    by_step = {b["step"]: b["say"] for b in MARIA["talk_track"] if b["phase"] == "intake"}
    base = persona(KEY).lines
    plain = "No, my name is Maria Lopez."
    return {
        **base,
        "identity": by_step["identity"],
        "confirm_yes": by_step["confirm"],
        # Plain names, never spelled (P5-4): the fix lines say the name as is.
        "fix_name": plain,
        "fix_both": f"{plain} {base['fix_member_id'].removeprefix('No, ')}",
        "reason": by_step["reason"],
        "claim_number": by_step["claim_number"],
        "service_date": by_step["service_date"],
        "claim_concern": by_step["claim_concern"],
        "readback": by_step["readback"],
    }


def _plan_done(label: str) -> str:
    return (
        "(() => [...document.querySelectorAll('[data-testid=\"tile-plan\"] li.is-done')]"
        f".some((li) => li.textContent.includes({json.dumps(label)})))()"
    )


def _text_in(selector: str, *words: str) -> str:
    cond = " && ".join(f"t.includes({json.dumps(w)})" for w in words)
    return (
        f"(() => [...document.querySelectorAll({json.dumps(selector)})]"
        f".some((el) => {{ const t = el.textContent || ''; return {cond}; }}))()"
    )


def check_for(item: dict) -> str | None:
    """The page condition (a JS expression) for one expect item, or None for items
    checked in Python (queue, wrap up)."""
    kind, iid = item["kind"], item.get("id") or ""
    if kind == "identity":
        return _text_in("aside[aria-label='Caller']", "Maria Lopez", "Evergreen HMO Silver")
    if kind == "repeat_caller":
        return _text_in("aside[aria-label='Caller'] .chip", "Repeat caller")
    if kind == "hero":
        return f"!!document.querySelector('[data-testid=\"hero\"][data-card-id={json.dumps(iid)}]')"
    if kind == "card":
        return f"!!document.querySelector('[data-testid=\"tile-{iid}\"]')"
    if kind == "kb":
        # The hero source label shows non breaking hyphens (U+2011); its title is plain.
        return (
            "(() => [...document.querySelectorAll('[data-testid=\"hero-source\"]')].some((el) => "
            "((el.title || '') + ' ' + (el.textContent || '')).replace(/\\u2011/g, '-')"
            f".includes({json.dumps(iid)})))()"
        )
    if kind == "tone_tip":
        return "!!document.querySelector('[data-testid^=\"tile-tone:\"]')"
    if kind == "ask":
        q, rid = json.dumps(item["ask"]), json.dumps(iid)
        return (
            "(() => [...document.querySelectorAll('[data-testid^=\"tile-ask:\"]')].some((t) => "
            f"(t.textContent || '').includes({q}) && ((t.textContent || '').includes({rid}) || "
            "(t.querySelector('[data-testid=\"tile-source\"]')?.title || '').split(', ')"
            f".includes({rid}))))()"
        )
    if kind == "plan":
        step = next(s for s in load_packs()[MARIA["pack"]].call_plan if s.id == iid)
        return _plan_done(step.label)
    return None


async def wait_check(page, js: str, timeout: float = CHECK_SECS) -> float:
    await page.wait_for_function(js, timeout=timeout * 1000, polling=100)
    return time.time()


def test_every_maria_expect_item_has_a_live_check():
    for b in MARIA["talk_track"]:
        for e in b["expect"]:
            assert e["kind"] in ("queue", "wrap_up") or check_for(e), e


async def test_maria_talk_track_end_to_end(schema, tmp_path, monkeypatch):
    from playwright.async_api import async_playwright, expect

    shots = Path(os.getenv("TALK_TRACK_SCREENSHOT_DIR") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setenv(SERVER_URL_ENV, base)
    monkeypatch.setenv(DATABASE_SCHEMA_ENV, schema)
    monkeypatch.setenv("QUEUE_PERSIST", "1")
    monkeypatch.setenv("HOLD_MUSIC", "off")
    for k in ("APP_PASSWORD", "INTAKE_PACK", "SETTINGS_PERSIST"):
        monkeypatch.delenv(k, raising=False)

    lines = _intake_lines()
    who = Persona(KEY, MARIA["name"], MARIA["dob"], MARIA["member_id"], lines)
    audio = {
        k: await synthesize(v, sample_rate=SAMPLE_RATE, voice=VOICE_CALLER)
        for k, v in lines.items()
    }
    pickup = await synthesize(PICKUP_LINE, sample_rate=SAMPLE_RATE, voice=VOICE_CALLER)
    track = MARIA["talk_track"]
    beat_pcm = {
        i: await synthesize(b["say"], sample_rate=SAMPLE_RATE, voice=VOICE_CALLER)
        for i, b in enumerate(track)
        if b["phase"] != "intake"
    }
    agent_pcm = {
        i: await synthesize(t, sample_rate=SAMPLE_RATE, voice=VOICE_AGENT)
        for i, t in AGENT_SAYS.items()
    }

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
    r: dict = {"persona": MARIA["name"], "checkpoints": [], "missed": []}

    def hit(beat: int, item: dict, secs: float) -> None:
        row = {"beat": beat + 1, "kind": item["kind"], "id": item.get("id"), "secs": round(secs, 2)}
        r["checkpoints"].append(row)
        log(t0, f"  beat {beat + 1} {item['kind']:<13} {item.get('id') or '':<30} {secs:6.2f}s")

    try:
        while not server.started:
            await asyncio.sleep(0.05)
        async with httpx.AsyncClient(base_url=base, timeout=30) as http, async_playwright() as pw:
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

            out = (await http.post("/calls/browser")).json()
            session_id = out["session_id"]
            room_name = out["room_url"].rsplit("/", 1)[-1]
            proc = launcher.processes[session_id]
            log(t0, f"call {session_id} room {room_name}")

            async def agent_say(i: int) -> None:
                await page.evaluate(
                    "([b, rate]) => window.__speak(b, rate)",
                    [base64.b64encode(agent_pcm[i]).decode(), SAMPLE_RATE],
                )

            async with HarnessParticipant("caller") as caller:
                await caller.join(out["room_url"], out["caller_token"])
                bot = await caller.wait_for_participant(BOT_NAME, timeout=120)
                rec = caller.recording_for(BOT_NAME)
                t_start = max(caller.joined_at, bot.joined_at)
                gaps: list[float] = []
                queued = await run_intake(
                    http, store, caller, rec, session_id, audio, t_start, who, gaps
                )
                assert queued is not None, "intake never enqueued the call"
                r["intake_secs"] = round(time.time() - t_start, 2)
                r["identity_result"] = queued["identity_result"]
                log(t0, f"intake done in {r['intake_secs']}s identity={r['identity_result']}")

                # ---- last intake beat: the call rings ---------------------------------
                last_intake = max(i for i, b in enumerate(track) if b["phase"] == "intake")
                rang_from = time.time()
                await toggle.click()
                dialog = page.get_by_role("dialog", name="Incoming call")
                await expect(dialog).to_be_visible(timeout=10_000)
                await expect(dialog).to_contain_text("Maria Lopez")
                await expect(dialog).to_contain_text("Verified")
                for e in track[last_intake]["expect"]:
                    hit(last_intake, e, time.time() - rang_from)
                await page.wait_for_timeout(1000)

                heard = asyncio.Event()

                async def keep_talking() -> None:
                    while not heard.is_set():
                        await caller.play(pickup)

                talking = asyncio.create_task(keep_talking())
                await asyncio.sleep(0.3)
                await page.evaluate("document.activeElement && document.activeElement.blur()")
                accept_at = time.time()
                await page.keyboard.press("Enter")
                await expect(page.get_by_role("button", name="Mute")).to_be_enabled(timeout=30_000)
                await caller.wait_for_participant(AGENT_NAME, timeout=15)
                caller_id = caller._client.participants()["local"]["id"]
                end = time.time() + 15
                while time.time() < end:
                    if await page.evaluate("(id) => window.__firstAudio[id] || null", caller_id):
                        break
                    await asyncio.sleep(0.05)
                r["accept_to_caller_audio_secs"] = round(time.time() - accept_at, 2)
                # A real caller keeps saying hello until the agent answers: here until
                # a hello is a final caller line on the agent's screen (the listen STT
                # can be reconnecting right at accept, Deepgram 1011 on idle sockets).
                try:
                    await page.wait_for_function(
                        "() => document.querySelectorAll('li.tr-caller[aria-busy=\"false\"]')"
                        ".length > 0",
                        timeout=45_000,
                    )
                finally:
                    heard.set()
                    await asyncio.wait_for(talking, 30)
                r["accept_to_caller_line_secs"] = round(time.time() - accept_at, 2)
                log(t0, f"caller heard on the agent's screen {r['accept_to_caller_line_secs']}s")
                await wait_bot_turn(rec, accept_at, start_timeout=5)
                await agent_say(AGENT_GREETING)
                await page.wait_for_function(
                    "() => document.querySelectorAll('li.tr-agent[aria-busy=\"false\"]')"
                    ".length > 0",
                    timeout=20_000,
                )

                # ---- the agent and closing beats --------------------------------------
                for i, b in enumerate(track):
                    if b["phase"] == "intake":
                        continue
                    _, said_end = await asyncio.wait_for(caller.play(beat_pcm[i]), 60)
                    log(
                        t0,
                        f"beat {i + 1} ({b['phase']}{' optional' if b['optional'] else ''}): "
                        f"{b['say']!r}",
                    )
                    if b.get("feature") == "ask_bar":
                        ask = next(e for e in b["expect"] if e["kind"] == "ask")
                        box = page.get_by_role("textbox", name="Ask")
                        await page.evaluate(
                            "document.activeElement && document.activeElement.blur()"
                        )
                        await page.keyboard.press("Meta+K")
                        await expect(box).to_be_focused()
                        await box.fill(ask["ask"])
                        await page.keyboard.press("Enter")
                    if i in AGENT_SAYS:
                        await asyncio.sleep(0.5)
                        await agent_say(i)
                    if b["phase"] == "closing":
                        # The talk track's rule: Accept on the hero before End.
                        hero = page.get_by_test_id("hero")
                        await expect(hero).to_have_attribute(
                            "data-card-id", "nbs:retro_auth_review:70412", timeout=10_000
                        )
                        await hero.get_by_role("button", name="Request retro auth review").click()
                        log(t0, "agent pressed Accept on the hero")
                    for e in b["expect"]:
                        js = check_for(e)
                        if js is None:
                            continue
                        try:
                            at = await wait_check(page, js)
                        except Exception:  # noqa: BLE001 - record the miss, fail below
                            r["missed"].append(
                                {"beat": i + 1, "kind": e["kind"], "id": e.get("id")}
                            )
                            log(t0, f"  MISSED beat {i + 1} {e['kind']} {e.get('id')}")
                            await page.screenshot(
                                path=str(shots / f"talk-track-miss-beat{i + 1}.png")
                            )
                            continue
                        hit(i, e, at - said_end)
                    if i == 7:
                        await page.screenshot(path=str(shots / "talk-track-hero-light.png"))

                # ---- End -> wrap up ---------------------------------------------------
                await asyncio.sleep(2.0)
                end_at = time.time()
                await page.get_by_role("button", name="End call").click()
                view = page.get_by_role("region", name="Wrap up")
                await expect(view).to_be_visible(timeout=15_000)
                box = view.get_by_label("Summary", exact=True)
                await expect(box).not_to_have_value("", timeout=40_000)
                r["end_to_draft_secs"] = round(time.time() - end_at, 2)
                r["summary"] = await box.input_value()
                wrap = track[-1]["expect"][-1]
                assert wrap["kind"] == "wrap_up"
                codes = {c.code: c.label for c in load_packs()[MARIA["pack"]].wrap_up.reason_codes}
                suggested = view.locator(".reason.is-suggested")
                await expect(suggested).to_contain_text(codes[wrap["reason_code"]])
                r["suggested_reason"] = (await suggested.inner_text()).split("\n")[0]
                for ref in wrap["refs"]:
                    assert ref in r["summary"], (ref, r["summary"])
                hit(len(track) - 1, wrap, time.time() - end_at)
                await page.screenshot(path=str(shots / "talk-track-wrapup-light.png"))
                await view.get_by_role("button", name="Save and go available").click()
                await expect(page.get_by_role("status")).to_contain_text(
                    "Call saved", timeout=10_000
                )
                await caller.wait_for_participant_left(AGENT_NAME, timeout=15)
                await browser.close()
            await asyncio.wait_for(proc.wait(), 30)
            cur = await store._execute(
                "SELECT wrap_up_reason FROM calls WHERE session_id = %s", (session_id,)
            )
            row = await cur.fetchone()
            r["wrap_up_reason_saved"] = row[0] if row else None
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
        if os.getenv("TALK_TRACK_RESULTS"):
            with open(os.environ["TALK_TRACK_RESULTS"], "a") as f:
                f.write(json.dumps(r) + "\n")

    want = sum(len(b["expect"]) for b in track)
    assert not r["missed"], r["missed"]
    assert len(r["checkpoints"]) == want, (len(r["checkpoints"]), want)
    assert r["wrap_up_reason_saved"] == track[-1]["expect"][-1]["reason_code"]
