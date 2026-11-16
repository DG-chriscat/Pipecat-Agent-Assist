"""GET /calls/{session_id}/caller (P3-3, AP-5, AP-13): access rules and the not verified
path. No database here: a fake history repo stands in. Seeded persona tests against
Postgres live in test_caller_history_db.py."""

from __future__ import annotations

import pytest
from agent_assist_server import auth, caller_api, queue_api
from agent_assist_server.app import create_app
from agent_assist_server.caller_api import MemberHistory
from agent_assist_server.queue_service import IntakeInfo
from fastapi.testclient import TestClient

ROOM_URL = "https://dg-chriscat.daily.co/caller-abc"

MARIA_INTAKE = {
    "full_name": "Maria Lopez",
    "dob": "1984-03-12",
    "member_id": "418302",
    "reason": "MRI claim denied",
    "claim_number": "70412",
    "summary": "Maria's MRI claim was denied; she wants to know why.",
}
DANIEL_INTAKE = {
    "full_name": "Ben Cole",
    "dob": "1990-06-17",
    "member_id": "852617",
    "reason": "Knee surgery prior auth status",
    "summary": "Asks about knee surgery authorization.",
}


class FakeHistory:
    """Stands in for CallerHistory; records lookups so tests can prove none happened."""

    def __init__(self, result: MemberHistory | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.lookups: list[str] = []

    async def lookup(self, member_id: str) -> MemberHistory | None:
        self.lookups.append(member_id)
        if self.error:
            raise self.error
        return self.result


MARIA_HISTORY = MemberHistory(
    member={
        "member_id": "418302",
        "first_name": "Maria",
        "last_name": "Lopez",
        "name": "Maria Lopez",
        "dob": "1984-03-12",
        "plan_id": "PLN-HMO-SILVER",
        "plan_name": "Evergreen HMO Silver",
        "plan_type": "HMO",
    },
    calls_30d=2,
    recent_events=[],
)


class Clock:
    def __init__(self, now: float = 1000.0):
        self._now = now

    def now(self) -> float:
        return self._now

    def call_later(self, delay, fn):
        class H:
            def cancel(self):
                pass

        return H()


def make_client(app, history: FakeHistory):
    app.state.caller_history = history

    async def mint(room_name, user_name):
        return f"tok-{user_name}", 1

    from agent_assist_server.queue_service import QueueService

    app.state.queue_service = QueueService(mint_token=mint, clock=Clock())
    return TestClient(app, base_url="http://localhost")


@pytest.fixture
def app():
    return create_app(env={})


async def put_call_with_agent(
    app, intake: dict, info: IntakeInfo, session_id: str = "s1", agent_name: str = "Ana"
) -> str:
    """Queue a call, have agent_name accept it, return the agent id."""
    svc = queue_api.get_queue(app)
    svc.register_session(session_id, ROOM_URL)
    agent = svc.sign_in(agent_name)
    svc.set_status(agent.agent_id, "available")
    svc._clock._now = 1000.0
    svc.enqueue_call(session_id, ROOM_URL, intake, info)
    svc._clock._now = 1102.0
    await svc.accept(agent.agent_id, session_id)
    return agent.agent_id


VERIFIED = IntakeInfo(outcome="completed", identity_result="verified", member_id="418302")


async def test_verified_caller_gets_member_history_intake_and_wait(app):
    history = FakeHistory(MARIA_HISTORY)
    client = make_client(app, history)
    agent_id = await put_call_with_agent(app, MARIA_INTAKE, VERIFIED)

    r = client.get("/calls/s1/caller", params={"agent_id": agent_id})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == "s1"
    assert body["identity_result"] == "verified"
    assert body["not_verified"] is False
    assert body["intake"] == MARIA_INTAKE
    assert body["wait_secs"] == 102.0
    assert body["member"]["member_id"] == "418302"
    assert body["member"]["plan_name"] == "Evergreen HMO Silver"
    assert body["calls_30d"] == 2
    assert body["repeat_caller"] is True
    assert body["recent_events"] == []
    assert history.lookups == ["418302"]


async def test_one_call_in_30_days_is_not_a_repeat_caller(app):
    one = MemberHistory(member=MARIA_HISTORY.member, calls_30d=1, recent_events=[])
    client = make_client(app, FakeHistory(one))
    agent_id = await put_call_with_agent(app, MARIA_INTAKE, VERIFIED)
    body = client.get("/calls/s1/caller", params={"agent_id": agent_id}).json()
    assert body["calls_30d"] == 1
    assert body["repeat_caller"] is False


@pytest.mark.parametrize(
    "identity", ["not_verified", "not_found", "close_match", "incomplete", None]
)
async def test_unverified_caller_gets_intake_only_and_no_lookup(app, identity):
    """Never guess a member: the claimed member id is not looked up."""
    history = FakeHistory(MARIA_HISTORY)
    client = make_client(app, history)
    info = IntakeInfo(outcome="completed", identity_result=identity, member_id="852617")
    agent_id = await put_call_with_agent(app, DANIEL_INTAKE, info)

    r = client.get("/calls/s1/caller", params={"agent_id": agent_id})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["not_verified"] is True
    assert body["identity_result"] == identity
    assert body["intake"] == DANIEL_INTAKE
    assert body["wait_secs"] == 102.0
    assert body["member"] is None
    assert body["calls_30d"] is None
    assert body["repeat_caller"] is None
    assert body["recent_events"] == []
    assert history.lookups == []


async def test_agent_verified_close_match_shows_the_member(app):
    # IN-13: Mark verified unlocks the record of the member matched by member ID.
    history = FakeHistory(MARIA_HISTORY)
    client = make_client(app, history)
    info = IntakeInfo(outcome="completed", identity_result="close_match", member_id="418302")
    agent_id = await put_call_with_agent(app, MARIA_INTAKE, info)
    body = client.get("/calls/s1/caller", params={"agent_id": agent_id}).json()
    assert body["not_verified"] is True and history.lookups == []
    assert body["identity_decision"] is None
    queue_api.get_queue(app).set_identity_decision(agent_id, "s1", "verified")
    body = client.get("/calls/s1/caller", params={"agent_id": agent_id}).json()
    assert body["not_verified"] is False
    assert body["member"]["member_id"] == "418302"
    assert body["identity_decision"]["decision"] == "verified"
    assert history.lookups == ["418302"]


async def test_could_not_verify_keeps_the_member_hidden(app):
    history = FakeHistory(MARIA_HISTORY)
    client = make_client(app, history)
    info = IntakeInfo(outcome="completed", identity_result="close_match", member_id="418302")
    agent_id = await put_call_with_agent(app, MARIA_INTAKE, info)
    queue_api.get_queue(app).set_identity_decision(agent_id, "s1", "not_verified")
    body = client.get("/calls/s1/caller", params={"agent_id": agent_id}).json()
    assert body["not_verified"] is True and body["member"] is None
    assert history.lookups == []


async def test_verified_without_member_id_is_treated_as_not_verified(app):
    history = FakeHistory(MARIA_HISTORY)
    client = make_client(app, history)
    info = IntakeInfo(outcome="completed", identity_result="verified", member_id=None)
    agent_id = await put_call_with_agent(app, MARIA_INTAKE, info)
    body = client.get("/calls/s1/caller", params={"agent_id": agent_id}).json()
    assert body["not_verified"] is True
    assert body["member"] is None
    assert history.lookups == []


async def test_verified_member_missing_from_db_is_treated_as_not_verified(app):
    client = make_client(app, FakeHistory(None))
    agent_id = await put_call_with_agent(app, MARIA_INTAKE, VERIFIED)
    body = client.get("/calls/s1/caller", params={"agent_id": agent_id}).json()
    assert body["not_verified"] is True
    assert body["member"] is None
    assert body["intake"] == MARIA_INTAKE


async def test_other_agent_cannot_read_the_caller(app):
    client = make_client(app, FakeHistory(MARIA_HISTORY))
    await put_call_with_agent(app, MARIA_INTAKE, VERIFIED)
    other = queue_api.get_queue(app).sign_in("Ben")

    r = client.get("/calls/s1/caller", params={"agent_id": other.agent_id})

    assert r.status_code == 403
    assert "Maria" not in r.text


async def test_queued_call_is_not_readable_before_accept(app):
    history = FakeHistory(MARIA_HISTORY)
    client = make_client(app, history)
    svc = queue_api.get_queue(app)
    svc.register_session("s1", ROOM_URL)
    agent = svc.sign_in("Ana")
    svc.enqueue_call("s1", ROOM_URL, MARIA_INTAKE, VERIFIED)

    r = client.get("/calls/s1/caller", params={"agent_id": agent.agent_id})

    assert r.status_code == 403
    assert history.lookups == []


async def test_agent_can_still_read_after_the_call_ends_in_wrap_up(app):
    client = make_client(app, FakeHistory(MARIA_HISTORY))
    agent_id = await put_call_with_agent(app, MARIA_INTAKE, VERIFIED)
    queue_api.get_queue(app).end_call("s1")
    r = client.get("/calls/s1/caller", params={"agent_id": agent_id})
    assert r.status_code == 200
    assert r.json()["member"]["member_id"] == "418302"


def test_unknown_call_is_404(app):
    client = make_client(app, FakeHistory(MARIA_HISTORY))
    agent = queue_api.get_queue(app).sign_in("Ana")
    r = client.get("/calls/nope/caller", params={"agent_id": agent.agent_id})
    assert r.status_code == 404


def test_agent_id_is_required(app):
    client = make_client(app, FakeHistory(MARIA_HISTORY))
    assert client.get("/calls/s1/caller").status_code == 422


async def test_history_failure_is_503_without_leaking_details(app):
    history = FakeHistory(error=RuntimeError("postgresql://user:secret@host/db exploded"))
    client = make_client(app, history)
    agent_id = await put_call_with_agent(app, MARIA_INTAKE, VERIFIED)

    r = client.get("/calls/s1/caller", params={"agent_id": agent_id})

    assert r.status_code == 503
    assert "secret" not in r.text
    assert "postgresql" not in r.text


async def test_history_timeout_is_503(app, monkeypatch):
    import asyncio

    class Slow(FakeHistory):
        async def lookup(self, member_id):
            await asyncio.sleep(5)

    monkeypatch.setattr(caller_api, "LOOKUP_TIMEOUT_SECS", 0.05)
    client = make_client(app, Slow())
    agent_id = await put_call_with_agent(app, MARIA_INTAKE, VERIFIED)
    assert client.get("/calls/s1/caller", params={"agent_id": agent_id}).status_code == 503


def test_default_history_reads_database_schema_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_SCHEMA", "demo_schema")
    app = create_app(env={})
    repo = caller_api.get_history(app)
    assert isinstance(repo, caller_api.CallerHistory)
    assert repo.schema == "demo_schema"


def test_login_required_when_auth_is_on():
    app = create_app(
        env={auth.PASSWORD_ENV: "pw", auth.SECRET_ENV: "secret-0123456789abcdef0123456789"}
    )
    client = TestClient(app, base_url="http://localhost")
    r = client.get("/calls/s1/caller", params={"agent_id": "agent-x"})
    assert r.status_code == 401
