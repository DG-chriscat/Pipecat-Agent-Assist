"""Wrap up (P3-5, AP-9 placeholder) and agent drop (QH-9) over the API, Daily mocked."""

import json
import time

import httpx
import pytest
from agent_assist_server import auth, queue_api, wrap_up_api
from agent_assist_server.app import app, create_app
from agent_assist_server.daily import DailyRestClient
from fastapi.testclient import TestClient

ROOM_URL = "https://dg-chriscat.daily.co/caller-abc"
MARIA = {"full_name": "Maria Alvarez", "reason": "MRI claim denied", "summary": "Denied MRI."}


class DailyMock:
    def __init__(self):
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/meeting-tokens"):
            props = json.loads(request.content)["properties"]
            return httpx.Response(200, json={"token": f"tok-{props['user_name']}"})
        return httpx.Response(200, json={"status": "sent"})

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
    monkeypatch.delenv(wrap_up_api.PACK_ENV, raising=False)
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


def wait_for(pred, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def on_call(client, name="Ana", session_id="s1") -> str:
    agent_id = client.post("/agents/sign-in", json={"name": name}).json()["agent_id"]
    client.post("/queue/calls", json={"session_id": session_id, "summary": MARIA})
    client.post(f"/agents/{agent_id}/status", json={"state": "available"})
    r = client.post(f"/agents/{agent_id}/accept", json={"session_id": session_id})
    assert r.status_code == 200, r.text
    return agent_id


def wrap_up(client, agent_id, session_id="s1", reason_code="denial_explained", notes="ok"):
    return client.post(
        f"/agents/{agent_id}/wrap-up",
        json={"session_id": session_id, "reason_code": reason_code, "notes": notes},
    )


# ---- reason codes come from the pack ---------------------------------------------


def test_reason_codes_match_the_bot_pack():
    from agent_assist_bot.packs import load_pack

    pack = load_pack("claims_status")
    want = [{"code": c.code, "label": c.label} for c in pack.wrap_up.reason_codes]
    assert wrap_up_api.reason_codes("claims_status") == want


def test_reason_codes_follow_the_intake_pack_env(monkeypatch, tmp_path):
    (tmp_path / "tiny.yaml").write_text(
        "wrap_up:\n  reason_codes:\n    - {code: resolved, label: Resolved}\n"
    )
    monkeypatch.setattr(wrap_up_api, "PACKS_DIR", tmp_path)
    monkeypatch.setenv(wrap_up_api.PACK_ENV, "tiny")
    assert wrap_up_api.reason_codes() == [{"code": "resolved", "label": "Resolved"}]


def test_pack_without_wrap_up_offers_other(monkeypatch, tmp_path):
    (tmp_path / "bare.yaml").write_text("id: bare\n")
    monkeypatch.setattr(wrap_up_api, "PACKS_DIR", tmp_path)
    assert wrap_up_api.reason_codes("bare") == [{"code": "other", "label": "Other"}]


# ---- GET /calls/{id}/wrap-up --------------------------------------------------------


def test_wrap_up_view_after_caller_hangs_up(client):
    agent_id = on_call(client)
    client.post("/queue/calls/s1/end")
    r = client.get("/calls/s1/wrap-up", params={"agent_id": agent_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == "s1"
    assert body["ended_by"] == "caller"
    assert body["caller_name"] == "Maria Alvarez"
    assert body["reason"] == "MRI claim denied"
    assert body["duration_secs"] >= 0
    assert body["wrap_up"] is None
    assert {"code": "other", "label": "Other"} in body["reason_codes"]
    assert body["summary_draft"] is None  # AP-9 drafts it in Phase 4


def test_wrap_up_view_only_for_that_agent(client):
    on_call(client)
    ben = client.post("/agents/sign-in", json={"name": "Ben"}).json()["agent_id"]
    client.post("/queue/calls/s1/end")
    assert client.get("/calls/s1/wrap-up", params={"agent_id": ben}).status_code == 403
    assert client.get("/calls/nope/wrap-up", params={"agent_id": ben}).status_code == 404


# ---- POST /agents/{id}/wrap-up --------------------------------------------------------


def test_save_wrap_up_then_available(client):
    agent_id = on_call(client)
    client.post(f"/agents/{agent_id}/status", json={"state": "wrap_up"})
    r = wrap_up(client, agent_id, notes="  Explained the PA-01 denial.  ")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["wrap_up"]["reason_code"] == "denial_explained"
    assert body["wrap_up"]["notes"] == "Explained the PA-01 denial."
    assert body["ended_by"] == "agent"
    agent = client.get("/queue").json()["agents"][0]
    assert agent["state"] == "available"
    assert agent["call_id"] is None


def test_save_wrap_up_rejects_unknown_reason_code(client):
    agent_id = on_call(client)
    client.post("/queue/calls/s1/end")
    r = wrap_up(client, agent_id, reason_code="made_up")
    assert r.status_code == 422
    assert client.get("/queue").json()["agents"][0]["state"] == "wrap_up"


def test_save_wrap_up_while_on_call_is_409(client):
    agent_id = on_call(client)
    assert wrap_up(client, agent_id).status_code == 409


def test_save_wrap_up_rejects_long_notes(client):
    agent_id = on_call(client)
    client.post("/queue/calls/s1/end")
    assert wrap_up(client, agent_id, notes="x" * 2001).status_code == 422


# ---- QH-9: agent drop ----------------------------------------------------------------


def test_agent_left_requeues_after_grace_and_tells_the_bot(client, daily, monkeypatch):
    svc = queue_api.get_queue(app)
    monkeypatch.setattr(svc, "_drop_grace", 0.05)
    agent_id = on_call(client)
    r = client.post("/queue/calls/s1/agent-left")
    assert r.status_code == 200
    assert client.get("/queue").json()["queue"] == []  # not yet: grace period
    assert wait_for(lambda: client.get("/queue").json()["queue"] != [])
    snap = client.get("/queue").json()
    assert snap["queue"][0]["session_id"] == "s1"
    assert snap["agents"][0] == {**snap["agents"][0], "agent_id": agent_id, "state": "offline"}
    assert wait_for(lambda: any(e["event"] == "requeued" for e in daily.bot_events()))
    requeued = [e for e in daily.bot_events() if e["event"] == "requeued"]
    assert requeued == [{"type": queue_api.BOT_MESSAGE_TYPE, "event": "requeued", "position": 1}]


def test_agent_left_after_end_does_nothing(client, monkeypatch):
    svc = queue_api.get_queue(app)
    monkeypatch.setattr(svc, "_drop_grace", 0.05)
    agent_id = on_call(client)
    client.post(f"/agents/{agent_id}/status", json={"state": "wrap_up"})
    assert client.post("/queue/calls/s1/agent-left").status_code == 200
    time.sleep(0.15)
    snap = client.get("/queue").json()
    assert snap["queue"] == []
    assert snap["agents"][0]["state"] == "wrap_up"


def test_bot_key_opens_agent_left():
    secret = "s" * 32
    env = {auth.PASSWORD_ENV: "pw", auth.SECRET_ENV: secret}
    with TestClient(create_app(env)) as c:
        hdr = {"authorization": f"Bearer {auth.bot_key(secret)}"}
        assert c.post("/queue/calls/s9/agent-left", headers=hdr).status_code == 200
        assert c.post("/queue/calls/s9/agent-left").status_code == 401
        assert c.post("/agents/a/wrap-up", headers=hdr, json={}).status_code == 401


# ---- agent End: the bot says goodbye, then the room closes ---------------------------


def test_agent_end_tells_the_bot_then_closes_the_room(client, daily, monkeypatch):
    monkeypatch.setattr(queue_api, "ROOM_CLOSE_DELAY_SECS", 0.05)
    agent_id = on_call(client)
    client.post(f"/agents/{agent_id}/status", json={"state": "wrap_up"})
    assert wait_for(lambda: any(e["event"] == "call_ended" for e in daily.bot_events()))
    assert wait_for(
        lambda: any(
            r.method == "DELETE" and r.url.path.endswith("/rooms/caller-abc")
            for r in daily.requests
        )
    )


def test_caller_hang_up_does_not_close_the_room_or_message_the_bot(client, daily, monkeypatch):
    monkeypatch.setattr(queue_api, "ROOM_CLOSE_DELAY_SECS", 0.01)
    on_call(client)
    client.post("/queue/calls/s1/end")
    time.sleep(0.2)
    assert not any(e["event"] == "call_ended" for e in daily.bot_events())
    assert not any(r.method == "DELETE" for r in daily.requests)
