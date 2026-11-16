from agent_assist_server.app import create_app
from fastapi.testclient import TestClient


def test_root_redirects_to_agent_portal():
    client = TestClient(create_app(env={}))
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/agent"


def test_root_behind_login_goes_to_login_with_next():
    env = {"APP_PASSWORD": "pw", "SESSION_SECRET": "s" * 32}
    client = TestClient(create_app(env=env))
    r = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"].startswith("/login")
