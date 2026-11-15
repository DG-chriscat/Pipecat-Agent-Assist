"""Migrations + seed against a real Postgres (DATABASE_URL). Skipped when unset.

Each test runs in its own throwaway schema (search_path) and drops it after,
so the public schema and the demo data are never touched.
"""

import datetime as dt
import os
import subprocess
import sys
import uuid

import pytest
from agent_assist_server.db import connect, migrate, seed

ALL_MIGRATIONS = [p.stem for p in migrate.migration_files()]

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")

AS_OF = dt.date(2026, 11, 15)
TABLES = [
    "plans",
    "members",
    "claims",
    "payments",
    "referrals",
    "prior_authorizations",
    "interactions",
    "kb_articles",
    "calls",
    "transcript_lines",
]


@pytest.fixture
async def schema():
    name = f"test_{uuid.uuid4().hex[:12]}"
    async with await connect() as admin:
        await admin.execute(f'CREATE SCHEMA "{name}"')
    try:
        yield name
    finally:
        async with await connect() as admin:
            await admin.execute(f'DROP SCHEMA "{name}" CASCADE')


@pytest.fixture
async def conn(schema):
    async with await connect(schema=schema) as c:
        yield c


async def count(conn, table):
    cur = await conn.execute(f"SELECT count(*) FROM {table}")
    return (await cur.fetchone())[0]


async def test_connect_sets_search_path_to_schema_only(conn, schema):
    cur = await conn.execute("SHOW search_path")
    assert (await cur.fetchone())[0].strip('"') == schema


async def test_migrate_creates_all_tables_in_schema_not_public(conn, schema):
    applied = await migrate.apply_migrations(conn)
    assert applied == ALL_MIGRATIONS
    cur = await conn.execute(
        "SELECT table_schema, table_name FROM information_schema.tables WHERE table_name = ANY(%s)",
        (TABLES + ["schema_migrations"],),
    )
    rows = await cur.fetchall()
    in_schema = {t for s, t in rows if s == schema}
    assert in_schema == set(TABLES) | {"schema_migrations"}


async def test_migrate_is_idempotent(conn):
    assert await migrate.apply_migrations(conn) == ALL_MIGRATIONS
    assert await migrate.apply_migrations(conn) == []


async def test_seed_loads_expected_counts_and_is_idempotent(conn):
    await migrate.apply_migrations(conn)
    data = seed.build_seed(AS_OF)
    first = await seed.load_seed(conn, as_of=AS_OF)
    second = await seed.load_seed(conn, as_of=AS_OF)
    assert first == second
    assert first["members"] == len(data.members) == await count(conn, "members")
    assert first["claims"] == len(data.claims)
    assert first["kb_articles"] == len(data.kb_articles)
    assert first["interactions"] == len(data.interactions)
    assert first["calls"] == 0
    assert first["transcript_lines"] == 0


async def test_seed_truncates_call_records(conn):
    await migrate.apply_migrations(conn)
    await seed.load_seed(conn, as_of=AS_OF)
    await conn.execute("INSERT INTO calls (session_id) VALUES ('s-1')")
    counts = await seed.load_seed(conn, as_of=AS_OF)
    assert counts["calls"] == 0


async def test_identity_lookup_by_name_dob_member_id(conn):
    await migrate.apply_migrations(conn)
    await seed.load_seed(conn, as_of=AS_OF)
    p = seed.DEMO_PERSONAS[0]
    cur = await conn.execute(
        "SELECT m.member_id, p.name FROM members m JOIN plans p USING (plan_id) "
        "WHERE lower(first_name) = lower(%s) AND lower(last_name) = lower(%s) "
        "AND dob = %s AND member_id = %s",
        (p.first_name.upper(), p.last_name.lower(), p.dob, p.member_id),
    )
    rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == p.member_id

    cur = await conn.execute(
        "SELECT count(*) FROM members WHERE lower(first_name) = lower(%s) "
        "AND lower(last_name) = lower(%s) AND dob = %s",
        (p.first_name, p.last_name, p.dob + dt.timedelta(days=1)),
    )
    assert (await cur.fetchone())[0] == 0


async def test_denied_claim_persona_has_denial_code(conn):
    await migrate.apply_migrations(conn)
    await seed.load_seed(conn, as_of=AS_OF)
    p = seed.DEMO_PERSONAS[0]
    cur = await conn.execute(
        "SELECT denial_code FROM claims WHERE member_id = %s AND status = 'denied'",
        (p.member_id,),
    )
    codes = [r[0] for r in await cur.fetchall()]
    assert codes and all(codes)


async def test_repeat_caller_visible_via_interactions(conn):
    await migrate.apply_migrations(conn)
    await seed.load_seed(conn, as_of=AS_OF)
    cur = await conn.execute(
        "SELECT member_id FROM interactions WHERE started_at >= %s::date - 30 "
        "GROUP BY member_id HAVING count(*) >= 2",
        (AS_OF,),
    )
    assert len(await cur.fetchall()) >= 2


async def test_call_and_transcript_records(conn):
    await migrate.apply_migrations(conn)
    await seed.load_seed(conn, as_of=AS_OF)
    p = seed.DEMO_PERSONAS[0]
    cur = await conn.execute(
        "INSERT INTO calls (session_id, member_id, intake, identity_result, flags) "
        "VALUES (%s, %s, %s::jsonb, 'verified', ARRAY['repeat_caller']) "
        "RETURNING call_id, status, created_at",
        ("sess-abc", p.member_id, '{"reason": "claim status"}'),
    )
    call_id, status, created_at = await cur.fetchone()
    assert status == "intake"
    assert created_at is not None
    await conn.execute(
        "INSERT INTO transcript_lines (call_id, speaker_role, text, tone) "
        "VALUES (%s, 'caller', 'Where is my claim?', 'frustrated')",
        (call_id,),
    )
    cur = await conn.execute(
        "SELECT c.intake->>'reason', t.speaker_role, t.ts IS NOT NULL "
        "FROM calls c JOIN transcript_lines t USING (call_id)"
    )
    assert await cur.fetchone() == ("claim status", "caller", True)


async def test_cli_migrate_then_seed(schema):
    env = dict(os.environ)
    base = [sys.executable, "-m", "agent_assist_server.db"]
    r1 = subprocess.run(
        base + ["migrate", "--schema", schema], env=env, capture_output=True, text=True
    )
    assert r1.returncode == 0, r1.stderr
    assert "0001_init" in r1.stdout
    r2 = subprocess.run(
        base + ["seed", "--schema", schema, "--as-of", AS_OF.isoformat()],
        env=env,
        capture_output=True,
        text=True,
    )
    assert r2.returncode == 0, r2.stderr
    assert "members" in r2.stdout
    assert os.environ["DATABASE_URL"] not in r1.stdout + r1.stderr + r2.stdout + r2.stderr


async def test_all_tables_have_row_level_security(conn, schema):
    await migrate.apply_migrations(conn)
    cur = await conn.execute(
        "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relkind = 'r' AND NOT c.relrowsecurity",
        (schema,),
    )
    assert await cur.fetchall() == []
