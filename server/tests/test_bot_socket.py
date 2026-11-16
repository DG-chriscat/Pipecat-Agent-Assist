"""QH-5b: the bot's own websocket (/ws/bot/{session_id}). The server pushes
agent_accepted, requeued and call_ended on it, with the same message id as the Daily
app message that still goes out as the fallback. Daily mocked."""

import json
import time

import httpx
import pytest
from agent_assist_server import auth, queue_api
from agent_assist_server.app import app, create_app
from agent_assist_server.daily import DailyRestClient
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

ROOM_URL = "https://dg-chriscat.daily.co/caller-abc"


class DailyMock:
    def __init__(self):
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/meeting-tokens"):
            props = json.loads(request.content)["properties"]
            return httpx.Response(200, json={"token": f"tok-{props['user_name']}"})
        return httpx.Response(200, json={"status": "sent", "total_count": 0, "data": []})

    def bot_events(self) -> list[dict]:
        return [
            json.loads(r.content)["data"]
            for r in self.requests
            if r.url.path.endswith("/send-app-message")
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
    for attr in ("daily_client_factory", "queue_service", "bot_channels"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def wait_for(pred, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def agent_takes(client, session_id="s1", name="Ana") -> str:
    agent_id = client.post("/agents/sign-in", json={"name": name}).json()["agent_id"]
    client.post("/agents/" + agent_id + "/status", json={"state": "available"})
    r = client.post(f"/agents/{agent_id}/accept", json={"session_id": session_id})
    assert r.status_code == 200, r.text
    return agent_id


def test_server_pushes_agent_accepted_on_the_bot_socket(client, daily):
    with client.websocket_connect("/ws/bot/s1") as ws:
        client.post("/queue/calls", json={"session_id": "s1", "summary": {}})
        agent_takes(client)
        msg = ws.receive_json()
        assert msg["type"] == queue_api.BOT_MESSAGE_TYPE
        assert msg["event"] == "agent_accepted"
        assert msg["agent_name"] == "Ana"
        # The Daily app message still goes out (fallback) with the same id, so the
        # bot acts on whichever arrives first and drops the other.
        assert wait_for(lambda: any(e["event"] == "agent_accepted" for e in daily.bot_events()))
        via_daily = next(e for e in daily.bot_events() if e["event"] == "agent_accepted")
        assert via_daily == msg
        assert isinstance(msg["id"], int)


def test_bot_socket_only_gets_its_own_session(client):
    with (
        client.websocket_connect("/ws/bot/s1") as ws1,
        client.websocket_connect("/ws/bot/s2") as ws2,
    ):
        client.post("/queue/calls", json={"session_id": "s1", "summary": {}})
        client.post("/queue/calls", json={"session_id": "s2", "summary": {}})
        agent_takes(client, "s1")
        assert ws1.receive_json()["event"] == "agent_accepted"
        # s2 heard nothing: end its call by the agent path to get a message we can wait on.
        agent2 = agent_takes(client, "s2", name="Bo")
        msg = ws2.receive_json()
        assert msg["event"] == "agent_accepted" and msg["agent_name"] == "Bo"
        assert agent2


def test_server_pushes_requeued_and_call_ended(client, monkeypatch):
    svc = queue_api.get_queue(app)
    monkeypatch.setattr(svc, "_drop_grace", 0.0)
    with client.websocket_connect("/ws/bot/s1") as ws:
        client.post("/queue/calls", json={"session_id": "s1", "summary": {}})
        agent_takes(client)
        assert ws.receive_json()["event"] == "agent_accepted"
        client.post("/queue/calls/s1/agent-left")
        requeued = ws.receive_json()
        assert requeued["event"] == "requeued" and requeued["position"] == 1
        agent_id = agent_takes(client, name="Bo")
        assert ws.receive_json()["event"] == "agent_accepted"
        client.post(f"/agents/{agent_id}/status", json={"state": "wrap_up"})
        assert ws.receive_json()["event"] == "call_ended"


def test_bot_socket_unknown_session_closes_4404(client):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws/bot/nope") as ws:
            ws.receive_json()
    assert e.value.code == 4404


def test_bot_socket_disconnect_unregisters(client):
    channels = queue_api.get_bot_channels(app)
    with client.websocket_connect("/ws/bot/s1"):
        assert wait_for(lambda: channels.count("s1") == 1)
    assert wait_for(lambda: channels.count("s1") == 0)


# ---- login on (P2-6): bot key only ----------------------------------------------------

SECRET = "s" * 32
ENV = {auth.PASSWORD_ENV: "pw", auth.SECRET_ENV: SECRET}


def test_bot_socket_needs_the_bot_key_when_login_is_on():
    secured = create_app(ENV)
    with TestClient(secured, base_url="http://localhost") as c:
        queue_api.get_queue(secured).register_session("s1", ROOM_URL)
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect("/ws/bot/s1") as ws:
                ws.receive_json()
        assert e.value.code == auth.WS_UNAUTHORIZED
        bad = {"authorization": "Bearer wrong"}
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect("/ws/bot/s1", headers=bad) as ws:
                ws.receive_json()
        assert e.value.code == auth.WS_UNAUTHORIZED
        hdr = {"authorization": f"Bearer {auth.bot_key(SECRET)}"}
        with c.websocket_connect("/ws/bot/s1", headers=hdr) as ws:
            ws.send_text("ping")  # the socket is open; the server ignores input


def test_bot_socket_refuses_a_logged_in_browser():
    # Bot only: a portal session cookie does not open it.
    secured = create_app(ENV)
    with TestClient(secured, base_url="http://localhost") as c:
        queue_api.get_queue(secured).register_session("s1", ROOM_URL)
        r = c.post("/login", data={"password": "pw"}, follow_redirects=False)
        assert r.status_code == 303
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect("/ws/bot/s1") as ws:
                ws.receive_json()
        assert e.value.code == auth.WS_UNAUTHORIZED


def test_bot_key_does_not_open_the_agent_queue_socket():
    secured = create_app(ENV)
    with TestClient(secured, base_url="http://localhost") as c:
        hdr = {"authorization": f"Bearer {auth.bot_key(SECRET)}"}
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect("/ws/queue", headers=hdr) as ws:
                ws.receive_json()
        assert e.value.code == auth.WS_UNAUTHORIZED
