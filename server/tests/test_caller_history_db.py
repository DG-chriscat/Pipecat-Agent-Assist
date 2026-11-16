"""Caller history from the seeded mock database (P3-3, AP-13, DC-8). Needs DATABASE_URL.

Each test seeds a throwaway schema at a fixed as_of date and drops it after. The
endpoint is exercised end to end with the real CallerHistory repo on that schema.
"""

from __future__ import annotations

import datetime as dt
import os
import uuid

import pytest
from agent_assist_server import queue_api
from agent_assist_server.app import create_app
from agent_assist_server.caller_api import CallerHistory
from agent_assist_server.db import connect, migrate, seed
from agent_assist_server.queue_service import IntakeInfo, QueueService
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")

AS_OF = dt.date(2026, 11, 15)
NOW = dt.datetime(2026, 11, 15, 12, 0, tzinfo=dt.UTC)
ROOM_URL = "https://dg-chriscat.daily.co/caller-abc"
NO_HISTORY_ID = "EVM199901"


@pytest.fixture
async def schema():
    name = f"test_{uuid.uuid4().hex[:12]}"
    async with await connect() as admin:
        await admin.execute(f'CREATE SCHEMA "{name}"')
    try:
        async with await connect(schema=name) as c:
            await migrate.apply_migrations(c)
            await seed.load_seed(c, as_of=AS_OF)
            # A brand new member with no calls, claims, or referrals (mock data, test only).
            await c.execute(
                "INSERT INTO members (member_id, first_name, last_name, dob, phone, plan_id, "
                "coverage_start) VALUES (%s, 'Nora', 'Quinn', '1995-08-01', '+15555550199', "
                "'PLN-PPO-GOLD', %s)",
                (NO_HISTORY_ID, AS_OF),
            )
        yield name
    finally:
        async with await connect() as admin:
            await admin.execute(f'DROP SCHEMA "{name}" CASCADE')


@pytest.fixture
def repo(schema):
    return CallerHistory(schema=schema, clock=lambda: NOW)


class Clock:
    def __init__(self):
        self._now = 1000.0

    def now(self):
        return self._now

    def call_later(self, delay, fn):
        class H:
            def cancel(self):
                pass

        return H()


@pytest.fixture
def app(repo):
    app = create_app(env={})
    app.state.caller_history = repo

    async def mint(room_name, user_name):
        return "tok", 1

    app.state.queue_service = QueueService(mint_token=mint, clock=Clock())
    return app


async def accepted(app, intake, info, session_id="s1") -> tuple[TestClient, str]:
    svc = queue_api.get_queue(app)
    svc.register_session(session_id, ROOM_URL)
    agent = svc.sign_in(f"Agent {session_id}")
    svc.set_status(agent.agent_id, "available")
    svc._clock._now = 1000.0
    svc.enqueue_call(session_id, ROOM_URL, intake, info)
    svc._clock._now = 1095.0
    await svc.accept(agent.agent_id, session_id)
    return TestClient(app, base_url="http://localhost"), agent.agent_id


def verified(member_id):
    return IntakeInfo(outcome="completed", identity_result="verified", member_id=member_id)


async def member_of(schema, kind, ref) -> str:
    table, key = {
        "call": ("interactions", "interaction_id"),
        "claim": ("claims", "claim_id"),
        "referral": ("referrals", "referral_id"),
    }[kind]
    async with await connect(schema=schema) as c:
        cur = await c.execute(f"SELECT member_id FROM {table} WHERE {key} = %s", (ref,))
        return (await cur.fetchone())[0]


# ---- repo ---------------------------------------------------------------------------


async def test_maria_repeat_caller_last_three_events(repo):
    h = await repo.lookup("EVM100231")
    assert h.member == {
        "member_id": "EVM100231",
        "first_name": "Maria",
        "last_name": "Alvarez",
        "name": "Maria Alvarez",
        "dob": "1984-03-12",
        "plan_id": "PLN-HMO-SILVER",
        "plan_name": "Evergreen HMO Silver",
        "plan_type": "HMO",
    }
    assert h.calls_30d == 2
    assert [(e["kind"], e["ref"], e["occurred_on"]) for e in h.recent_events] == [
        ("call", "INT-000001", "2026-11-09"),
        ("call", "INT-000002", "2026-10-31"),
        ("claim", "CLM-900001", "2026-10-30"),
    ]
    denied = h.recent_events[2]
    assert denied["status"] == "denied"
    assert denied["summary"] == "MRI, knee, left claim denied, CO-197"
    assert h.recent_events[0]["summary"] == "called claims, 9 min, escalated to claims review"


async def test_james_chen_not_repeat_and_no_twin_leak(repo, schema):
    h = await repo.lookup("EVM100232")
    assert h.member["dob"] == "1979-07-04"
    assert h.member["plan_name"] == "Summit PPO Gold"
    assert h.calls_30d == 0
    for e in h.recent_events:
        assert await member_of(schema, e["kind"], e["ref"]) == "EVM100232"
    assert "INT-000010" not in {e["ref"] for e in h.recent_events}  # the other James Chen


