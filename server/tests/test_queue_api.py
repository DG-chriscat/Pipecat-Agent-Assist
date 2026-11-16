"""Queue REST + websocket API and the bot notifier (PRD QH-3 to QH-7), Daily mocked."""

import asyncio
import json
import time

import httpx
import pytest
from agent_assist_server import queue_api
from agent_assist_server.app import app
from agent_assist_server.daily import DailyRestClient
from agent_assist_server.queue_service import QueueService
from fastapi.testclient import TestClient

ROOM_URL = "https://dg-chriscat.daily.co/caller-abc"


class DailyMock:
    def __init__(self, token_status: int = 200):
        self.requests: list[httpx.Request] = []
        self.token_status = token_status

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/meeting-tokens"):
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "nope"})
            props = json.loads(request.content)["properties"]
            return httpx.Response(200, json={"token": f"tok-{props['user_name']}"})
        if request.url.path.endswith("/send-app-message"):
            return httpx.Response(200, json={"status": "sent"})
        if request.url.path.endswith("/presence"):
            return httpx.Response(200, json={"total_count": 1, "data": []})
        return httpx.Response(404)

    def app_messages(self) -> list[tuple[str, dict]]:
        return [
            (r.url.path.split("/")[-2], json.loads(r.content))
            for r in self.requests
            if r.url.path.endswith("/send-app-message")
        ]

    def token_bodies(self) -> list[dict]:
        return [
            json.loads(r.content) for r in self.requests if r.url.path.endswith("/meeting-tokens")
        ]


@pytest.fixture
def daily():
    return DailyMock()


