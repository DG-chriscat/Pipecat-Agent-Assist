"""Queue write-through to Postgres (docs/queue.md). Skipped without DATABASE_URL.

Each test uses a throwaway schema; public and the demo data are never touched.
"""

import os
import uuid

import pytest
from agent_assist_server.db import connect, migrate
from agent_assist_server.queue_service import AgentState, QueueService
from agent_assist_server.queue_store import QueueStore

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")


@pytest.fixture
async def schema():
    name = f"test_{uuid.uuid4().hex[:12]}"
    async with await connect() as admin:
        await admin.execute(f'CREATE SCHEMA "{name}"')
    try:
        async with await connect(schema=name) as c:
            await migrate.apply_migrations(c)
        yield name
    finally:
        async with await connect() as admin:
            await admin.execute(f'DROP SCHEMA "{name}" CASCADE')


async def mint(room_name, user_name):
    return f"tok-{room_name}", 1


async def rows(schema, query, *args):
    async with await connect(schema=schema) as c:
        cur = await c.execute(query, args or None)
        return await cur.fetchall()


async def test_agents_table_has_rls(schema):
    got = await rows(
        schema,
        "SELECT c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relname = 'agents'",
        schema,
    )
    assert got == [(True,)]


async def test_write_through_calls_and_agents(schema):
    store = QueueStore(schema=schema)
    svc = QueueService(mint_token=mint)
    svc.subscribe(store.on_event)

    agent = svc.sign_in("Ana")
    svc.set_status(agent.agent_id, AgentState.AVAILABLE)
    svc.enqueue_call("sess-1", "https://x.daily.co/caller-1", {"reason": "claim denied"})
    await svc.accept(agent.agent_id, "sess-1")
    await store.drain()

    calls = await rows(
        schema,
        "SELECT session_id, status, intake->>'reason', agent_id, queued_at IS NOT NULL, "
        "answered_at IS NOT NULL FROM calls",
    )
    assert calls == [("sess-1", "with_agent", "claim denied", agent.agent_id, True, True)]
    agents = await rows(schema, "SELECT agent_id, name, state FROM agents")
    assert agents == [(agent.agent_id, "Ana", "on_call")]

    svc.end_call("sess-1")
    await store.drain()
    assert await rows(schema, "SELECT status, ended_at IS NOT NULL FROM calls") == [("ended", True)]
    assert await rows(schema, "SELECT state FROM agents") == [("wrap_up",)]


async def test_write_through_keeps_intake_written_by_the_bot(schema):
    """P2-3's bot may create the calls row first; enqueue must not wipe its fields."""
    async with await connect(schema=schema) as c:
        await c.execute(
            "INSERT INTO calls (session_id, status, intake, identity_result) "
            "VALUES ('sess-2', 'ready_for_queue', '{\"dob\": \"1984-03-12\"}', 'verified')"
        )
    store = QueueStore(schema=schema)
    svc = QueueService(mint_token=mint)
    svc.subscribe(store.on_event)
    svc.enqueue_call("sess-2", "https://x.daily.co/caller-2", {"reason": "billing"})
    await store.drain()
    got = await rows(schema, "SELECT status, intake, identity_result FROM calls")
    assert got == [("queued", {"dob": "1984-03-12", "reason": "billing"}, "verified")]


async def test_write_through_merges_intake_flags_and_identity(schema):
    """Flags and identity from the bot handoff reach calls; the bot's own flags stay."""
    from agent_assist_server.db import seed
    from agent_assist_server.queue_service import IntakeInfo

    async with await connect(schema=schema) as c:
        await seed.load_seed(c)
        member = (await (await c.execute("SELECT member_id FROM members LIMIT 1")).fetchone())[0]
        await c.execute(
            "INSERT INTO calls (session_id, status, flags) "
            "VALUES ('sess-4', 'ready_for_queue', ARRAY['asked_for_person'])"
        )
    store = QueueStore(schema=schema)
    svc = QueueService(mint_token=mint)
    svc.subscribe(store.on_event)
    info = IntakeInfo(
        outcome="emergency",
        identity_result="not_verified",
        member_id=member,
        flags=("asked_for_person", "emergency", "identity_not_verified"),
    )
    svc.enqueue_call("sess-4", "https://x.daily.co/caller-4", {"reason": "x"}, info)
    # A fresh call (no bot row) with an unknown member id: the FK must not fail the write.
    svc.enqueue_call(
        "sess-5",
        "https://x.daily.co/caller-5",
        {},
        IntakeInfo(identity_result="not_found", member_id="NOPE000", flags=("emergency",)),
    )
    await store.drain()
    got = await rows(
        schema, "SELECT session_id, identity_result, member_id, flags FROM calls ORDER BY 1"
    )
    assert got == [
        (
            "sess-4",
            "not_verified",
            member,
            ["asked_for_person", "emergency", "identity_not_verified"],
        ),
        ("sess-5", "not_found", None, ["emergency"]),
    ]


