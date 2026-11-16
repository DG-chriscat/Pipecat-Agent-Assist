"""Connectors (P4-1) against a real Postgres with the seed; skipped without DATABASE_URL.

One throwaway `test_<hex>` schema per module (migrated, seeded, dropped after), so
public and the demo data are never touched. The connectors only read, so tests can
share it.
"""

import datetime as dt
import os
import uuid

import pytest
import pytest_asyncio
from agent_assist_bot.connectors import ConnectorSession, PostgresReader

pytestmark = [
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
    pytest.mark.asyncio(loop_scope="module"),
]

AS_OF = dt.date(2026, 11, 15)
MARIA = "418302"
JAMES = "527194"
AISHA = "639251"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
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


@pytest_asyncio.fixture(loop_scope="module")
async def reader(schema):
    r = PostgresReader(os.environ["DATABASE_URL"], schema=schema)
    yield r
    await r.close()


def session(reader, member_id: str | None = MARIA, **kw):
    return ConnectorSession(reader, call_id="call-test", member_id=member_id, **kw)


async def test_claims_search_returns_only_the_call_members_claims(reader):
    out = await session(reader).call("claims_search", {})
    assert out["tool"] == "claims_search"
    assert out["source"] == "Claims system"
    ids = [c["claim_id"] for c in out["items"]]
    assert ids == ["70412", "70358"]  # newest service date first
    assert out["count"] == 2
    denied = out["items"][0]
    assert denied == {
        "claim_id": "70412",
        "service_date": "2026-10-12",
        "provider": "Riverside Imaging Center",
        "service": "MRI, knee, left",
        "billed_amount": 1850.0,
        "status": "denied",
        "denial_code": "CO-197",
        "denial_reason": "Precertification or authorization absent",
        "received_on": "2026-10-16",
        "processed_on": "2026-10-30",
    }


async def test_claims_filters_and_get(reader):
    s = session(reader)
    out = await s.call("claims_search", {"status": "denied"})
    assert [c["claim_id"] for c in out["items"]] == ["70412"]
    out = await s.call("claims_search", {"denial_code": "CO-197"})
    assert [c["claim_id"] for c in out["items"]] == ["70412"]
    out = await s.call("claims_search", {"service_from": "2026-10-01"})
    assert [c["claim_id"] for c in out["items"]] == ["70412"]
    out = await s.call("claims_search", {"provider": "lakeside"})
    assert [c["claim_id"] for c in out["items"]] == ["70358"]
    got = await s.call("claims_get", {"claim_id": "70358"})
    assert got["found"] is True
    assert got["item"]["allowed_amount"] == 120.0
    assert got["item"]["member_responsibility"] == 25.0
    assert got["item"]["auth_id"] is None


async def test_get_never_returns_another_members_row(reader):
    # 58139 belongs to James Carter; Maria's call cannot see it.
    got = await session(reader).call("claims_get", {"claim_id": "58139"})
    assert got == {"tool": "claims_get", "source": "Claims system", "found": False, "item": None}
    for tool, key, other in [
        ("payments_get", "payment_id", "PAY-000004"),
        ("referrals_get", "referral_id", "REF-700001"),
        ("prior_authorizations_get", "auth_id", "PA-500101"),
        ("interactions_get", "interaction_id", "INT-000006"),
        ("members_get", "member_id", JAMES),
    ]:
        got = await session(reader).call(tool, {key: other})
        assert got["found"] is False, tool


@pytest.mark.parametrize(
    "tool",
    ["claims_search", "payments_search", "referrals_search", "interactions_search"],
)
async def test_scoped_search_only_returns_the_call_member(reader, tool):
    # Maria and Sarah both have rows in every scoped table.
    pair = (MARIA, AISHA)
    for member in pair:
        out = await session(reader, member_id=member).call(tool, {"limit": 25})
        assert out["count"] > 0
        ids = {i[next(iter(i))] for i in out["items"]}
        for other in pair:
            if other == member:
                continue
            theirs = await session(reader, member_id=other).call(tool, {"limit": 25})
            assert ids.isdisjoint({i[next(iter(i))] for i in theirs["items"]})


async def test_explicit_other_member_id_is_refused(reader):
    out = await session(reader).call("claims_search", {"member_id": JAMES})
    assert out["error"] == "member_scope"
    same = await session(reader).call("claims_search", {"member_id": "418 302"})
    assert same["count"] == 2


async def test_scoped_tool_without_member_is_refused(reader):
    out = await session(reader, member_id=None).call("claims_search", {})
    assert out["error"] == "no_member"
    # Reference data still works on a call with no verified member.
    plans = await session(reader, member_id=None).call("plans_search", {})
    assert plans["count"] == 6


async def test_members_returns_the_caller_with_plan(reader):
    out = await session(reader).call("members_search", {})
    assert out["source"] == "Member system"
    assert out["items"] == [
        {
            "member_id": MARIA,
            "name": "Maria Lopez",
            "dob": "1984-03-12",
            "plan_id": "PLN-HMO-SILVER",
            "plan_name": "Evergreen HMO Silver",
            "plan_type": "HMO",
            "coverage_start": "2024-01-01",
            "coverage_end": None,
        }
    ]
    got = await session(reader).call("members_get", {"member_id": MARIA})
    assert got["item"]["name"] == "Maria Lopez"


async def test_plans_search_and_get(reader):
    s = session(reader, member_id=None)
    out = await s.call("plans_search", {"plan_type": "HMO"})
    assert out["source"] == "Benefits system"
    assert [p["plan_id"] for p in out["items"]] == ["PLN-HMO-GOLD", "PLN-HMO-SILVER"]
    got = await s.call("plans_get", {"plan_id": "PLN-HMO-SILVER"})
    assert got["item"] == {
        "plan_id": "PLN-HMO-SILVER",
        "name": "Evergreen HMO Silver",
        "plan_type": "HMO",
        "requires_referral": True,
        "deductible": 1500.0,
        "out_of_pocket_max": 6000.0,
        "pcp_copay": 25.0,
        "specialist_copay": 50.0,
        "monthly_premium": 412.0,
    }


