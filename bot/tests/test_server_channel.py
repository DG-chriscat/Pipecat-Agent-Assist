"""QH-5b: the bot's socket to the server (ServerChannel) and first-signal-wins between
it and the Daily app message fallback. Real local websocket server, no network."""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets
from agent_assist_bot import bot, handoff, hold
from agent_assist_bot.server_channel import ServerChannel, channel_url
from agent_assist_bot.transcript import CallRoles

ACCEPTED = {"type": hold.MESSAGE_TYPE, "event": "agent_accepted", "agent_name": "Ana", "id": 7}


class Server:
    """A /ws/bot/{session_id} stand in: records each connection's path and headers,
    and lets the test push messages to the open socket."""

    def __init__(self, close_code: int | None = None):
        self.paths: list[str] = []
        self.auth: list[str | None] = []
        self.sockets: list = []
        self.close_code = close_code
        self.connected = asyncio.Event()

    async def handler(self, ws):
        self.paths.append(ws.request.path)
        self.auth.append(ws.request.headers.get("authorization"))
        if self.close_code is not None:
            await ws.close(code=self.close_code)
            return
        self.sockets.append(ws)
        self.connected.set()
        try:
            await ws.wait_closed()
        finally:
            self.connected.clear()

    async def push(self, msg: dict) -> None:
        await self.sockets[-1].send(json.dumps(msg))


@pytest.fixture
async def server():
    srv = Server()
    async with websockets.serve(srv.handler, "127.0.0.1", 0) as s:
        port = s.sockets[0].getsockname()[1]
        srv.url = f"http://127.0.0.1:{port}"
        yield srv


async def wait_until(pred, timeout=2.0):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if pred():
            return True
        await asyncio.sleep(0.01)
    return False


def test_channel_url_maps_http_to_ws():
    assert channel_url("http://127.0.0.1:8000/", "s1") == "ws://127.0.0.1:8000/ws/bot/s1"
    assert channel_url("https://x.fly.dev", "a b") == "wss://x.fly.dev/ws/bot/a%20b"


async def test_channel_delivers_server_messages_with_the_bot_key(server):
    got: list[dict] = []

    async def on_message(msg):
        got.append(msg)

    ch = ServerChannel(server.url, "s1", on_message, bot_key="k123")
    ch.start()
    try:
        assert await asyncio.wait_for(server.connected.wait(), 2)
        await server.push(ACCEPTED)
        assert await wait_until(lambda: got == [ACCEPTED])
        assert server.paths == ["/ws/bot/s1"]
        assert server.auth == ["Bearer k123"]
    finally:
        await ch.close()


async def test_channel_reconnects_after_a_drop(server):
    got: list[dict] = []

    async def on_message(msg):
        got.append(msg)

    ch = ServerChannel(server.url, "s1", on_message, retry_delays=(0.01,))
    ch.start()
    try:
        assert await asyncio.wait_for(server.connected.wait(), 2)
        await server.sockets[-1].close()
        assert await wait_until(lambda: len(server.paths) == 2)
        assert await asyncio.wait_for(server.connected.wait(), 2)
        await server.push(ACCEPTED)
        assert await wait_until(lambda: got == [ACCEPTED])
    finally:
        await ch.close()


async def test_channel_gives_up_when_the_server_refuses():
    srv = Server(close_code=4401)
    async with websockets.serve(srv.handler, "127.0.0.1", 0) as s:
        url = f"http://127.0.0.1:{s.sockets[0].getsockname()[1]}"

        async def on_message(msg):
            raise AssertionError("no messages expected")

        ch = ServerChannel(url, "s1", on_message, retry_delays=(0.01,))
        ch.start()
        assert await wait_until(lambda: ch.done)
        assert srv.paths == ["/ws/bot/s1"]  # no retry loop on 4401
        await ch.close()


async def test_channel_with_no_server_does_not_raise():
    async def on_message(msg):
        raise AssertionError("no messages expected")

    ch = ServerChannel("http://127.0.0.1:9", "s1", on_message, retry_delays=(0.01,))
    ch.start()
    await asyncio.sleep(0.1)
    await ch.close()  # down socket: the Daily fallback still works


async def test_bad_message_does_not_kill_the_channel(server):
    got: list[dict] = []

    async def on_message(msg):
        got.append(msg)

    ch = ServerChannel(server.url, "s1", on_message)
    ch.start()
    try:
        assert await asyncio.wait_for(server.connected.wait(), 2)
        await server.sockets[-1].send("not json")
        await server.push(ACCEPTED)
        assert await wait_until(lambda: got == [ACCEPTED])
    finally:
        await ch.close()


# ---- handoff opens the channel after enqueue --------------------------------------------


async def test_queue_handoff_tells_listeners_after_enqueue():
    import httpx

    seen: list[str] = []
    h = handoff.QueueHandoff(
        "http://srv",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"position": 1})),
    )

    async def opened(session_id):
        seen.append(session_id)

    h.add_enqueued_listener(opened)
    from test_queue_hold import HANDOFF

    await h.on_intake_complete(HANDOFF)
    assert seen == ["s1"]


