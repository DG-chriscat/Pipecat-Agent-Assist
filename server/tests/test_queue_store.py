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
