"""Spike S4 live test: a client joins a real Daily room with a token from our endpoint.

Needs DAILY_API_KEY and network access. Skipped by default (-m 'not live').
Run with: uv run pytest -m live server/tests/test_agent_token_live.py -s
"""

import os
import threading
import time
import uuid

import httpx
import pytest
from agent_assist_server.app import app
from fastapi.testclient import TestClient

pytestmark = pytest.mark.live

DAILY_API_URL = "https://api.daily.co/v1"
USER_NAME = "S4 Live Agent"


@pytest.fixture
def daily_api():
    key = os.environ.get("DAILY_API_KEY")
    if not key:
        pytest.skip("DAILY_API_KEY not set")
    with httpx.Client(
        base_url=DAILY_API_URL, headers={"Authorization": f"Bearer {key}"}, timeout=15.0
    ) as client:
        yield client


@pytest.fixture
def room(daily_api):
    name = f"s4-live-{uuid.uuid4().hex[:8]}"
    resp = daily_api.post(
        "/rooms",
        json={"name": name, "privacy": "private", "properties": {"exp": int(time.time()) + 600}},
    )
    assert resp.status_code == 200, resp.text
    room = resp.json()
    print(f"\ncreated room {room['name']} at {room['url']}")
    yield room
    deleted = daily_api.delete(f"/rooms/{name}")
    print(f"deleted room {name}: HTTP {deleted.status_code} {deleted.json()}")
    assert deleted.status_code == 200


def _wait(event: threading.Event, what: str, timeout: float = 20.0) -> None:
    assert event.wait(timeout), f"timed out waiting for {what}"


def test_browser_style_join_with_agent_token(daily_api, room):
    daily = pytest.importorskip("daily")

    # 1. Fetch a token from our endpoint (real Daily REST call, no mocks).
    resp = TestClient(app).post(f"/rooms/{room['name']}/agent-token", json={"user_name": USER_NAME})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    token = body["token"]
    print(f"endpoint returned token, expires_at={body['expires_at']}")

    # 2. Daily validates the token and reports the properties we asked for.
    props = daily_api.get(f"/meeting-tokens/{token}").json()
    assert props["room_name"] == room["name"]
    assert props["user_name"] == USER_NAME
    assert props.get("is_owner", False) is False
    assert props["exp"] == body["expires_at"]
    assert 3500 <= props["exp"] - int(time.time()) <= 3600
    print(f"token validated by Daily: room_name, user_name, is_owner=False, exp={props['exp']}")

    # 3. Join the private room with a daily-python client using only that token.
    daily.Daily.init()
    client = daily.CallClient()
    joined, left = threading.Event(), threading.Event()
    join_result: dict = {}

    def on_join(data, error):
        join_result["error"] = error
        joined.set()

    try:
        client.join(
            room["url"],
            meeting_token=token,
            client_settings={"inputs": {"camera": False, "microphone": False}},
            completion=on_join,
        )
        _wait(joined, "join")
        assert join_result["error"] is None, join_result["error"]

        local = client.participants()["local"]
        print(f"joined as local participant userName={local['info']['userName']!r}")
        assert local["info"]["userName"] == USER_NAME
        assert local["info"].get("isOwner", False) is False

        # 4. Server-side view: Daily presence shows the participant with that user_name.
        names: list[str] = []
        for _ in range(15):
            presence = daily_api.get(f"/rooms/{room['name']}/presence").json()
            names = [p.get("userName") for p in presence.get("data", [])]
            if USER_NAME in names:
                break
            time.sleep(1)
        print(f"Daily presence for room: {names}")
        assert USER_NAME in names
    finally:
        client.leave(completion=lambda error: left.set())
        left.wait(10)
        client.release()
