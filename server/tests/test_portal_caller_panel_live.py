"""Live P3-3: the portal caller panel fills from the real endpoint for Maria Alvarez.

Run locally with a Postgres, DAILY_API_KEY, a built portal (npm run build in portal/),
and Playwright's Chromium:
    PLAYWRIGHT_BROWSERS_PATH=... uv run --with playwright \\
        pytest -m live server/tests/test_portal_caller_panel_live.py -s -v

P3_SCREENSHOT_DIR (optional) keeps the 1600x1000 screenshot.

The real server runs under uvicorn on a throwaway schema seeded with today's mock data
(DATABASE_SCHEMA), so GET /calls/{id}/caller reads the seeded interactions, claims, and
referrals. A real Daily room is created and the call is put in the queue with the
intake answers the bot captures for demo persona Maria Alvarez (verified, EVM100231);
no bot or LLM is needed for the caller panel. The built portal in headless Chromium
signs in, goes available, accepts with Enter, joins the room, and the caller panel
must show the accept summary at once and then the member, plan, repeat chip, and the
last 3 events from the database.
"""

from __future__ import annotations

import asyncio
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
from agent_assist_server.app import app
from agent_assist_server.caller_api import CallerHistory
from agent_assist_server.daily import DailyRestClient
from agent_assist_server.portal import DEFAULT_DIST
from agent_assist_server.queue_api import get_queue
from agent_assist_server.queue_service import IntakeInfo

pytestmark = pytest.mark.live

REQUIRED = ("DAILY_API_KEY", "DATABASE_URL")
AGENT_NAME = "Agent Ana"
VIEWPORT = {"width": 1600, "height": 1000}
CHROMIUM_ARGS = [
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
]
# What the claims_status intake saves for Maria (docs/intake.md, harness script).
MARIA_INTAKE = {
    "full_name": "Maria Alvarez",
    "dob": "1984-03-12",
    "member_id": "EVM100231",
    "reason": "MRI claim was denied",
    "claim_number": "CLM-900001",
    "service_date": "About a month ago, Riverside Imaging Center",
    "claim_concern": "Denied, letter mentioned no prior authorization",
    "summary": (
        "Maria Alvarez is calling about her MRI claim CLM-900001 from Riverside Imaging "
        "Center, which was denied for no prior authorization."
    ),
}


@pytest.fixture
def ready():
    missing = [k for k in REQUIRED if not os.getenv(k)]
    if missing:
        pytest.skip(f"missing env: {', '.join(missing)}")
    if not (DEFAULT_DIST / "index.html").is_file():
        pytest.skip("portal not built: run npm ci && npm run build in portal/")
    pytest.importorskip("playwright.async_api")


@pytest.fixture
async def schema(ready):
    from agent_assist_server.db import connect, migrate, seed

    name = f"test_{uuid.uuid4().hex[:12]}"
    async with await connect() as admin:
        await admin.execute(f'CREATE SCHEMA "{name}"')
    try:
        async with await connect(schema=name) as c:
            await migrate.apply_migrations(c)
            await seed.load_seed(c, as_of=dt.datetime.now(dt.UTC).date())
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


