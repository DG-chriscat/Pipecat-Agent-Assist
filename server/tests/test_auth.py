"""Shared password login (P2-6)."""

from __future__ import annotations

import logging

import pytest
from agent_assist_server import auth
from agent_assist_server.app import create_app
from agent_assist_server.bot_launcher import BotLaunchRequest, build_bot_env
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

PASSWORD = "correct horse battery staple"
SECRET = "test-session-secret-0123456789abcdef"
ENV = {auth.PASSWORD_ENV: PASSWORD, auth.SECRET_ENV: SECRET}
WS_URL = "ws://localhost/ws/queue"
HTML = {"accept": "text/html,application/xhtml+xml"}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app(clock):
    return create_app(env=ENV, clock=clock)


@pytest.fixture
def client(app):
    # localhost: the cookie is not Secure, so the test client sends it over http.
    return TestClient(app, base_url="http://localhost")


def login(client, password=PASSWORD, next_=None, **kw):
    url = "/login" if next_ is None else f"/login?next={next_}"
    return client.post(url, data={"password": password}, follow_redirects=False, **kw)


# ---- config -----------------------------------------------------------------------


def test_auth_disabled_when_password_unset(caplog):
    with caplog.at_level(logging.WARNING, logger="agent_assist_server.auth"):
        app = create_app(env={})
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert auth.PASSWORD_ENV in warnings[0].getMessage()
    c = TestClient(app)
    assert c.get("/queue").status_code == 200
    assert c.get("/caller", headers=HTML).status_code == 200


def test_refuses_to_start_without_session_secret():
    with pytest.raises(RuntimeError, match=auth.SECRET_ENV):
        create_app(env={auth.PASSWORD_ENV: PASSWORD})


def test_secrets_never_logged(caplog, clock):
    with caplog.at_level(logging.DEBUG):
        app = create_app(env=ENV, clock=clock)
        c = TestClient(app, base_url="http://localhost")
        login(c, password="nope-123")
        login(c)
    text = caplog.text
    assert PASSWORD not in text and SECRET not in text and "nope-123" not in text


# ---- unauthenticated access -------------------------------------------------------