@pytest.fixture
def client(daily, monkeypatch):
    monkeypatch.delenv("QUEUE_PERSIST", raising=False)
    app.state.daily_client_factory = lambda: DailyRestClient(
        api_key="test-key", transport=httpx.MockTransport(daily.handler)
    )
    with TestClient(app) as c:
        svc = queue_api.get_queue(app)
        svc.register_session("s1", ROOM_URL)
        svc.register_session("s2", "https://dg-chriscat.daily.co/caller-def")
        yield c
    for attr in ("daily_client_factory", "queue_service"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def sign_in(client, name="Ana") -> str:
    r = client.post("/agents/sign-in", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["agent_id"]


def make_available(client, agent_id):
    r = client.post(f"/agents/{agent_id}/status", json={"state": "available"})
    assert r.status_code == 200, r.text


def enqueue(client, session_id="s1", summary=None):
    return client.post(
        "/queue/calls", json={"session_id": session_id, "summary": summary or {"reason": "x"}}
    )


def wait_for(pred, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


# ---- REST ---------------------------------------------------------------------


def test_sign_in_and_status(client):
    agent_id = sign_in(client)
    make_available(client, agent_id)
    snap = client.get("/queue").json()
    assert snap["agents"][0]["state"] == "available"
    assert snap["offer_timeout_secs"] == 20


def test_bad_status_is_409(client):
    agent_id = sign_in(client)
    r = client.post(f"/agents/{agent_id}/status", json={"state": "on_call"})
    assert r.status_code == 409


def test_unknown_agent_is_409(client):
    r = client.post("/agents/agent-nope/status", json={"state": "available"})
    assert r.status_code == 409


def test_enqueue_unknown_session_is_404(client):
    r = enqueue(client, "not-launched-here")
    assert r.status_code == 404


def test_enqueue_returns_position(client):
    assert enqueue(client, "s1").json() == {"session_id": "s1", "status": "queued", "position": 1}
    assert enqueue(client, "s2").json()["position"] == 2


def test_accept_returns_token_room_and_summary(client, daily):
    agent_id = sign_in(client, "Agent Ana")
    make_available(client, agent_id)
    enqueue(client, "s1", {"reason": "claim denied"})
    r = client.post(f"/agents/{agent_id}/accept", json={"session_id": "s1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token"] == "tok-Agent Ana"
    assert body["room_url"] == ROOM_URL
    assert body["summary"] == {"reason": "claim denied"}
    # Same token shape as POST /rooms/{room}/agent-token: non-owner, scoped to the room.
    props = daily.token_bodies()[0]["properties"]
    assert props["room_name"] == "caller-abc"
    assert props["is_owner"] is False
    assert props["user_name"] == "Agent Ana"


INTAKE_HANDOFF = {
    "session_id": "s1",
    "call_id": "c-1",
    "outcome": "emergency",
    "summary": {"reason": "claim denied", "summary": "Caller described chest pain."},
    "identity_result": "not_verified",
    "member_id": "EVM100231",
    "flags": ["identity_not_verified", "emergency"],
}


def test_enqueue_accepts_intake_handoff_and_flags_reach_the_call(client):
    """P2-3 IntakeHandoff fields from QueueHandoff land on the queued call record."""
    r = client.post("/queue/calls", json=INTAKE_HANDOFF)
    assert r.status_code == 200, r.text
    call = client.get("/queue").json()["queue"][0]
    assert call["outcome"] == "emergency"
    assert call["identity_result"] == "not_verified"
    assert call["member_id"] == "EVM100231"
    assert call["flags"] == ["identity_not_verified", "emergency"]
    assert call["summary"] == INTAKE_HANDOFF["summary"]


def test_offer_and_accept_carry_flags(client):
    agent_id = sign_in(client, "Agent Ana")
    client.post("/queue/calls", json=INTAKE_HANDOFF)
    with client.websocket_connect("/ws/queue") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        make_available(client, agent_id)
        while (msg := ws.receive_json())["type"] != "offer":
            pass
        assert msg["call"]["flags"] == ["identity_not_verified", "emergency"]
    body = client.post(f"/agents/{agent_id}/accept", json={"session_id": "s1"}).json()
    assert body["flags"] == ["identity_not_verified", "emergency"]
    assert body["identity_result"] == "not_verified"
    assert body["outcome"] == "emergency"


def test_enqueue_rejects_bad_identity_result(client):
    r = client.post("/queue/calls", json={**INTAKE_HANDOFF, "identity_result": "maybe"})
    assert r.status_code == 422


def test_enqueue_old_shape_still_works(client):
    r = enqueue(client, "s1", {"reason": "x"})
    assert r.status_code == 200
    call = client.get("/queue").json()["queue"][0]
    assert call["flags"] == [] and call["identity_result"] is None


def test_accept_without_offer_is_409(client):
    agent_id = sign_in(client)
    enqueue(client)
    r = client.post(f"/agents/{agent_id}/accept", json={"session_id": "s1"})
    assert r.status_code == 409


def test_accept_token_failure_is_502_and_call_requeued(daily, client):
    daily.token_status = 500
    agent_id = sign_in(client)
    make_available(client, agent_id)
    enqueue(client)
    r = client.post(f"/agents/{agent_id}/accept", json={"session_id": "s1"})
    assert r.status_code == 502
    assert client.get("/queue").json()["queue"][0]["session_id"] == "s1"


def test_decline_rings_next(client):
    ana = sign_in(client, "Ana")
    make_available(client, ana)
    ben = sign_in(client, "Ben")
    make_available(client, ben)
    enqueue(client)
    r = client.post(f"/agents/{ana}/decline", json={"session_id": "s1"})
    assert r.status_code == 200
    agents = {a["name"]: a for a in client.get("/queue").json()["agents"]}
    assert agents["Ana"]["state"] == "offline"
    assert agents["Ben"]["offer_call_id"] == "s1"


def test_end_call_frees_ringing_agent(client):
    agent_id = sign_in(client)
    make_available(client, agent_id)
    enqueue(client)
    r = client.post("/queue/calls/s1/end")
    assert r.status_code == 200
    snap = client.get("/queue").json()
    assert snap["queue"] == []
    assert snap["agents"][0]["state"] == "available"


# ---- bot notifier (QH-5, QH-6) ----------------------------------------------------


def test_bot_told_when_queued_and_when_accepted(client, daily):
    enqueue(client, "s1")
    agent_id = sign_in(client, "Ana")
    make_available(client, agent_id)
    client.post(f"/agents/{agent_id}/accept", json={"session_id": "s1"})
    assert wait_for(lambda: len(daily.app_messages()) == 2)
    (room1, queued), (room2, accepted) = daily.app_messages()
    assert room1 == room2 == "caller-abc"
    assert queued == {
        "data": {"type": queue_api.BOT_MESSAGE_TYPE, "event": "queued", "position": 1},
        "recipient": "*",
    }
    assert accepted["data"] == {
        "type": queue_api.BOT_MESSAGE_TYPE,
        "event": "agent_accepted",
        "agent_name": "Ana",
    }
    assert "tok-" not in json.dumps(daily.app_messages())


def test_bot_notifier_warms_daily_when_the_offer_rings(client, daily):
    # QH-5: the bot stops hold music on agent_accepted, so that message must not wait
    # on a new TLS connection to Daily. The offer warms it (accept is <= 20 s later).
    enqueue(client, "s1")
    agent_id = sign_in(client, "Ana")
    make_available(client, agent_id)
    assert wait_for(lambda: any(r.url.path.endswith("/presence") for r in daily.requests))
    client.post(f"/agents/{agent_id}/accept", json={"session_id": "s1"})
    assert wait_for(lambda: len(daily.app_messages()) == 2)
    paths = [r.url.path for r in daily.requests if "meeting-tokens" not in r.url.path]
    warm = paths.index("/v1/rooms/caller-abc/presence")
    assert warm < len(paths) - 1 and paths[-1].endswith("/send-app-message")


def test_bot_notifier_keeps_one_daily_connection(daily, monkeypatch):
    # Every app message used to build its own client (and TLS connection). The
    # notifier now makes one keep-alive client and sends everything through it.
    monkeypatch.delenv("QUEUE_PERSIST", raising=False)
    made: list[DailyRestClient] = []

    class Tracking(DailyRestClient):
        def keep_alive(self):
            client = super().keep_alive()
            made.append(client)
            return client

    app.state.daily_client_factory = lambda: Tracking(
        api_key="test-key", transport=httpx.MockTransport(daily.handler)
    )
    try:
        with TestClient(app) as c:
            svc = queue_api.get_queue(app)
            svc.register_session("s1", ROOM_URL)
            svc.register_session("s2", "https://dg-chriscat.daily.co/caller-def")
            enqueue(c, "s1")
            enqueue(c, "s2")
            agent_id = sign_in(c, "Ana")
            make_available(c, agent_id)
            c.post(f"/agents/{agent_id}/accept", json={"session_id": "s1"})
            assert wait_for(lambda: len(daily.app_messages()) >= 4)
            assert len(made) == 1
            assert made[0]._shared is not None and not made[0]._shared.is_closed
    finally:
        for attr in ("daily_client_factory", "queue_service"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)


async def test_keep_alive_client_reuses_one_http_client():
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        return httpx.Response(200, json={"total_count": 0, "data": []})

    plain = DailyRestClient(api_key="k", transport=httpx.MockTransport(handler))
    kept = plain.keep_alive()
    assert plain._keep_alive is False and kept._keep_alive is True
    await kept.send_app_message("room-a", {"x": 1})
    first = kept._shared
    await kept.get_presence("room-a")
    await kept.send_app_message("room-b", {"x": 2})
    assert kept._shared is first and not first.is_closed
    await plain.send_app_message("room-a", {"x": 3})
    assert plain._shared is None  # plain clients still open and close per call
    assert seen == [
        "/v1/rooms/room-a/send-app-message",
        "/v1/rooms/room-a/presence",
        "/v1/rooms/room-b/send-app-message",
        "/v1/rooms/room-a/send-app-message",
    ]


# ---- websocket (QH-4) -------------------------------------------------------------


def recv_until(ws, type_, limit=50):
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] == type_:
            return msg
    raise AssertionError(f"no {type_} message")


def test_ws_sends_snapshot_then_pushes_changes(client):
    with client.websocket_connect("/ws/queue") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
        assert snap["queue"] == [] and snap["agents"] == []
        enqueue(client)
        msg = recv_until(ws, "call_queued")
        assert msg["call"]["session_id"] == "s1"
        assert msg["position"] == 1


def test_ws_agent_flow_sign_in_available_offer_accept(client):
    enqueue(client, "s1", {"reason": "claim denied"})
    with client.websocket_connect("/ws/queue") as ws:
        ws.receive_json()  # snapshot
        ws.send_json({"type": "sign_in", "name": "Ana"})
        me = recv_until(ws, "signed_in")["agent"]
        ws.send_json({"type": "set_status", "agent_id": me["agent_id"], "state": "available"})
        offer = recv_until(ws, "offer")
        assert offer["agent_id"] == me["agent_id"]
        assert offer["call"]["summary"] == {"reason": "claim denied"}
        ws.send_json({"type": "accept", "agent_id": me["agent_id"], "session_id": "s1"})
        accepted = recv_until(ws, "accepted")
        assert accepted["token"] == "tok-Ana"
        assert accepted["room_url"] == ROOM_URL
        assert accepted["summary"] == {"reason": "claim denied"}


def test_ws_token_only_goes_to_the_accepting_socket(client):
    enqueue(client)
    with (
        client.websocket_connect("/ws/queue") as watcher,
        client.websocket_connect("/ws/queue") as agent_ws,
    ):
        watcher.receive_json()
        agent_ws.receive_json()
        agent_ws.send_json({"type": "sign_in", "name": "Ana"})
        me = recv_until(agent_ws, "signed_in")["agent"]
        agent_ws.send_json({"type": "set_status", "agent_id": me["agent_id"], "state": "available"})
        recv_until(agent_ws, "offer")
        agent_ws.send_json({"type": "accept", "agent_id": me["agent_id"], "session_id": "s1"})
        recv_until(agent_ws, "accepted")
        seen = recv_until(watcher, "call_accepted")
        assert "token" not in json.dumps(seen)


def test_ws_bad_command_gets_error_and_socket_stays_open(client):
    with client.websocket_connect("/ws/queue") as ws:
        ws.receive_json()
        ws.send_json({"type": "set_status", "agent_id": "agent-nope", "state": "available"})
        err = recv_until(ws, "error")
        assert "unknown agent" in err["message"]
        ws.send_json({"type": "bogus"})
        assert recv_until(ws, "error")["message"].startswith("unknown command")
        ws.send_text("not json")
        assert recv_until(ws, "error")["message"] == "invalid JSON"
        ws.send_json({"type": "sign_in", "name": "Ana"})
        assert recv_until(ws, "signed_in")["agent"]["name"] == "Ana"


def test_ws_disconnect_unsubscribes(client):
    svc: QueueService = queue_api.get_queue(app)
    before = len(svc._listeners)
    with client.websocket_connect("/ws/queue") as ws:
        ws.receive_json()
        assert len(svc._listeners) == before + 1
    assert wait_for(lambda: len(svc._listeners) == before)


def test_persist_write_through_uses_database_schema(monkeypatch):
    """QUEUE_PERSIST=1 writes to DATABASE_SCHEMA when set (live tests use throwaway ones)."""
    from agent_assist_server import queue_store

    made = []
    monkeypatch.setattr(queue_store.QueueStore, "__init__", lambda self, **kw: made.append(kw))
    monkeypatch.setattr(queue_store.QueueStore, "on_event", lambda self, e: None)
    monkeypatch.setenv("QUEUE_PERSIST", "1")
    monkeypatch.setenv("DATABASE_SCHEMA", "test_abc")
    try:
        queue_api.get_queue(app)
        assert made == [{"schema": "test_abc"}]
    finally:
        delattr(app.state, "queue_service")


# ---- token prefetch on offer (P3-5: accept to audio under 2 s) -----------------------


async def test_prefetching_minter_mints_on_offer_and_hands_it_over_on_accept():
    calls = []

    async def mint(room_name, user_name):
        calls.append((room_name, user_name))
        return f"tok-{len(calls)}", 99

    svc = QueueService(mint_token=lambda r, u: minter(r, u))
    minter = queue_api.PrefetchingMinter(mint, svc)
    svc.subscribe(minter.on_event)
    svc.register_session("s1", ROOM_URL)
    agent = svc.sign_in("Ana")
    svc.set_status(agent.agent_id, "available")
    svc.enqueue_call("s1", ROOM_URL, {"reason": "x"})  # offer -> prefetch starts
    await asyncio.sleep(0)
    assert calls == [("caller-abc", "Ana")]
    res = await svc.accept(agent.agent_id, "s1")
    assert res.token == "tok-1"
    assert calls == [("caller-abc", "Ana")]  # no second mint


async def test_prefetching_minter_falls_back_when_prefetch_failed():
    calls = []

    async def mint(room_name, user_name):
        calls.append(room_name)
        if len(calls) == 1:
            raise RuntimeError("daily hiccup")
        return "tok-2", 99

    svc = QueueService(mint_token=lambda r, u: minter(r, u))
    minter = queue_api.PrefetchingMinter(mint, svc)
    svc.subscribe(minter.on_event)
    agent = svc.sign_in("Ana")
    svc.set_status(agent.agent_id, "available")
    svc.enqueue_call("s1", ROOM_URL, {})
    res = await svc.accept(agent.agent_id, "s1")
    assert res.token == "tok-2"
    assert len(calls) == 2


async def test_prefetched_tokens_are_dropped_when_the_offer_is_missed():
    async def mint(room_name, user_name):
        return "tok", 99

    svc = QueueService(mint_token=lambda r, u: minter(r, u))
    minter = queue_api.PrefetchingMinter(mint, svc)
    svc.subscribe(minter.on_event)
    agent = svc.sign_in("Ana")
    svc.set_status(agent.agent_id, "available")
    svc.enqueue_call("s1", ROOM_URL, {})
    assert len(minter.pending) == 1
    svc.decline(agent.agent_id, "s1")
    assert minter.pending == {}