async def test_live_caller_panel_fills_for_maria(schema, shots, monkeypatch):
    from playwright.async_api import async_playwright, expect

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    monkeypatch.delenv("QUEUE_PERSIST", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)  # the module app was built without login
    app.state.caller_history = CallerHistory(schema=schema)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serve_task = asyncio.create_task(server.serve())
    daily = DailyRestClient.from_env()
    room_name = None
    timings: dict[str, float] = {}
    t0 = time.time()
    try:
        while not server.started:
            await asyncio.sleep(0.05)
        room = await daily.create_browser_room()
        room_name = room.name
        log(t0, f"Daily room {room_name} created")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(args=CHROMIUM_ARGS)
            ctx = await browser.new_context(viewport=VIEWPORT, permissions=["microphone"])
            page = await ctx.new_page()
            page.on("console", lambda m: m.type == "error" and print(f"  [portal] {m.text}"))

            await page.goto(f"{base}/agent/")
            await page.get_by_label("Your name").fill(AGENT_NAME)
            await page.get_by_role("button", name="Sign in").click()
            toggle = page.get_by_role("switch", name="Available")
            await expect(toggle).to_be_enabled(timeout=10_000)
            log(t0, "portal signed in")

            # The intake handoff, as the bot posts it after Maria's read back.
            svc = get_queue(app)
            session_id = f"live-{uuid.uuid4().hex[:8]}"
            svc.register_session(session_id, room.url)
            svc.enqueue_call(
                session_id,
                room.url,
                MARIA_INTAKE,
                IntakeInfo(outcome="completed", identity_result="verified", member_id="EVM100231"),
            )
            log(t0, f"call {session_id} queued as Maria Alvarez (verified)")
            # Let her wait a little, like a real caller on hold.
            await asyncio.sleep(3)

            await toggle.click()
            card = page.get_by_role("dialog", name="Incoming call")
            await expect(card).to_be_visible(timeout=5_000)
            await expect(card).to_contain_text("Maria Alvarez")
            log(t0, "incoming card up")

            accept_at = time.time()
            await page.keyboard.press("Enter")
            panel = page.get_by_role("complementary", name="Caller")
            await expect(panel).to_contain_text("Maria Alvarez", timeout=10_000)
            await expect(panel).to_contain_text("MRI claim was denied")
            timings["accept_to_summary_secs"] = time.time() - accept_at
            await expect(panel).to_contain_text("Evergreen HMO Silver", timeout=10_000)
            timings["accept_to_history_secs"] = time.time() - accept_at
            await expect(panel).to_contain_text("Mar 12, 1984")
            await expect(panel.get_by_text("Repeat", exact=True)).to_be_visible()
            items = panel.get_by_test_id("history").get_by_role("listitem")
            await expect(items).to_have_count(3)
            events = await items.all_inner_texts()
            log(
                t0,
                f"summary {timings['accept_to_summary_secs']:.2f}s, history "
                f"{timings['accept_to_history_secs']:.2f}s after Enter",
            )
            for e in events:
                log(t0, f"  before this call: {' '.join(e.split())}")
            id_line = await panel.get_by_test_id("caller-id").inner_text()
            log(t0, f"caller id line: {id_line}")
            assert id_line == "EVM100231 · Evergreen HMO Silver"
            assert "CO-197" in " ".join(events)

            await expect(page.get_by_role("button", name="Mute")).to_be_enabled(timeout=30_000)
            timings["accept_to_audio_joined_secs"] = time.time() - accept_at
            log(t0, f"room joined {timings['accept_to_audio_joined_secs']:.2f}s after Enter")
            await page.wait_for_timeout(1500)
            path = shots / "p3-3-caller-panel-maria.png"
            await page.screenshot(path=str(path))
            log(t0, f"screenshot {path}")

            # The endpoint answers this agent only.
            agent_id = next(a.agent_id for a in svc.agents() if a.name == AGENT_NAME)
            other = svc.sign_in("Agent Ben").agent_id
            async with httpx.AsyncClient(base_url=base) as http:
                mine = await http.get(f"/calls/{session_id}/caller", params={"agent_id": agent_id})
                theirs = await http.get(f"/calls/{session_id}/caller", params={"agent_id": other})
            log(t0, f"GET caller as Ana -> {mine.status_code}, as Ben -> {theirs.status_code}")
            assert mine.status_code == 200 and theirs.status_code == 403
            body = mine.json()
            log(
                t0,
                "endpoint: "
                + json.dumps(
                    {
                        "member": body["member"]["name"],
                        "plan": body["member"]["plan_name"],
                        "calls_30d": body["calls_30d"],
                        "repeat_caller": body["repeat_caller"],
                        "events": [e["ref"] for e in body["recent_events"]],
                        "wait_secs": body["wait_secs"],
                    }
                ),
            )

            await page.get_by_role("button", name="End call").click()
            await expect(page.get_by_role("region", name="Wrap up")).to_be_visible(timeout=10_000)
            log(t0, "ended; agent in wrap up")
            await browser.close()
    finally:
        server.should_exit = True
        await asyncio.wait_for(serve_task, 10)
        if room_name:
            await daily.delete_room(room_name)
            log(t0, f"room {room_name} deleted")
        for attr in ("queue_service", "caller_history"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)

    print("TIMINGS " + json.dumps({k: round(v, 2) for k, v in timings.items()}, indent=2))
