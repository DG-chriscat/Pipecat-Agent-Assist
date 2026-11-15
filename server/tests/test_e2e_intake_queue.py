"""Intake end to queue to agent offer (P2-5), offline.

The real intake Flow handlers run against an in-memory call store, the bot's
QueueHandoff posts to the real FastAPI app over ASGI, and an agent on /ws/queue gets
the offer with the intake summary. Daily is mocked. The voice-driven version of the
same path is server/tests/test_e2e_live.py.
"""

import datetime as dt

import httpx
import pytest
from agent_assist_bot.calls import Member, MemoryCallStore
from agent_assist_bot.handoff import QueueHandoff
from agent_assist_bot.intake import IntakeSession
from agent_assist_bot.packs import load_pack
from agent_assist_server import queue_api
from agent_assist_server.app import app
from agent_assist_server.daily import DailyRestClient
from fastapi.testclient import TestClient

ROOM_URL = "https://dg-chriscat.daily.co/caller-e2e"
MARIA = Member("EVM100231", "Maria", "Alvarez", dt.date(1984, 3, 12))


def daily_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/meeting-tokens"):
        return httpx.Response(200, json={"token": "tok-agent"})
    if request.url.path.endswith("/send-app-message"):
        return httpx.Response(200, json={"status": "sent"})
    return httpx.Response(404)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("QUEUE_PERSIST", raising=False)
    app.state.daily_client_factory = lambda: DailyRestClient(
        api_key="test-key", transport=httpx.MockTransport(daily_handler)
    )
    with TestClient(app) as c:
        queue_api.get_queue(app).register_session("sess-e2e", ROOM_URL)
        yield c
    for attr in ("daily_client_factory", "queue_service"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def fn(node, name):
    return next(f for f in node["functions"] if f.name == name)


async def run_intake(session: IntakeSession) -> None:
    """Drive the Flow the way the LLM would for Maria Alvarez."""
    await session.start()
    _, node = await fn(session.identity_node(), "verify_identity").handler(
        {"full_name": "Maria Alvarez", "date_of_birth": "1984-03-12", "member_id": "EVM 100231"},
        None,
    )
    _, node = await fn(node, "save_reason").handler({"reason": "denied MRI claim"}, None)
    for answer in ("CLM-900001", "last month, Riverside Imaging", "denied, no prior auth"):
        _, node = await fn(node, "save_answer").handler({"answer": answer}, None)
    assert node["name"] == "readback"
    _, node = await fn(node, "confirm_summary").handler(
        {"summary": "Maria Alvarez, MRI claim CLM-900001 denied for no prior auth."}, None
    )
    assert node["name"] == "handoff"


async def test_finished_intake_is_queued_and_offered_with_its_summary(client):
    handoff = QueueHandoff("http://testserver", transport=httpx.ASGITransport(app=app))
    store = MemoryCallStore([MARIA])
    session = IntakeSession(
        pack=load_pack("claims_status"), store=store, session_id="sess-e2e", handoff=handoff
    )
    await run_intake(session)

    assert store.calls[session.call_id].status == "ready_for_queue"
    queue = client.get("/queue").json()["queue"]
    assert [c["session_id"] for c in queue] == ["sess-e2e"]
    call = queue[0]
    assert call["outcome"] == "completed"
    assert call["identity_result"] == "verified"
    assert call["member_id"] == "EVM100231"
    assert call["flags"] == []
    assert call["summary"]["claim_number"] == "CLM-900001"
    assert call["summary"]["summary"].startswith("Maria Alvarez, MRI claim")

    with client.websocket_connect("/ws/queue") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        ws.send_json({"type": "sign_in", "name": "Agent Ana"})
        agent = next(m for m in iter(ws.receive_json, None) if m["type"] == "signed_in")
        agent_id = agent["agent"]["agent_id"]
        ws.send_json({"type": "set_status", "agent_id": agent_id, "state": "available"})
        offer = next(m for m in iter(ws.receive_json, None) if m["type"] == "offer")
        assert offer["call"]["session_id"] == "sess-e2e"
        assert offer["call"]["summary"] == call["summary"]
        ws.send_json({"type": "accept", "agent_id": agent_id, "session_id": "sess-e2e"})
        accepted = next(m for m in iter(ws.receive_json, None) if m["type"] == "accepted")
    assert accepted["room_url"] == ROOM_URL
    assert accepted["token"] == "tok-agent"
    assert accepted["summary"] == call["summary"]