async def test_seed_clears_agents(schema):
    from agent_assist_server.db import seed

    async with await connect(schema=schema) as c:
        await c.execute("INSERT INTO agents (agent_id, name) VALUES ('agent-1', 'Ana')")
        counts = await seed.load_seed(c)
    assert counts["agents"] == 0


async def test_db_errors_do_not_break_the_queue(schema):
    store = QueueStore(schema=schema, url="postgresql://nobody@127.0.0.1:1/none")
    svc = QueueService(mint_token=mint)
    svc.subscribe(store.on_event)
    svc.enqueue_call("sess-3", "https://x.daily.co/caller-3", {})
    await store.drain()
    assert [c.session_id for c in svc.waiting_calls()] == ["sess-3"]


async def test_write_through_saves_wrap_up_on_the_call(schema):
    """P3-5: ended_by, reason code, notes, and save time land on the calls row."""
    store = QueueStore(schema=schema)
    svc = QueueService(mint_token=mint)
    svc.subscribe(store.on_event)
    agent = svc.sign_in("Ana")
    svc.set_status(agent.agent_id, AgentState.AVAILABLE)
    svc.enqueue_call("sess-6", "https://x.daily.co/caller-6", {"reason": "claim denied"})
    await svc.accept(agent.agent_id, "sess-6")
    svc.end_call("sess-6")
    svc.save_wrap_up(agent.agent_id, "sess-6", "denial_explained", "Explained PA-01.")
    await store.drain()
    got = await rows(
        schema,
        "SELECT status, ended_by, wrap_up_reason, wrap_up_notes, wrapped_up_at IS NOT NULL "
        "FROM calls",
    )
    assert got == [("ended", "caller", "denial_explained", "Explained PA-01.", True)]
    assert await rows(schema, "SELECT state, current_call FROM agents") == [("available", None)]


async def test_write_through_requeued_call_clears_the_agent(schema):
    """QH-9: an agent drop puts the row back to queued with no agent or answer time."""
    store = QueueStore(schema=schema)
    svc = QueueService(mint_token=mint)
    svc.subscribe(store.on_event)
    agent = svc.sign_in("Ana")
    svc.set_status(agent.agent_id, AgentState.AVAILABLE)
    svc.enqueue_call("sess-7", "https://x.daily.co/caller-7", {})
    await svc.accept(agent.agent_id, "sess-7")
    svc.agent_left("sess-7")
    await store.drain()
    got = await rows(schema, "SELECT status, agent_id, answered_at FROM calls")
    assert got == [("queued", None, None)]
    assert await rows(schema, "SELECT state FROM agents") == [("offline",)]


async def test_write_through_saves_phi_reveals_on_the_call(schema):
    """AP-17: each reveal lands on calls.phi_reveals (field, agent, time; never the value)."""
    store = QueueStore(schema=schema)
    svc = QueueService(mint_token=mint)
    svc.subscribe(store.on_event)
    agent = svc.sign_in("Ana")
    svc.set_status(agent.agent_id, AgentState.AVAILABLE)
    svc.enqueue_call("sess-8", "https://x.daily.co/caller-8", {"dob": "1984-03-12"})
    await svc.accept(agent.agent_id, "sess-8")
    svc.log_phi_reveal(agent.agent_id, "sess-8", "member_id")
    svc.log_phi_reveal(agent.agent_id, "sess-8", "dob")
    await store.drain()
    (got,) = await rows(schema, "SELECT phi_reveals FROM calls")
    assert [(r["field"], r["agent_id"]) for r in got[0]] == [
        ("member_id", agent.agent_id),
        ("dob", agent.agent_id),
    ]
    assert all(isinstance(r["at"], float) for r in got[0])
