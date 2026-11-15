"""Dial-in webhook -> Daily room -> bot launch, with Daily's REST API mocked."""

import base64
import json

import httpx
import pytest
from agent_assist_server import dialin
from agent_assist_server.app import app
from agent_assist_server.bot_launcher import (
    TOKEN_ENV,
    BotLaunchRequest,
    build_bot_command,
    build_bot_env,
)
from agent_assist_server.daily import (
    DailyRestClient,
    build_dialin_room_body,
    build_token_body,
)
from fastapi.testclient import TestClient

FAKE_KEY = "test-daily-key-not-real"
CALL = {
    "callId": "11111111-2222-3333-4444-555555555555",
    "callDomain": "66666666-7777-8888-9999-000000000000",
    "To": "+15550001111",
    "From": "+15552223333",
}


class FakeLauncher:
    def __init__(self):
        self.launched: list[BotLaunchRequest] = []

    async def launch(self, req):
        self.launched.append(req)
        return 4242


class DailyMock:
    """Records requests and answers like Daily's REST API."""

    def __init__(self, room_status=200):
        self.requests: list[httpx.Request] = []
        self.room_status = room_status

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content or b"{}")
        if request.url.path.endswith("/rooms"):
            if self.room_status != 200:
                return httpx.Response(self.room_status, json={"error": "nope"})
            name = body["name"]
            return httpx.Response(
                200,
                json={
                    "id": "room-id",
                    "name": name,
                    "url": f"https://dg-chriscat.daily.co/{name}",
                    "config": {"sip_endpoint": f"sip:{name}@daily.example"},
                },
            )
        if request.url.path.endswith("/meeting-tokens"):
            return httpx.Response(200, json={"token": "meeting-token-abc"})
        return httpx.Response(404)

    def bodies(self, suffix):
        return [json.loads(r.content) for r in self.requests if r.url.path.endswith(suffix)]


@pytest.fixture
def daily_mock():
    return DailyMock()


@pytest.fixture
def launcher():
    return FakeLauncher()


@pytest.fixture
def client(daily_mock, launcher, monkeypatch):
    monkeypatch.delenv(dialin.HMAC_ENV, raising=False)
    app.state.daily_client_factory = lambda: DailyRestClient(
        api_key=FAKE_KEY,
        api_url="https://api.daily.co/v1",
        transport=httpx.MockTransport(daily_mock.handler),
    )
    app.state.bot_launcher = launcher
    yield TestClient(app)
    del app.state.daily_client_factory
    del app.state.bot_launcher


# --- webhook parsing -------------------------------------------------------


def test_parse_dialin_payload():
    call = dialin.DialinWebhook.model_validate({**CALL, "sipHeaders": {"x-a": "1"}})
    assert call.call_id == CALL["callId"]
    assert call.call_domain == CALL["callDomain"]
    assert call.to == CALL["To"]
    assert call.from_ == CALL["From"]
    assert call.sip_headers == {"x-a": "1"}


def test_parse_allows_missing_to_from():
    call = dialin.DialinWebhook.model_validate({"callId": "a", "callDomain": "b"})
    assert call.to is None and call.from_ is None


@pytest.mark.parametrize(
    "payload",
    [{}, {"callId": "a"}, {"callDomain": "b"}, {"callId": "", "callDomain": "b"}],
)
def test_missing_required_fields_is_400(client, launcher, daily_mock, payload):
    r = client.post(dialin.DIALIN_WEBHOOK_PATH, json=payload)
    assert r.status_code == 400
    assert launcher.launched == []
    assert daily_mock.requests == []