async def test_queue_handoff_does_not_tell_listeners_when_enqueue_fails():
    import httpx

    seen: list[str] = []
    h = handoff.QueueHandoff(
        "http://srv", transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )

    async def opened(session_id):
        seen.append(session_id)

    h.add_enqueued_listener(opened)
    from test_queue_hold import HANDOFF

    with pytest.raises(handoff.HandoffError):
        await h.on_intake_complete(HANDOFF)
    assert seen == []


# ---- first signal wins ---------------------------------------------------------------


class Music:
    def __init__(self):
        self.enabled = False
        self.log: list[str] = []

    def enable(self):
        self.enabled = True
        self.log.append("on")

    def disable(self):
        self.enabled = False
        self.log.append("off")


class FakeWorker:
    def __init__(self):
        self.queued = []

    async def queue_frames(self, frames):
        self.queued.extend(frames)

    async def cancel(self, **kwargs):
        pass


class FakeTransport:
    def __init__(self):
        self.handlers = {}

    def event_handler(self, name):
        def deco(fn):
            self.handlers[name] = fn
            return fn

        return deco


def wired():
    transport, worker, music = FakeTransport(), FakeWorker(), Music()
    roles = CallRoles()
    ctl = hold.HoldController(worker.queue_frames, music=music, interval=60)
    on_server_message = bot.register_handlers(transport, worker, hold=ctl, roles=roles)
    return transport, worker, music, ctl, roles, on_server_message


def spoken(worker):
    return [f.text for f in worker.queued]


async def queue_it(transport):
    queued = {"type": hold.MESSAGE_TYPE, "event": "queued", "position": 1}
    await transport.handlers["on_app_message"](transport, queued, "api")


async def test_socket_signal_stops_the_music():
    transport, worker, music, ctl, roles, on_server_message = wired()
    await queue_it(transport)
    assert music.enabled
    await on_server_message(ACCEPTED)
    assert not music.enabled
    assert ctl.state is hold.HoldState.WITH_AGENT
    assert roles.listening
    assert spoken(worker) == [hold.CONNECTING_LINE]
    ctl.stop()


async def test_daily_fallback_still_stops_the_music_without_the_socket():
    transport, worker, music, ctl, roles, _ = wired()
    await queue_it(transport)
    await transport.handlers["on_app_message"](transport, ACCEPTED, "api")
    assert not music.enabled
    assert roles.listening
    assert spoken(worker) == [hold.CONNECTING_LINE]
    ctl.stop()


async def test_second_copy_of_the_same_signal_is_a_no_op():
    transport, worker, music, ctl, roles, on_server_message = wired()
    await queue_it(transport)
    await on_server_message(ACCEPTED)
    await transport.handlers["on_app_message"](transport, ACCEPTED, "api")
    assert music.log == ["on", "off"]
    assert spoken(worker) == [hold.CONNECTING_LINE]
    ctl.stop()


async def test_late_daily_copy_does_not_undo_a_requeue():
    # Socket: accepted (id 7), then requeued (id 8). The Daily copy of id 7 arrives
    # late; it must not take the call off hold again.
    transport, worker, music, ctl, roles, on_server_message = wired()
    await queue_it(transport)
    await on_server_message(ACCEPTED)
    requeued = {"type": hold.MESSAGE_TYPE, "event": "requeued", "position": 1, "id": 8}
    await on_server_message(requeued)
    assert ctl.state is hold.HoldState.QUEUED and music.enabled
    await transport.handlers["on_app_message"](transport, ACCEPTED, "api")
    assert ctl.state is hold.HoldState.QUEUED and music.enabled
    assert spoken(worker) == [hold.CONNECTING_LINE, hold.REQUEUED_LINE]
    ctl.stop()


async def test_build_worker_opens_the_channel_after_enqueue(monkeypatch, server):
    # Wiring: QueueHandoff enqueue -> channel opens -> socket signal stops the mixer.
    import httpx

    for k, v in {"DAILY_API_KEY": "d", "DEEPGRAM_API_KEY": "g", "ANTHROPIC_API_KEY": "a"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("HOLD_MUSIC", raising=False)
    h = handoff.QueueHandoff(
        server.url,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"position": 1})),
    )
    from agent_assist_bot.calls import MemoryCallStore

    _, transport = bot.build_worker(
        "https://x.daily.co/r", "tok", session_id="s1", handoff=h, store=MemoryCallStore()
    )
    mixer = transport._params.audio_out_mixer
    for fn in transport._event_handlers["on_app_message"].handlers:
        await fn(transport, {"type": hold.MESSAGE_TYPE, "event": "queued", "position": 1}, "api")
    assert mixer.enabled
    from test_queue_hold import HANDOFF

    await h.on_intake_complete(HANDOFF)
    assert await asyncio.wait_for(server.connected.wait(), 2)
    assert server.paths == ["/ws/bot/s1"]
    await server.push(ACCEPTED)
    assert await wait_until(lambda: not mixer.enabled)
    await h.close()
