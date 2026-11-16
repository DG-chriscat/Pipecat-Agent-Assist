"""Warm bot pool (P5-2): hand off, refill, fallback to cold start, shutdown.

The pool runs real subprocesses, but they are tests/fake_warm_bot.py, not the bot.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from agent_assist_server import bot_launcher
from agent_assist_server.bot_launcher import BotLauncher, BotLaunchRequest, WarmBotPool

FAKE = str(Path(__file__).with_name("fake_warm_bot.py"))

REQ = BotLaunchRequest(
    room_url="https://x.daily.co/r1",
    token="tok-1",
    session_id="s1",
    call_id="call-1",
    call_domain="dom-1",
)


async def until(cond, timeout=10.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


def make_pool(tmp_path, size=1, mode="warm", **kw) -> WarmBotPool:
    return WarmBotPool(
        size,
        command=lambda: [sys.executable, FAKE, str(tmp_path), mode],
        refill_delay=0,
        retry_delay=0.05,
        **kw,
    )


def make_launcher(tmp_path, pool, warm_wait=0.0) -> BotLauncher:
    return BotLauncher(
        pool=pool,
        command_for=lambda req: [sys.executable, FAKE, str(tmp_path), "cold", req.session_id],
        warm_wait=warm_wait,
    )


async def stop(launcher: BotLauncher):
    await launcher.close()
    for p in launcher.processes.values():
        if p.returncode is None:
            p.terminate()
            await p.wait()


async def test_hands_the_call_to_a_ready_warm_bot(tmp_path):
    pool = make_pool(tmp_path)
    launcher = make_launcher(tmp_path, pool)
    await launcher.start()
    try:
        await until(lambda: pool.ready_count == 1)
        warm_pid = pool.idle_pids()[0]

        pid = await launcher.launch(REQ)

        assert pid == warm_pid
        assert launcher.processes["s1"].pid == warm_pid
        assert launcher.last_mode == "warm"
        out = tmp_path / f"{pid}.json"
        await until(out.exists)
        assert json.loads(out.read_text()) == {
            "room_url": REQ.room_url,
            "token": "tok-1",
            "session_id": "s1",
            "call_id": "call-1",
            "call_domain": "dom-1",
        }
        assert not list(tmp_path.glob("cold-*"))
    finally:
        await stop(launcher)


async def test_warm_bot_never_holds_a_room_token_or_app_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("DAILY_ROOM_TOKEN", "leaked")
    monkeypatch.setenv("APP_PASSWORD", "pw")
    monkeypatch.setenv("SESSION_SECRET", "secret")
    env = bot_launcher.build_warm_env()
    assert "DAILY_ROOM_TOKEN" not in env
    assert "APP_PASSWORD" not in env and "SESSION_SECRET" not in env
    assert env["AGENT_ASSIST_BOT_KEY"]
    assert bot_launcher.build_warm_command(python="py") == [
        "py",
        "-m",
        "agent_assist_bot",
        "--warm",
    ]


async def test_refills_after_a_hand_off(tmp_path):
    pool = make_pool(tmp_path)
    launcher = make_launcher(tmp_path, pool)
    await launcher.start()
    try:
        await until(lambda: pool.ready_count == 1)
        first = await launcher.launch(REQ)
        assert pool.ready_count == 0
        await until(lambda: pool.ready_count == 1)
        assert pool.idle_pids() != [first]

        second = await launcher.launch(BotLaunchRequest("https://x.daily.co/r2", "tok-2", "s2"))
        assert second != first
        assert launcher.last_mode == "warm"
        out = tmp_path / f"{second}.json"
        await until(out.exists)
        got = json.loads(out.read_text())
        assert got["session_id"] == "s2" and got["call_id"] is None
    finally:
        await stop(launcher)


async def test_keeps_size_warm_bots(tmp_path):
    pool = make_pool(tmp_path, size=2)
    await pool.start()
    try:
        await until(lambda: pool.ready_count == 2)
        await asyncio.sleep(0.2)
        assert len(list(tmp_path.glob("spawned-*"))) == 2
    finally:
        await pool.close()


async def test_falls_back_to_cold_start_when_no_warm_bot_is_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_READY_DELAY", "30")
    pool = make_pool(tmp_path)
    launcher = make_launcher(tmp_path, pool, warm_wait=0.2)
    await launcher.start()
    try:
        assert pool.ready_count == 0
        await launcher.launch(REQ)
        assert launcher.last_mode == "cold"
        cold = tmp_path / "cold-s1"
        await until(cold.exists)
        assert cold.read_text() == "tok-1"  # cold start still gets its token via env
    finally:
        await stop(launcher)


async def test_waits_briefly_for_a_warm_bot_that_is_still_loading(tmp_path, monkeypatch):
    """Fly wakes the machine on the first call: the pool's bot is already loading, and
    finishing it beats starting a second, cold one from scratch."""
    monkeypatch.setenv("FAKE_READY_DELAY", "0.5")
    pool = make_pool(tmp_path)
    launcher = make_launcher(tmp_path, pool, warm_wait=10)
    await launcher.start()
    try:
        await until(lambda: pool.starting_count == 1)
        assert pool.ready_count == 0
        pid = await launcher.launch(REQ)
        assert launcher.last_mode == "warm"
        await until((tmp_path / f"{pid}.json").exists)
        assert not list(tmp_path.glob("cold-*"))
    finally:
        await stop(launcher)


async def test_without_a_pool_every_call_is_a_cold_start(tmp_path):
    launcher = make_launcher(tmp_path, None)
    await launcher.start()
    await launcher.launch(REQ)
    assert launcher.last_mode == "cold"
    await until((tmp_path / "cold-s1").exists)
    await stop(launcher)


async def test_crashing_warm_bots_back_off_and_calls_go_cold(tmp_path):
    pool = make_pool(tmp_path, mode="crash", max_failures=3)
    launcher = make_launcher(tmp_path, pool)
    await launcher.start()
    try:
        await until(lambda: pool.failures >= 3)
        await asyncio.sleep(0.3)
        assert pool.failures == 3  # gave up: no endless respawn loop
        assert pool.ready_count == 0
        await launcher.launch(REQ)
        assert launcher.last_mode == "cold"
    finally:
        await stop(launcher)


async def test_a_warm_bot_that_dies_while_idle_is_replaced(tmp_path):
    pool = make_pool(tmp_path)
    await pool.start()
    try:
        await until(lambda: pool.ready_count == 1)
        dead = pool.idle_pids()[0]
        os.kill(dead, 9)
        await until(lambda: pool.ready_count == 1 and pool.idle_pids() != [dead])
        proc = pool.take()
        assert proc is not None and proc.returncode is None
        proc.terminate()
        await proc.wait()
    finally:
        await pool.close()


async def test_take_skips_a_warm_bot_that_already_exited(tmp_path):
    pool = make_pool(tmp_path, size=1)
    await pool.start()
    try:
        await until(lambda: pool.ready_count == 1)
        proc = pool._idle[0].proc
        proc.kill()
        await proc.wait()
        assert pool.take() is None
    finally:
        await pool.close()


async def test_shutdown_stops_idle_warm_bots_and_does_not_refill(tmp_path):
    pool = make_pool(tmp_path, size=2)
    launcher = make_launcher(tmp_path, pool)
    await launcher.start()
    await until(lambda: pool.ready_count == 2)
    idle = [w.proc for w in pool._idle]
    in_call = await launcher.launch(REQ)

    await launcher.close()

    for p in idle:
        if p.pid != in_call:
            assert p.returncode is not None
    assert pool.ready_count == 0
    await asyncio.sleep(0.3)
    assert len(list(tmp_path.glob("spawned-*"))) == 2  # no refill after close
    # A call in progress is not cut off by shutdown.
    assert launcher.processes["s1"].returncode is None
    await stop(launcher)


async def test_a_bot_in_a_call_keeps_its_output_read_after_shutdown(tmp_path):
    """Its stdout is a pipe to the server: if nobody reads it, the bot blocks on a
    full pipe mid call."""
    pool = make_pool(tmp_path)
    launcher = make_launcher(tmp_path, pool)
    await launcher.start()
    try:
        await until(lambda: pool.ready_count == 1)
        await launcher.launch(REQ)
        await launcher.close()
        assert pool.readers_for_taken_bots() == 1
    finally:
        await stop(launcher)


@pytest.mark.parametrize(
    "raw,size",
    [(None, 1), ("", 1), ("0", 0), ("1", 1), ("2", 2), ("5", 2), ("-1", 0), ("x", 1)],
)
def test_pool_size_from_env(raw, size):
    env = {} if raw is None else {bot_launcher.WARM_POOL_ENV: raw}
    assert bot_launcher.warm_pool_size(env) == size


def test_launcher_from_env_off_has_no_pool():
    assert BotLauncher.from_env({bot_launcher.WARM_POOL_ENV: "0"}).pool is None
    assert BotLauncher.from_env({}).pool.size == 1


class RecordingLauncher:
    def __init__(self):
        self.events = []

    async def start(self):
        self.events.append("start")

    async def close(self):
        self.events.append("close")


def test_the_warm_pool_starts_and_stops_with_the_server(monkeypatch):
    """Fly (one machine, idle stop): the pool starts when the server starts."""
    from agent_assist_server import app as app_module
    from fastapi.testclient import TestClient

    rec = RecordingLauncher()
    monkeypatch.setattr(app_module.BotLauncher, "from_env", classmethod(lambda cls, env=None: rec))
    fresh = app_module.create_app(env={})
    with TestClient(fresh):
        assert fresh.state.bot_launcher is rec
        assert rec.events == ["start"]
    assert rec.events == ["start", "close"]


def test_a_launcher_set_by_a_test_is_left_alone():
    from agent_assist_server import app as app_module
    from fastapi.testclient import TestClient

    fresh = app_module.create_app(env={})
    fake = object()
    fresh.state.bot_launcher = fake
    with TestClient(fresh):
        assert fresh.state.bot_launcher is fake


class FailingLauncher:
    async def launch(self, req):
        raise OSError("no python")


async def test_bot_launched_timing_line_says_warm_or_cold(tmp_path, caplog):
    import logging

    pool = make_pool(tmp_path)
    launcher = make_launcher(tmp_path, pool)
    await launcher.start()
    try:
        await until(lambda: pool.ready_count == 1)
        with caplog.at_level(logging.INFO, logger="agent_assist_server"):
            await bot_launcher.launch_in_background(launcher, REQ)
            await bot_launcher.launch_in_background(
                launcher, BotLaunchRequest("https://x.daily.co/r2", "tok-2", "s2")
            )
        lines = [r.getMessage() for r in caplog.records if "event=bot_launched" in r.getMessage()]
        assert len(lines) == 2
        assert lines[0].endswith("session=s1 mode=warm")
        assert lines[1].endswith("session=s2 mode=cold")
    finally:
        await stop(launcher)


@pytest.mark.parametrize("path", ["/calls/browser", "/daily/dialin"])
def test_the_bot_starts_after_the_response_and_a_failure_is_logged(path, monkeypatch, caplog):
    """Waiting for a loading warm bot can take seconds; Daily's webhook and the caller
    page get their answer first. A launch failure is logged, not lost."""
    import logging

    import httpx
    from agent_assist_server import dialin
    from agent_assist_server.app import app
    from agent_assist_server.daily import DailyRestClient
    from fastapi.testclient import TestClient

    def handler(request):
        if request.url.path.endswith("/rooms"):
            name = "r1"
            return httpx.Response(
                200,
                json={
                    "name": name,
                    "url": f"https://x.daily.co/{name}",
                    "config": {"sip_endpoint": "sip:r1@x"},
                },
            )
        return httpx.Response(200, json={"token": "tok"})

    monkeypatch.delenv(dialin.HMAC_ENV, raising=False)
    app.state.daily_client_factory = lambda: DailyRestClient(
        api_key="k", transport=httpx.MockTransport(handler)
    )
    app.state.bot_launcher = FailingLauncher()
    try:
        with caplog.at_level(logging.ERROR, logger="agent_assist_server"):
            r = TestClient(app).post(path, json={"callId": "c1", "callDomain": "d1"})
        assert r.status_code == 200
        assert "bot launch failed" in caplog.text
    finally:
        del app.state.daily_client_factory
        del app.state.bot_launcher


# ---- P5-5: the call's settings go with the assignment ---------------------------------

SETTINGS = {"tts_model": "flux", "tts_voice": "flux-hannah-en", "hold_update_secs": 30}


async def test_warm_bot_gets_the_calls_settings_with_its_assignment(tmp_path):
    pool = make_pool(tmp_path)
    launcher = make_launcher(tmp_path, pool)
    await launcher.start()
    try:
        await until(lambda: pool.ready_count == 1)
        req = BotLaunchRequest(REQ.room_url, "tok-1", "s1", settings=SETTINGS)
        pid = await launcher.launch(req)
        out = tmp_path / f"{pid}.json"
        await until(out.exists)
        assert json.loads(out.read_text())["settings"] == SETTINGS
    finally:
        await stop(launcher)


def test_cold_bot_gets_the_calls_settings_in_its_env():
    req = BotLaunchRequest(REQ.room_url, "tok-1", "s1", settings=SETTINGS)
    env = bot_launcher.build_bot_env(req, base={"PATH": "/bin"})
    assert json.loads(env[bot_launcher.SETTINGS_ENV]) == SETTINGS


def test_no_settings_leaves_the_env_and_assignment_without_them():
    env = bot_launcher.build_bot_env(REQ, base={bot_launcher.SETTINGS_ENV: "stale"})
    assert bot_launcher.SETTINGS_ENV not in env
    assert "settings" not in bot_launcher.assignment_line(REQ)
