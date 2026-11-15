from agent_assist_server.app import app
from fastapi.testclient import TestClient


def test_app_title():
    assert app.title == "Deepgram Vitals"


def test_healthz_returns_ok():
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "agent-assist-server"}