def test_html_request_redirects_to_login_with_next(client):
    r = client.get("/caller?x=1", headers=HTML, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=%2Fcaller%3Fx%3D1"


def test_api_request_gets_401(client):
    r = client.get("/queue")
    assert r.status_code == 401
    assert r.json() == {"detail": "Not authenticated"}
    assert client.post("/agents/sign-in", json={"name": "Ana"}).status_code == 401
    assert client.post("/calls/browser", headers=HTML).status_code == 401


def test_websocket_closes_4401_without_cookie(client):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(WS_URL) as ws:
            ws.receive_json()
    assert e.value.code == 4401


def test_websocket_rejects_forged_cookie(client):
    client.cookies.set(auth.COOKIE_NAME, "forged.value.sig", domain="localhost")
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(WS_URL) as ws:
            ws.receive_json()
    assert e.value.code == 4401


def test_healthz_and_dialin_exempt(client):
    assert client.get("/healthz").json()["status"] == "ok"
    r = client.post("/daily/dialin", json={"test": "test"})
    assert r.status_code == 200 and r.json() == {"status": "OK"}


def test_login_page_and_assets_public(client):
    r = client.get("/login", headers=HTML)
    assert r.status_code == 200
    assert 'type="password"' in r.text and "Sign in" in r.text
    assert "!" not in r.text.split("<body", 1)[1].replace("<!--", "")
    for name in ("d-tile.png", "wordmark.png"):
        a = client.get(f"/login/assets/{name}")
        assert a.status_code == 200 and a.headers["content-type"] == "image/png"
    assert client.get("/login/assets/login.html").status_code == 404


# ---- login --------------------------------------------------------------------------


def test_good_password_sets_cookie_and_redirects_to_next(client):
    r = login(client, next_="/caller")
    assert r.status_code == 303
    assert r.headers["location"] == "/caller"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{auth.COOKIE_NAME}=")
    low = cookie.lower()
    assert "httponly" in low and "samesite=lax" in low
    assert f"max-age={12 * 3600}" in low
    assert "secure" not in low  # localhost
    assert client.get("/queue").status_code == 200
    assert client.get("/caller", headers=HTML).status_code == 200


def test_cookie_is_secure_on_public_host(app):
    c = TestClient(app, base_url="https://agent-assist.fly.dev")
    r = login(c)
    assert "secure" in r.headers["set-cookie"].lower()


def test_websocket_works_with_cookie(client):
    login(client)
    with client.websocket_connect(WS_URL) as ws:
        assert ws.receive_json()["type"] == "snapshot"


def test_bad_password_rerenders_with_generic_error(client):
    r = login(client, password="nope")
    assert r.status_code == 401
    assert "set-cookie" not in r.headers
    assert "That password did not work" in r.text
    assert 'type="password"' in r.text
    assert client.get("/queue").status_code == 401


def test_cookie_expires_after_12_hours(client, clock):
    login(client)
    clock.now += 12 * 3600 - 5
    assert client.get("/queue").status_code == 200
    clock.now += 10
    assert client.get("/queue").status_code == 401


def test_cookie_signed_with_other_secret_rejected(clock):
    other = create_app(env={**ENV, auth.SECRET_ENV: "another-secret-another-secret"}, clock=clock)
    c1 = TestClient(other, base_url="http://localhost")
    login(c1)
    value = c1.cookies.get(auth.COOKIE_NAME)
    c2 = TestClient(create_app(env=ENV, clock=clock), base_url="http://localhost")
    c2.cookies.set(auth.COOKIE_NAME, value, domain="localhost")
    assert c2.get("/queue").status_code == 401


@pytest.mark.parametrize(
    "next_,expected",
    [
        ("/caller", "/caller"),
        ("/spike/agent?room=a", "/spike/agent?room=a"),
        ("https://evil.example/", "/caller"),
        ("//evil.example/", "/caller"),
        ("/\\evil.example", "/caller"),
        ("javascript:alert(1)", "/caller"),
        ("caller", "/caller"),
        ("/%0d%0aSet-Cookie:x=1", "/caller"),
        ("", "/caller"),
    ],
)
def test_open_redirect_blocked(client, next_, expected):
    r = client.post(
        "/login", params={"next": next_}, data={"password": PASSWORD}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"] == expected


def test_safe_next_helper():
    assert auth.safe_next("/a/b?c=d") == "/a/b?c=d"
    assert auth.safe_next("/\tevil") == auth.DEFAULT_NEXT
    assert auth.safe_next(None) == auth.DEFAULT_NEXT


def test_login_form_keeps_next(client):
    r = client.get("/login?next=/caller", headers=HTML)
    assert 'action="/login?next=%2Fcaller"' in r.text


def test_logout_clears_cookie(client):
    login(client)
    r = client.get("/logout", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert auth.COOKIE_NAME in r.headers["set-cookie"]
    assert client.get("/queue").status_code == 401


# ---- rate limit ---------------------------------------------------------------------


def test_rate_limit_after_10_failures_per_ip(client, clock):
    for _ in range(10):
        assert login(client, password="nope").status_code == 401
    r = login(client, password="nope")
    assert r.status_code == 429
    # Blocked even with the right password until the window passes.
    assert login(client).status_code == 429
    clock.now += 601
    assert login(client).status_code == 303


def test_rate_limit_is_per_ip(app):
    a = TestClient(app, base_url="http://localhost", client=("10.0.0.1", 1))
    b = TestClient(app, base_url="http://localhost", client=("10.0.0.2", 1))
    for _ in range(11):
        login(a, password="nope")
    assert login(a).status_code == 429
    assert login(b).status_code == 303


def test_rate_limiter_window_slides(clock):
    rl = auth.RateLimiter(max_failures=2, window_s=10, clock=clock)
    rl.record_failure("ip")
    clock.now += 6
    rl.record_failure("ip")
    assert rl.blocked("ip")
    clock.now += 5  # first failure aged out
    assert not rl.blocked("ip")


# ---- two people at once -------------------------------------------------------------


def test_two_browsers_have_independent_sessions(app):
    caller = TestClient(app, base_url="http://localhost")
    agent = TestClient(app, base_url="http://localhost")
    login(caller, next_="/caller")
    login(agent)
    assert caller.get("/caller", headers=HTML).status_code == 200
    assert agent.post("/agents/sign-in", json={"name": "Ana"}).status_code == 200
    agent.get("/logout")
    assert agent.get("/queue").status_code == 401
    assert caller.get("/queue").status_code == 200


# ---- bot to server ------------------------------------------------------------------


def test_bot_key_allows_queue_handoff_only(client, app):
    key = auth.bot_key(SECRET)
    hdr = {"authorization": f"Bearer {key}"}
    assert client.post("/queue/calls/s9/end", headers=hdr).status_code == 200
    r = client.post("/queue/calls", json={"session_id": "nope"}, headers=hdr)
    assert r.status_code == 404  # reached the handler
    assert client.get("/queue", headers=hdr).status_code == 401
    bad = {"authorization": "Bearer wrong"}
    assert client.post("/queue/calls/s9/end", headers=bad).status_code == 401


def test_bot_env_gets_bot_key_not_secrets():
    req = BotLaunchRequest(room_url="https://x.daily.co/r", token="t", session_id="s")
    env = build_bot_env(req, base={**ENV, "PATH": "/bin"})
    assert env[auth.BOT_KEY_ENV] == auth.bot_key(SECRET)
    assert auth.PASSWORD_ENV not in env and auth.SECRET_ENV not in env
    env = build_bot_env(req, base={"PATH": "/bin"})
    assert auth.BOT_KEY_ENV not in env
