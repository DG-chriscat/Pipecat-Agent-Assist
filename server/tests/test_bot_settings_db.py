"""PostgresSettingsStore against a real Postgres (DATABASE_URL), in a throwaway schema
per test. Skipped when DATABASE_URL is unset."""

from __future__ import annotations

import os
import uuid

import pytest
from agent_assist_server import bot_settings as bs
from agent_assist_server.db import connect, migrate

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")


def good(**over) -> dict:
    return {**bs.defaults({}), **over}


@pytest.fixture
async def schema():
    name = f"test_{uuid.uuid4().hex[:12]}"
    async with await connect() as admin:
        await admin.execute(f'CREATE SCHEMA "{name}"')
    try:
        async with await connect(schema=name) as conn:
            await migrate.apply_migrations(conn)
        yield name
    finally:
        async with await connect() as admin:
            await admin.execute(f'DROP SCHEMA "{name}" CASCADE')


def store(schema):
    return bs.PostgresSettingsStore(schema=schema, defaults=lambda: good())


async def test_no_row_means_defaults(schema):
    s = await store(schema).get()
    assert s.values == good() and s.is_default


async def test_save_persists_and_logs_the_change(schema):
    saved = await store(schema).save(
        good(tts_model="aura-2", tts_voice="aura-2-thalia-en", hold_update_secs=30),
        session_id="sess-a",
        agent="Ana",
    )
    assert saved.values["tts_voice"] == "aura-2-thalia-en" and saved.updated_by == "sess-a"
    # A fresh store (another process, or after a restart) reads the same row.
    again = await store(schema).get()
    assert again.values == saved.values and not again.is_default
    (event,) = await store(schema).events()
    assert event.kind == "save" and event.session_id == "sess-a" and event.agent == "Ana"
    assert event.changes == {
        "tts_model": {"from": "flux", "to": "aura-2"},
        "tts_voice": {"from": "flux-alexis-en", "to": "aura-2-thalia-en"},
        "hold_update_secs": {"from": 60, "to": 30},
    }


async def test_second_save_updates_the_one_row(schema):
    s = store(schema)
    await s.save(good(hold_update_secs=30), session_id="a", agent=None)
    await s.save(good(hold_update_secs=45), session_id="b", agent=None)
    async with await connect(schema=schema) as conn:
        cur = await conn.execute("SELECT count(*), max(hold_update_secs) FROM bot_settings")
        assert await cur.fetchone() == (1, 45)
    events = await s.events()
    assert [e.session_id for e in events] == ["b", "a"]
    assert events[0].changes == {"hold_update_secs": {"from": 30, "to": 45}}


async def test_reset_deletes_the_row_and_logs(schema):
    s = store(schema)
    await s.save(good(identity_read_back=False), session_id="a", agent=None)
    reset = await s.reset(session_id="b", agent="Ben")
    assert reset.values == good() and reset.is_default
    assert (await store(schema).get()).is_default
    latest = (await s.events())[0]
    assert latest.kind == "reset" and latest.agent == "Ben"
    assert latest.changes == {"identity_read_back": {"from": False, "to": True}}


async def test_tables_have_row_level_security(schema):
    async with await connect(schema=schema) as conn:
        cur = await conn.execute(
            "SELECT relname, relrowsecurity FROM pg_class c JOIN pg_namespace n "
            "ON n.oid = c.relnamespace WHERE nspname = %s AND relname = ANY(%s)",
            (schema, ["bot_settings", "settings_events"]),
        )
        assert dict(await cur.fetchall()) == {"bot_settings": True, "settings_events": True}


async def test_the_row_rejects_a_bad_model(schema):
    import psycopg

    async with await connect(schema=schema) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute(bs.UPSERT_SQL, {**good(tts_model="aura-1"), "session_id": "x"})


async def test_stt_models_persist(schema):
    s = store(schema)
    values = good(stt_intake_model="nova-3-general", stt_assist_model="nova-3-medical")
    await s.save(values, session_id="sess", agent="Ana")
    fresh = await store(schema).get()
    assert fresh.values["stt_intake_model"] == "nova-3-general"
    assert fresh.values["stt_assist_model"] == "nova-3-medical"


@pytest.mark.parametrize(
    "field, value", [("stt_intake_model", "nova-3-medical"), ("stt_assist_model", "nova-2")]
)
async def test_the_row_rejects_a_bad_stt_model(schema, field, value):
    import psycopg

    async with await connect(schema=schema) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute(bs.UPSERT_SQL, {**good(**{field: value}), "session_id": "x"})


async def test_flux_controls_persist(schema):
    s = store(schema)
    values = good(tts_speed=1.1, tts_expressivity=-1, slow_readback=False, readback_speed=0.85)
    await s.save(values, session_id="sess", agent="Ana")
    fresh = (await store(schema).get()).values
    assert fresh["tts_speed"] == pytest.approx(1.1)
    assert fresh["tts_expressivity"] == -1
    assert fresh["slow_readback"] is False
    assert fresh["readback_speed"] == pytest.approx(0.85)


@pytest.mark.parametrize(
    "field, value",
    [("tts_speed", 1.6), ("tts_expressivity", 3), ("readback_speed", 1.1)],
)
async def test_the_row_rejects_bad_flux_controls(schema, field, value):
    import psycopg

    async with await connect(schema=schema) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute(bs.UPSERT_SQL, {**good(**{field: value}), "session_id": "x"})


async def test_llm_per_job_persists(schema):
    s = store(schema)
    values = good(
        llm_intake="phonellm-mini-alpha-2-fp8", llm_assist="think-fast", llm_wrapup="think-fast"
    )
    await s.save(values, session_id="sess", agent="Ana")
    fresh = (await store(schema).get()).values
    assert (fresh["llm_intake"], fresh["llm_assist"], fresh["llm_wrapup"]) == (
        "phonellm-mini-alpha-2-fp8",
        "think-fast",
        "think-fast",
    )


@pytest.mark.parametrize("field", ["llm_intake", "llm_assist", "llm_wrapup"])
async def test_the_row_rejects_a_bad_llm(schema, field):
    import psycopg

    async with await connect(schema=schema) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute(bs.UPSERT_SQL, {**good(**{field: "gpt-4o"}), "session_id": "x"})
