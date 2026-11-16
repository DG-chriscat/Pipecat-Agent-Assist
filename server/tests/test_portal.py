"""Agent portal served at /agent from portal/dist (P3-1)."""

from __future__ import annotations

import pytest
from agent_assist_server import auth
from agent_assist_server.app import create_app
from agent_assist_server.portal import PORTAL_DIST_ENV
from fastapi.testclient import TestClient

PASSWORD = "correct horse battery staple"
SECRET = "test-session-secret-0123456789abcdef"
HTML = {"accept": "text/html,application/xhtml+xml"}
INDEX = '<!doctype html><html><body><div id="root"></div>portal-index</body></html>'


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text(INDEX)
    (d / "assets" / "app.js").write_text("console.log('portal')")
    (tmp_path / "secret.txt").write_text("outside dist")
    return d


def make_client(env):
    return TestClient(create_app(env=env), base_url="http://localhost")


@pytest.fixture
def open_client(dist):
    return make_client({PORTAL_DIST_ENV: str(dist)})


@pytest.fixture
def auth_env(dist):
    return {auth.PASSWORD_ENV: PASSWORD, auth.SECRET_ENV: SECRET, PORTAL_DIST_ENV: str(dist)}


def test_agent_serves_index(open_client):
    r = open_client.get("/agent", headers=HTML)
    assert r.status_code == 200
    assert "portal-index" in r.text
    assert r.headers["content-type"].startswith("text/html")


def test_agent_trailing_slash_serves_index(open_client):
    r = open_client.get("/agent/", headers=HTML)
    assert r.status_code == 200
    assert "portal-index" in r.text


def test_agent_serves_static_asset(open_client):
    r = open_client.get("/agent/assets/app.js")
    assert r.status_code == 200
    assert "console.log('portal')" in r.text
    assert "javascript" in r.headers["content-type"]


def test_agent_spa_fallback_for_client_routes(open_client):
    r = open_client.get("/agent/home/anything", headers=HTML)
    assert r.status_code == 200
    assert "portal-index" in r.text


def test_agent_missing_asset_is_404_not_index(open_client):
    r = open_client.get("/agent/assets/missing.js")
    assert r.status_code == 404


def test_agent_does_not_serve_files_outside_dist(open_client):
    r = open_client.get("/agent/..%2Fsecret.txt", headers=HTML)
    assert "outside dist" not in r.text
    r = open_client.get("/agent/%2E%2E/secret.txt", headers=HTML)
    assert "outside dist" not in r.text


def test_agent_when_portal_not_built(tmp_path):
    c = make_client({PORTAL_DIST_ENV: str(tmp_path / "nope")})
    r = c.get("/agent", headers=HTML)
    assert r.status_code == 503
    assert "npm run build" in r.text


def test_agent_redirects_to_login_when_logged_out(auth_env):
    c = make_client(auth_env)
    r = c.get("/agent", headers=HTML, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["location"] == "/login?next=%2Fagent"
    r = c.get("/agent/assets/app.js", follow_redirects=False)
    assert r.status_code == 401


def test_agent_serves_index_behind_login(auth_env):
    c = make_client(auth_env)
    r = c.post("/login?next=/agent", data={"password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/agent"
    r = c.get("/agent", headers=HTML)
    assert r.status_code == 200
    assert "portal-index" in r.text
    assert c.get("/agent/assets/app.js").status_code == 200