async def test_payments_shows_james_duplicate_premium_draft(reader):
    s = session(reader, member_id=JAMES)
    out = await s.call("payments_search", {"kind": "premium", "paid_from": "2026-11-01"})
    assert out["source"] == "Billing system"
    assert out["items"] == [
        {
            "payment_id": "PAY-000067",
            "paid_at": "2026-11-01T06:02:00Z",
            "amount": 610.0,
            "kind": "premium",
            "method": "autopay",
            "status": "posted",
            "reference": "AP2026110067",
        },
        {
            "payment_id": "PAY-000004",
            "paid_at": "2026-11-01T06:00:00Z",
            "amount": 610.0,
            "kind": "premium",
            "method": "autopay",
            "status": "posted",
            "reference": "AP2026110004",
        },
    ]
    got = await s.call("payments_get", {"payment_id": "PAY-000067"})
    assert got["item"]["amount"] == 610.0


async def test_referrals(reader):
    out = await session(reader).call("referrals_search", {"status": "approved"})
    assert out["source"] == "Referral system"
    assert out["items"] == [
        {
            "referral_id": "REF-700002",
            "from_provider": "Lakeside Family Medicine",
            "to_provider": "Pine Hill Orthopedics",
            "specialty": "Orthopedics",
            "status": "approved",
            "created_on": "2026-08-27",
            "valid_to": "2026-11-25",
            "visits_allowed": 3,
        }
    ]


async def test_prior_authorizations(reader):
    s = session(reader, member_id="852617")
    out = await s.call("prior_authorizations_search", {"status": "pending"})
    assert out["source"] == "Utilization management"
    assert [a["auth_id"] for a in out["items"]] == ["PA-500101"]
    assert out["items"][0]["service"] == "Arthroscopic knee surgery, right"
    got = await s.call("prior_authorizations_get", {"auth_id": "PA-500101"})
    assert got["item"]["notes"] == "Awaiting clinical notes from provider."
    # Maria has no auth on file: the CO-197 story.
    none = await session(reader).call("prior_authorizations_search", {})
    assert none["count"] == 0 and none["items"] == []


async def test_interactions_caller_history(reader):
    out = await session(reader).call("interactions_search", {"since": "2026-10-16"})
    assert out["source"] == "Interaction history"
    assert [i["interaction_id"] for i in out["items"]] == ["INT-000001", "INT-000002"]
    first = out["items"][0]
    assert first["started_at"] == "2026-11-09T15:10:00Z"
    assert first["duration_seconds"] == 512
    assert first["queue"] == "claims"
    assert first["outcome"] == "escalated to claims review"
    assert first["notes"].startswith("Asked why MRI claim 70412 was denied.")
    q = await session(reader).call("interactions_search", {"queue": "general"})
    assert [i["interaction_id"] for i in q["items"]] == ["INT-000003"]


async def test_kb_keyword_search_ranks_title_tags_and_body(reader):
    s = session(reader, member_id=None)
    out = await s.call("kb_articles_search", {"query": "CO-197 denial"})
    assert out["source"] == "Knowledge base"
    assert out["items"][0]["article_id"] == "KB-CL-002"
    assert set(out["items"][0]) == {"article_id", "title", "category", "tags", "summary"}
    retro = await s.call("kb_articles_search", {"query": "retro authorization"})
    assert retro["items"][0]["article_id"] == "KB-CL-009"
    dup = await s.call("kb_articles_search", {"query": "charged twice duplicate premium"})
    assert dup["items"][0]["article_id"] == "KB-BL-001"
    billing = await s.call("kb_articles_search", {"category": "billing", "limit": 25})
    assert {a["article_id"][:5] for a in billing["items"]} == {"KB-BL"}
    nothing = await s.call("kb_articles_search", {"query": "zzzqqq"})
    assert nothing["count"] == 0
    got = await s.call("kb_articles_get", {"article_id": "KB-CL-009"})
    assert got["item"]["title"] == "Retro authorization review"
    assert got["item"]["body"]


async def test_reader_is_read_only(reader):
    import psycopg

    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        await reader.fetch("UPDATE claims SET status = 'paid' WHERE claim_id = %(c)s", {"c": "x"})


class SlowReader:
    """Real reader, but every query sleeps first: drives the 2 s timeout against Postgres."""

    def __init__(self, inner, secs):
        self.inner, self.secs = inner, secs

    async def fetch(self, query, params):
        await self.inner.fetch("SELECT pg_sleep(%(s)s)", {"s": self.secs})
        return await self.inner.fetch(query, params)

    async def close(self):
        await self.inner.close()


async def test_timeout_against_postgres_then_the_reader_recovers(reader):
    import time

    s = ConnectorSession(
        SlowReader(reader, 1.0), call_id="call-slow", member_id=MARIA, timeout_secs=0.2
    )
    start = time.perf_counter()
    out = await s.call("claims_search", {})
    assert out["error"] == "timeout"
    assert time.perf_counter() - start < 0.9
    # The cancelled connection was dropped; the next call reconnects and works.
    ok = await session(reader).call("claims_search", {})
    assert ok["count"] == 2


async def test_server_side_statement_timeout_backs_up_the_client_timeout(reader):
    import psycopg

    with pytest.raises(psycopg.errors.QueryCanceled):
        await reader.fetch("SELECT pg_sleep(3)", {})
    ok = await reader.fetch("SELECT 1 AS one", {})
    assert ok == [{"one": 1}]
