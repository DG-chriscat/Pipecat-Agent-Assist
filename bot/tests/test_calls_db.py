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


async def test_add_transcript_line_saves_final_lines_for_wrap_up(store):
    call_id = await store.create_call("sess-6")
    await store.add_transcript_line(call_id, "caller", "I already called.", 1794790000.25)
    await store.add_transcript_line(call_id, "agent", "Let me check.", 1794790002.5)
    cur = await store._execute(
        "SELECT speaker_role, text, extract(epoch FROM ts)::float FROM transcript_lines "
        "WHERE call_id = %s ORDER BY ts",
        (call_id,),
    )
    assert await cur.fetchall() == [
        ("caller", "I already called.", 1794790000.25),
        ("agent", "Let me check.", 1794790002.5),
    ]


async def test_add_transcript_line_saves_the_caller_tone_reading(store):
    # P4-5, IN-10: one tone reading per caller line, saved with the line.
    call_id = await store.create_call("sess-6t")
    reading = {
        "emotion": "angry",
        "confidence": 0.8968,
        "labels": [["angry", 0.8968], ["disgusted", 0.0508]],
        "sarcasm": 0.0066,
        "step": 4,
        "mood": "Frustrated",
        "score": 1.0,
    }
    await store.add_transcript_line(call_id, "caller", "This is ridiculous.", 1.0, reading)
    await store.add_transcript_line(call_id, "agent", "I hear you.", 2.0)
    cur = await store._execute(
        "SELECT speaker_role, tone, tone_reading FROM transcript_lines "
        "WHERE call_id = %s ORDER BY ts",
        (call_id,),
    )
    assert await cur.fetchall() == [
        ("caller", "angry", reading),
        ("agent", None, None),
    ]


async def test_add_assist_event_logs_feedback_on_the_call(store):
    # DC-9: accepting a next best step is logged on the call record, nothing else.
    call_id = await store.create_call("sess-7")
    await store.add_assist_event(
        call_id,
        "accept",
        "nbs:retro_auth_review:CLM-900001",
        {"action_id": "request_retro_auth_review", "simulated": True},
    )
    await store.add_assist_event(call_id, "card_sent", "card:denied_claim", {"latency_ms": 812.5})
    cur = await store._execute(
        "SELECT kind, card_id, data FROM assist_events WHERE call_id = %s ORDER BY event_id",
        (call_id,),
    )
    assert await cur.fetchall() == [
        (
            "accept",
            "nbs:retro_auth_review:CLM-900001",
            {"action_id": "request_retro_auth_review", "simulated": True},
        ),
        ("card_sent", "card:denied_claim", {"latency_ms": 812.5}),
    ]
    # Source systems are untouched (DC-2, DC-9).
    cur = await store._execute("SELECT status FROM claims WHERE claim_id = 'CLM-900001'")
    assert await cur.fetchone() == ("denied",)


async def test_assist_events_table_has_rls(store, schema):
    cur = await store._execute(
        "SELECT c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relname = 'assist_events'",
        (schema,),
    )
    assert await cur.fetchall() == [(True,)]


async def test_reconnects_after_dropped_connection(store):
    call_id = await store.create_call("sess-5")
    await store._conn.close()
    await store.save_intake(call_id, {"reason": "x"})
    assert (await row(store, call_id))[2] == {"reason": "x"}
