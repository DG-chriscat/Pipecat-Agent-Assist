import json
import time

import httpx
import pytest
from agent_assist_server.app import app, get_daily_http_client
from fastapi.testclient import TestClient

DAILY_TOKENS_URL = "https://api.daily.co/v1/meeting-tokens"


@pytest.fixture
def daily_calls():
    return []


@pytest.fixture
def make_client(daily_calls):
    """Build a TestClient whose Daily HTTP calls hit a mock transport."""

    def _make(handler):
        def recording_handler(request: httpx.Request) -> httpx.Response:
            daily_calls.append(request)
            return handler(request)

        async def override():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(recording_handler)
            ) as client:
                yield client

        app.dependency_overrides[get_daily_http_client] = override
        return TestClient(app)

    yield _make
    app.dependency_overrides.clear()


def test_agent_token_success(monkeypatch, make_client, daily_calls):
    monkeypatch.setenv("DAILY_API_KEY", "test-key")
    client = make_client(lambda req: httpx.Response(200, json={"token": "tok-123"}))

    before = int(time.time())
    response = client.post("/rooms/room-abc/agent-token", json={"user_name": "Agent Alice"})
    after = int(time.time())

    assert response.status_code == 200
    body = response.json()
    assert body["token"] == "tok-123"
    assert body["room_name"] == "room-abc"
    assert before + 3600 <= body["expires_at"] <= after + 3600

    assert len(daily_calls) == 1
    req = daily_calls[0]
    assert req.method == "POST"
    assert str(req.url) == DAILY_TOKENS_URL
    assert req.headers["Authorization"] == "Bearer test-key"
    props = json.loads(req.content)["properties"]
    assert props["room_name"] == "room-abc"
    assert props["user_name"] == "Agent Alice"
    assert props["is_owner"] is False
    assert props["exp"] == body["expires_at"]


def test_agent_token_daily_error_returns_502(monkeypatch, make_client):
    monkeypatch.setenv("DAILY_API_KEY", "test-key")
    client = make_client(
        lambda req: httpx.Response(
            400, json={"error": "invalid-request-error", "info": "room not found"}
        )
    )

    response = client.post("/rooms/nope/agent-token", json={"user_name": "Agent Alice"})

    assert response.status_code == 502
    assert "400" in response.json()["detail"]


def test_agent_token_daily_unreachable_returns_502(monkeypatch, make_client):
    monkeypatch.setenv("DAILY_API_KEY", "test-key")

    def boom(req):
        raise httpx.ConnectError("connection refused", request=req)

    client = make_client(boom)

    response = client.post("/rooms/room-abc/agent-token", json={"user_name": "Agent Alice"})

    assert response.status_code == 502


def test_agent_token_missing_key_returns_503(monkeypatch, make_client, daily_calls):
    monkeypatch.delenv("DAILY_API_KEY", raising=False)
    client = make_client(lambda req: httpx.Response(200, json={"token": "x"}))

    response = client.post("/rooms/room-abc/agent-token", json={"user_name": "Agent Alice"})

    assert response.status_code == 503
    assert "DAILY_API_KEY" in response.json()["detail"]
    assert daily_calls == []


def test_agent_token_requires_user_name(monkeypatch, make_client, daily_calls):
    monkeypatch.setenv("DAILY_API_KEY", "test-key")
    client = make_client(lambda req: httpx.Response(200, json={"token": "x"}))

    assert client.post("/rooms/room-abc/agent-token", json={}).status_code == 422
    assert client.post("/rooms/room-abc/agent-token", json={"user_name": ""}).status_code == 422
    assert daily_calls == []


def test_spike_agent_page_served():
    response = TestClient(app).get("/spike/agent")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert "@daily-co/daily-js" in html
    assert "/agent-token" in html
    assert 'id="mute"' in html
    assert 'id="participants"' in html