def test_invalid_json_is_400(client):
    r = client.post(
        dialin.DIALIN_WEBHOOK_PATH,
        content=b"not json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400


# --- test ping -------------------------------------------------------------


@pytest.mark.parametrize("payload", [{"test": "test"}, {"Test": True}])
def test_verification_ping_returns_ok_without_side_effects(client, launcher, daily_mock, payload):
    r = client.post(dialin.DIALIN_WEBHOOK_PATH, json=payload)
    assert r.status_code == 200
    assert r.json() == {"status": "OK"}
    assert daily_mock.requests == []
    assert launcher.launched == []


# --- full flow: webhook -> room -> token -> bot -----------------------------


def test_full_flow_creates_room_token_and_launches_bot(client, launcher, daily_mock):
    r = client.post(dialin.DIALIN_WEBHOOK_PATH, json=CALL)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "OK"
    assert out["dailyRoom"].startswith("https://dg-chriscat.daily.co/dialin-")
    assert "token" not in json.dumps(out).lower()

    # Daily REST calls, in order, authenticated with the key
    paths = [req.url.path for req in daily_mock.requests]
    assert paths == ["/v1/rooms", "/v1/meeting-tokens"]
    for req in daily_mock.requests:
        assert req.method == "POST"
        assert req.headers["authorization"] == f"Bearer {FAKE_KEY}"

    # Room creation request shape: SIP dial-in, audio only, expiring
    (room_body,) = daily_mock.bodies("/rooms")
    props = room_body["properties"]
    assert props["sip"] == {
        "display_name": CALL["From"],
        "video": False,
        "sip_mode": "dial-in",
        "num_endpoints": 1,
    }
    assert props["start_video_off"] is True
    assert props["eject_at_room_exp"] is True
    assert isinstance(props["exp"], int)

    (token_body,) = daily_mock.bodies("/meeting-tokens")
    assert token_body["properties"]["room_name"] == room_body["name"]
    assert token_body["properties"]["is_owner"] is True

    # Bot launch arguments
    (req,) = launcher.launched
    assert req.room_url == out["dailyRoom"]
    assert req.token == "meeting-token-abc"
    assert req.call_id == CALL["callId"]
    assert req.call_domain == CALL["callDomain"]
    assert req.session_id == out["sessionId"]


def test_daily_failure_returns_502_and_no_bot(launcher, monkeypatch):
    monkeypatch.delenv(dialin.HMAC_ENV, raising=False)
    mock = DailyMock(room_status=403)
    app.state.daily_client_factory = lambda: DailyRestClient(
        api_key=FAKE_KEY, transport=httpx.MockTransport(mock.handler)
    )
    app.state.bot_launcher = launcher
    try:
        r = TestClient(app).post(dialin.DIALIN_WEBHOOK_PATH, json=CALL)
    finally:
        del app.state.daily_client_factory
        del app.state.bot_launcher
    assert r.status_code == 502
    assert FAKE_KEY not in r.text
    assert launcher.launched == []


def test_missing_daily_key_returns_500(launcher, monkeypatch):
    monkeypatch.delenv("DAILY_API_KEY", raising=False)
    monkeypatch.delenv(dialin.HMAC_ENV, raising=False)
    app.state.bot_launcher = launcher
    try:
        r = TestClient(app).post(dialin.DIALIN_WEBHOOK_PATH, json=CALL)
    finally:
        del app.state.bot_launcher
    assert r.status_code == 500
    assert launcher.launched == []


# --- HMAC (optional, enabled by DAILY_DIALIN_HMAC) ---------------------------

SECRET = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()


def _signed(payload, ts="1700000000"):
    body = json.dumps(payload, separators=(",", ":"))
    return body, {
        "content-type": "application/json",
        "X-Pinless-Timestamp": ts,
        "X-Pinless-Signature": dialin.compute_signature(SECRET, ts, body),
    }


def test_hmac_valid_signature_accepted(client, launcher, monkeypatch):
    monkeypatch.setenv(dialin.HMAC_ENV, SECRET)
    body, headers = _signed(CALL)
    r = client.post(dialin.DIALIN_WEBHOOK_PATH, content=body, headers=headers)
    assert r.status_code == 200
    assert len(launcher.launched) == 1


def test_hmac_signed_test_ping_accepted(client, monkeypatch):
    monkeypatch.setenv(dialin.HMAC_ENV, SECRET)
    body, headers = _signed({"test": "test"})
    r = client.post(dialin.DIALIN_WEBHOOK_PATH, content=body, headers=headers)
    assert r.json() == {"status": "OK"}


def test_hmac_bad_signature_rejected(client, launcher, daily_mock, monkeypatch):
    monkeypatch.setenv(dialin.HMAC_ENV, SECRET)
    body, headers = _signed(CALL)
    headers["X-Pinless-Signature"] = "bogus"
    r = client.post(dialin.DIALIN_WEBHOOK_PATH, content=body, headers=headers)
    assert r.status_code == 401
    assert daily_mock.requests == [] and launcher.launched == []


def test_hmac_missing_headers_rejected(client, monkeypatch):
    monkeypatch.setenv(dialin.HMAC_ENV, SECRET)
    r = client.post(dialin.DIALIN_WEBHOOK_PATH, json=CALL)
    assert r.status_code == 401


# --- request builders and launcher ----------------------------------------


def test_room_body_defaults():
    body = build_dialin_room_body(caller=None, now=1000.0, room_name="r1")
    assert body["name"] == "r1"
    assert body["properties"]["exp"] == 1000 + 3600
    assert body["properties"]["sip"]["sip_mode"] == "dial-in"
    assert body["properties"]["sip"]["display_name"] == "dial-in caller"


def test_token_body():
    body = build_token_body(room_name="r1", now=1000.0)
    assert body["properties"]["room_name"] == "r1"
    assert body["properties"]["user_name"] == "Intake Bot"
    assert body["properties"]["exp"] == 1000 + 3600


def test_daily_client_repr_hides_key():
    c = DailyRestClient(api_key=FAKE_KEY)
    assert FAKE_KEY not in repr(c)


LAUNCH = BotLaunchRequest(
    room_url="https://dg-chriscat.daily.co/r1",
    token="secret-token",
    call_id="cid",
    call_domain="cdom",
    session_id="sid",
)


def test_bot_command_args():
    cmd = build_bot_command(LAUNCH, python="/usr/bin/python3")
    assert cmd == [
        "/usr/bin/python3",
        "-m",
        "agent_assist_bot",
        "--room-url",
        "https://dg-chriscat.daily.co/r1",
        "--call-id",
        "cid",
        "--call-domain",
        "cdom",
        "--session-id",
        "sid",
    ]
    assert "secret-token" not in cmd


def test_bot_env_carries_token_and_inherits_keys():
    env = build_bot_env(LAUNCH, base={"DEEPGRAM_API_KEY": "x", "PATH": "/bin"})
    assert env[TOKEN_ENV] == "secret-token"
    assert env["DEEPGRAM_API_KEY"] == "x"
    assert env["PATH"] == "/bin"
