"""POST /calls/browser and GET /caller (PRD IN-12, ADR 0002), Daily and launcher mocked."""

import json

import httpx
import pytest
from agent_assist_server import browser_call
from agent_assist_server.app import app
from agent_assist_server.bot_launcher import BotLaunchRequest, build_bot_command
from agent_assist_server.daily import DailyRestClient, build_browser_room_body
from fastapi.testclient import TestClient

FAKE_KEY = "test-daily-key-not-real"


class FakeLauncher:
    def __init__(self):
        self.launched: list[BotLaunchRequest] = []

    async def launch(self, req):
        self.launched.append(req)
        return 4242


class DailyMock:
    """Answers like Daily's REST API; hands out a distinct token per meeting-token call."""

    def __init__(self, room_status=200, token_status=200):
        self.requests: list[httpx.Request] = []
        self.room_status = room_status
        self.token_status = token_status

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content or b"{}")
        if request.url.path.endswith("/rooms"):
            if self.room_status != 200:
                return httpx.Response(self.room_status, json={"error": "nope"})
            name = body["name"]
            return httpx.Response(
                200, json={"name": name, "url": f"https://dg-chriscat.daily.co/{name}"}
            )
        if request.url.path.endswith("/meeting-tokens"):
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "nope"})
            props = body["properties"]
            who = "bot" if props["is_owner"] else "caller"
            return httpx.Response(200, json={"token": f"{who}-token"})
        return httpx.Response(404)

    def bodies(self, suffix):
        return [json.loads(r.content) for r in self.requests if r.url.path.endswith(suffix)]


@pytest.fixture
def launcher():
    return FakeLauncher()


def _client(mock: DailyMock | None, launcher: FakeLauncher, key: str = FAKE_KEY) -> TestClient:
    if mock is not None:
        app.state.daily_client_factory = lambda: DailyRestClient(
            api_key=key, transport=httpx.MockTransport(mock.handler)
        )
    app.state.bot_launcher = launcher
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_app_state():
    yield
    for attr in ("daily_client_factory", "bot_launcher"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def test_browser_call_creates_room_tokens_and_starts_bot(launcher):
    mock = DailyMock()
    r = _client(mock, launcher).post(browser_call.BROWSER_CALL_PATH)
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out) == {"session_id", "room_url", "caller_token"}
    assert out["room_url"].startswith("https://dg-chriscat.daily.co/caller-")
    assert out["caller_token"] == "caller-token"

    paths = [req.url.path for req in mock.requests]
    assert paths == ["/v1/rooms", "/v1/meeting-tokens", "/v1/meeting-tokens"]
    for req in mock.requests:
        assert req.headers["authorization"] == f"Bearer {FAKE_KEY}"

    (room_body,) = mock.bodies("/rooms")
    props = room_body["properties"]
    assert "sip" not in props  # browser caller: no dial-in
    assert room_body["privacy"] == "private"
    assert props["eject_at_room_exp"] is True and isinstance(props["exp"], int)

    bot_body, caller_body = (b["properties"] for b in mock.bodies("/meeting-tokens"))
    assert bot_body["room_name"] == room_body["name"]
    assert bot_body["is_owner"] is True
    assert bot_body["user_name"] == "Intake Bot"
    assert caller_body["room_name"] == room_body["name"]
    assert caller_body["is_owner"] is False
    assert caller_body["user_name"] == "caller"
    assert isinstance(caller_body["exp"], int)

    (req,) = launcher.launched
    assert req.room_url == out["room_url"]
    assert req.token == "bot-token"
    assert req.call_id is None and req.call_domain is None
    assert req.session_id == out["session_id"]
    assert "bot-token" not in r.text


@pytest.mark.parametrize("which", ["room", "token"])
def test_daily_error_returns_502_and_no_bot(launcher, which):
    mock = DailyMock(**{f"{which}_status": 500})
    r = _client(mock, launcher).post(browser_call.BROWSER_CALL_PATH)
    assert r.status_code == 502
    assert FAKE_KEY not in r.text and "nope" not in r.text
    assert launcher.launched == []


def test_missing_daily_key_returns_503(launcher, monkeypatch):
    monkeypatch.delenv("DAILY_API_KEY", raising=False)
    r = _client(None, launcher).post(browser_call.BROWSER_CALL_PATH)
    assert r.status_code == 503
    assert launcher.launched == []


def test_caller_page_served():
    r = TestClient(app).get("/caller")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    html = r.text
    assert "@daily-co/daily-js" in html
    assert "/calls/browser" in html
    for button_id in ('id="start"', 'id="mute"', 'id="hangup"', 'id="status"'):
        assert button_id in html


def test_browser_room_body():
    body = build_browser_room_body(now=1000.0, room_name="r1")
    assert body == {
        "name": "r1",
        "privacy": "private",
        "properties": {
            "exp": 1000 + 3600,
            "eject_at_room_exp": True,
            "start_video_off": True,
        },
    }


def test_bot_command_without_dialin_omits_call_args():
    req = BotLaunchRequest(room_url="https://x.daily.co/r", token="secret", session_id="sid")
    cmd = build_bot_command(req, python="/usr/bin/python3")
    assert cmd == [
        "/usr/bin/python3",
        "-m",
        "agent_assist_bot",
        "--room-url",
        "https://x.daily.co/r",
        "--session-id",
        "sid",
    ]