async def test_referral_events_are_included(repo):
    h = await repo.lookup("EVM100306")  # pending cardiology referral 5 days ago
    first = h.recent_events[0]
    assert (first["kind"], first["ref"], first["status"]) == ("referral", "REF-700005", "pending")
    assert first["summary"] == "referral to Northgate Cardiology, pending"


async def test_member_with_no_history(repo):
    h = await repo.lookup(NO_HISTORY_ID)
    assert h.member["name"] == "Nora Quinn"
    assert h.calls_30d == 0
    assert h.recent_events == []


async def test_unknown_member_is_none(repo):
    assert await repo.lookup("EVM000000") is None


async def test_events_never_belong_to_another_member(repo, schema):
    for persona in seed.DEMO_PERSONAS:
        h = await repo.lookup(persona.member_id)
        assert h.member["member_id"] == persona.member_id
        assert len(h.recent_events) <= 3
        for e in h.recent_events:
            assert await member_of(schema, e["kind"], e["ref"]) == persona.member_id


# ---- endpoint, seeded personas ---------------------------------------------------------


async def test_endpoint_verified_repeat_caller_maria(app):
    intake = {"full_name": "Maria Alvarez", "member_id": "EVM100231", "reason": "MRI denied"}
    client, agent_id = await accepted(app, intake, verified("EVM100231"))
    body = client.get("/calls/s1/caller", params={"agent_id": agent_id}).json()
    assert body["not_verified"] is False
    assert body["member"]["name"] == "Maria Alvarez"
    assert body["calls_30d"] == 2
    assert body["repeat_caller"] is True
    assert body["wait_secs"] == 95.0
    assert [e["ref"] for e in body["recent_events"]] == ["INT-000001", "INT-000002", "CLM-900001"]


async def test_endpoint_aisha_repeat_caller(app):
    intake = {"full_name": "Aisha Patel", "member_id": "EVM100233", "reason": "Claim pending"}
    client, agent_id = await accepted(app, intake, verified("EVM100233"))
    body = client.get("/calls/s1/caller", params={"agent_id": agent_id}).json()
    assert body["repeat_caller"] is True
    assert body["calls_30d"] == 2


async def test_endpoint_not_verified_daniel_returns_intake_only(app):
    """Daniel gives a wrong DOB: not_verified. His claimed id must not be looked up."""
    intake = {
        "full_name": "Daniel Okafor",
        "dob": "1990-06-17",
        "member_id": "EVM100235",
        "reason": "Knee surgery prior auth status",
    }
    info = IntakeInfo(
        outcome="completed",
        identity_result="not_verified",
        member_id="EVM100235",
        flags=("identity_not_verified",),
    )
    client, agent_id = await accepted(app, intake, info)
    r = client.get("/calls/s1/caller", params={"agent_id": agent_id})
    body = r.json()
    assert body["not_verified"] is True
    assert body["member"] is None
    assert body["recent_events"] == []
    assert body["intake"] == intake
    assert "PA-500101" not in r.text
    assert "Harbor EPO" not in r.text


async def test_endpoint_no_history_member(app):
    intake = {"full_name": "Nora Quinn", "member_id": NO_HISTORY_ID, "reason": "New member"}
    client, agent_id = await accepted(app, intake, verified(NO_HISTORY_ID))
    body = client.get("/calls/s1/caller", params={"agent_id": agent_id}).json()
    assert body["member"]["name"] == "Nora Quinn"
    assert body["calls_30d"] == 0
    assert body["repeat_caller"] is False
    assert body["recent_events"] == []


async def test_endpoint_two_calls_do_not_cross(app):
    """Two accepted calls: each agent sees only their own caller."""
    maria = {"full_name": "Maria Alvarez", "member_id": "EVM100231", "reason": "MRI denied"}
    linda = {"full_name": "Linda Nguyen", "member_id": "EVM100234", "reason": "EOB question"}
    client, maria_agent = await accepted(app, maria, verified("EVM100231"), "s1")
    queue_api.get_queue(app).register_session("s2", "https://dg-chriscat.daily.co/caller-def")
    _, linda_agent = await accepted(app, linda, verified("EVM100234"), "s2")

    m = client.get("/calls/s1/caller", params={"agent_id": maria_agent})
    n = client.get("/calls/s2/caller", params={"agent_id": linda_agent})
    assert m.json()["member"]["member_id"] == "EVM100231"
    assert n.json()["member"]["member_id"] == "EVM100234"
    assert "EVM100234" not in m.text and "Linda" not in m.text
    assert "EVM100231" not in n.text and "Maria" not in n.text
    cross = client.get("/calls/s2/caller", params={"agent_id": maria_agent})
    assert cross.status_code == 403
