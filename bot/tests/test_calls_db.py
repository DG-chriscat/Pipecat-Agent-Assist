"""PostgresCallStore against a real Postgres (DATABASE_URL); skipped when unset.

Each test migrates and seeds a throwaway `test_<hex>` schema and drops it after,
so the public schema and the demo data are never touched.
"""

import datetime as dt
import os
import uuid

import pytest
from agent_assist_bot.calls import PostgresCallStore

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")

AS_OF = dt.date(2026, 11, 15)


@pytest.fixture
async def schema():
    from agent_assist_server.db import connect, migrate, seed

    name = f"test_{uuid.uuid4().hex[:12]}"
    async with await connect() as admin:
        await admin.execute(f'CREATE SCHEMA "{name}"')
    try:
        async with await connect(schema=name) as c:
            await migrate.apply_migrations(c)
            await seed.load_seed(c, as_of=AS_OF)
        yield name
    finally:
        async with await connect() as admin:
            await admin.execute(f'DROP SCHEMA "{name}" CASCADE')


@pytest.fixture
async def store(schema):
    s = PostgresCallStore(os.environ["DATABASE_URL"], schema=schema)
    yield s
    await s.close()


async def row(store, call_id):
    cur = await store._execute(
        "SELECT status, member_id, intake, identity_result, flags FROM calls WHERE call_id = %s",
        (call_id,),
    )
    return await cur.fetchone()


@pytest.mark.parametrize(
    "name, dob, member_id, want",
    [
        ("Maria Alvarez", "1984-03-12", "EVM100231", ("verified", "EVM100231")),
        ("maria alvarez", "1984-03-12", "evm 100 231", ("verified", "EVM100231")),
        # Daniel Okafor's scripted wrong DOB (docs/db.md).
        ("Daniel Okafor", "1990-07-15", "EVM100235", ("not_verified", "EVM100235")),
        # Second James Chen, wrong ID, right name + DOB: one member matches.
        ("James Chen", "1986-02-09", "EVM000000", ("not_verified", "EVM100236")),
        # Name shared by two members, DOB matches neither.
        ("James Chen", "1990-01-01", "EVM000000", ("not_found", None)),
        ("Nobody Real", "2000-01-01", "ZZZ999999", ("not_found", None)),
    ],
)
async def test_verify_identity_against_seed(store, name, dob, member_id, want):
    check = await store.verify_identity(name, dob, member_id)
    assert (check.result, check.member_id) == want


async def test_create_call_inserts_intake_row_and_is_idempotent_per_session(store):
    call_id = await store.create_call("sess-1")
    assert await store.create_call("sess-1") == call_id
    status, member_id, intake, identity, flags = await row(store, call_id)
    assert (status, member_id, intake, identity, flags) == ("intake", None, {}, None, [])


async def test_save_intake_merges_fields_as_captured(store):
    call_id = await store.create_call("sess-2")
    await store.save_intake(call_id, {"full_name": "Maria Alvarez"})
    assert (await row(store, call_id))[2] == {"full_name": "Maria Alvarez"}
    await store.save_intake(call_id, {"reason": "denied MRI claim"})
    await store.save_intake(call_id, {"reason": "denied MRI claim, CO-197"})
    assert (await row(store, call_id))[2] == {
        "full_name": "Maria Alvarez",
        "reason": "denied MRI claim, CO-197",
    }


async def test_set_identity_and_flags(store):
    call_id = await store.create_call("sess-3")
    await store.set_identity(call_id, "not_verified", "EVM100235")
    await store.add_flag(call_id, "identity_not_verified")
    await store.add_flag(call_id, "identity_not_verified")
    await store.add_flag(call_id, "emergency")
    _, member_id, _, identity, flags = await row(store, call_id)
    assert (identity, member_id) == ("not_verified", "EVM100235")
    assert flags == ["identity_not_verified", "emergency"]


async def test_mark_ready_for_queue_stores_summary(store):
    call_id = await store.create_call("sess-4")
    await store.save_intake(call_id, {"reason": "claim denied"})
    await store.mark_ready_for_queue(call_id, "Maria Alvarez, MRI claim denied.")
    status, _, intake, _, _ = await row(store, call_id)
    assert status == "ready_for_queue"
    assert intake == {"reason": "claim denied", "summary": "Maria Alvarez, MRI claim denied."}


async def test_reconnects_after_dropped_connection(store):
    call_id = await store.create_call("sess-5")
    await store._conn.close()
    await store.save_intake(call_id, {"reason": "x"})
    assert (await row(store, call_id))[2] == {"reason": "x"}
