"""Packs v2 against the real seed (docs/db.md demo personas).

The in-memory tests use `seed.build_seed` and always run. The DB tests read the
seed through the P4-1 connectors in a throwaway `test_<hex>` schema (migrated,
seeded, dropped after); they skip without DATABASE_URL.
"""

import dataclasses
import datetime as dt
import os
import uuid

import pytest
import pytest_asyncio
from agent_assist_bot.connectors import ConnectorSession, PostgresReader
from agent_assist_bot.packs import connector_facts, load_packs, select_pack

AS_OF = dt.date(2026, 11, 15)
needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")


@pytest.fixture(scope="module")
def packs():
    return load_packs()


@pytest.fixture(scope="module")
def seed_data():
    from agent_assist_server.db import seed

    return seed.build_seed(AS_OF)


# --- facts helper --------------------------------------------------------------------


def test_connector_facts_prefixes_fields():
    row = {"claim_id": "70412", "denial_code": "CO-197"}
    assert connector_facts("claims", row) == {
        "claims.claim_id": "70412",
        "claims.denial_code": "CO-197",
    }


def test_connector_facts_rejects_unknown_connector():
    with pytest.raises(KeyError):
        connector_facts("ledger", {"x": 1})


# --- in memory seed ------------------------------------------------------------------


def test_persona_packs_exist(packs, seed_data):
    from agent_assist_server.db import seed

    assert {p.pack for p in seed.DEMO_PERSONAS} <= set(packs)


def test_james_chen_duplicate_premium_in_seed(packs, seed_data):
    rows = [dataclasses.asdict(p) for p in seed_data.payments if p.member_id == "527194"]
    alert = packs["account_payments"].card_template("duplicate_charge")
    dups = alert.detect.find(rows)
    assert len(dups) == 1
    dup = dups[0]
    assert dup["kind"] == "premium" and dup["paid_at"].date() == AS_OF.replace(day=1)
    # No other member has a duplicate premium draft.
    others = [dataclasses.asdict(p) for p in seed_data.payments if p.member_id != "527194"]
    by_member: dict[str, list] = {}
    for r in others:
        by_member.setdefault(r["member_id"], []).append(r)
    assert all(alert.detect.find(rs) == [] for rs in by_member.values())


def test_kb_articles_cited_by_rules_exist(packs, seed_data):
    ids = {a.article_id for a in seed_data.kb_articles}
    for pack in packs.values():
        for rule in pack.next_best_steps:
            for token in rule.source.replace(",", " ").split():
                if token.startswith("KB-"):
                    assert token in ids, (pack.id, rule.id, token)


@pytest.mark.parametrize(
    "line, pack_id",
    [
        ("They denied my MRI claim, something about a prior authorization", "claims_status"),
        ("I got charged twice for my premium this month", "account_payments"),
        ("My cardiology claim is still pending", "claims_status"),
        ("What is an EOB, I don't understand it", "general_question"),
    ],
)
def test_persona_lines_select_their_pack(packs, line, pack_id):
    assert select_pack(line, packs).id == pack_id


# --- through the connectors ----------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def reader():
    from agent_assist_server.db import connect, migrate, seed

    name = f"test_{uuid.uuid4().hex[:12]}"
    async with await connect() as admin:
        await admin.execute(f'CREATE SCHEMA "{name}"')
    try:
        async with await connect(schema=name) as c:
            await migrate.apply_migrations(c)
            await seed.load_seed(c, as_of=AS_OF)
        r = PostgresReader(os.environ["DATABASE_URL"], schema=name)
        yield r
        await r.close()
    finally:
        async with await connect() as admin:
            await admin.execute(f'DROP SCHEMA "{name}" CASCADE')


def _session(reader, member_id):
    return ConnectorSession(reader, call_id="call-test", member_id=member_id)


@needs_db
@pytest.mark.asyncio(loop_scope="module")
async def test_maria_lopez_hits_retro_auth_review(packs, reader):
    pack = packs["claims_status"]
    line = "They said my MRI was denied because there was no prior authorization."
    assert select_pack(line, packs) is pack
    s = _session(reader, "418302")
    denied = await s.call("claims_search", dict(pack.card_template("denied_claim").search))
    assert [c["claim_id"] for c in denied["items"]] == ["70412"]
    claim = (await s.call("claims_get", {"claim_id": "70412"}))["item"]
    facts = connector_facts("claims", claim)
    fired = [r for r in pack.rules_triggered(line) if r.applies(facts)]
    assert [r.id for r in fired] == ["retro_auth_review"]
    card = fired[0].render(facts)
    assert card.headline == "Start a retro authorization review for 70412"
    assert "Riverside Imaging Center" in card.script
    assert "{" not in card.why + card.script


@needs_db
@pytest.mark.asyncio(loop_scope="module")
async def test_james_chen_hits_duplicate_alert_and_refund(packs, reader):
    pack = packs["account_payments"]
    line = "I was charged twice for my premium this month"
    assert select_pack(line, packs) is pack
    alert = pack.card_template("duplicate_charge")
    s = _session(reader, "527194")
    rows = (await s.call("payments_search", dict(alert.search)))["items"]
    dups = alert.detect.find(rows)
    assert len(dups) == 1
    facts = connector_facts("payments", dups[0])
    fired = [r for r in pack.rules_triggered(line) if r.applies(facts)]
    assert [r.id for r in fired] == ["duplicate_premium_refund"]
    card = fired[0].render(facts)
    assert dups[0]["payment_id"] in card.headline
    assert card.action_id == "submit_premium_refund"


@needs_db
@pytest.mark.asyncio(loop_scope="module")
async def test_aisha_and_linda_and_daniel_hit_their_rules(packs, reader):
    pack = packs["claims_status"]
    cases = [
        ("639251", "claims_get", {"claim_id": "31845"}, "claims",
         "my claim is still pending", "provider_info_follow_up"),
        ("741526", "claims_get", {"claim_id": "52976"}, "claims",
         "it was denied as out of network", "out_of_network_appeal"),
        ("852617", "prior_authorizations_get", {"auth_id": "PA-500101"},
         "prior_authorizations", "is my prior authorization approved yet",
         "prior_auth_pending"),
    ]  # fmt: skip
    for member, tool, args, conn, line, rule_id in cases:
        item = (await _session(reader, member).call(tool, args))["item"]
        facts = connector_facts(conn, item)
        fired = [r.id for r in pack.rules_triggered(line) if r.applies(facts)]
        assert fired == [rule_id], (member, fired)


@needs_db
@pytest.mark.asyncio(loop_scope="module")
async def test_linda_eob_question_finds_kb_article(packs, reader):
    pack = packs["general_question"]
    t = pack.card_template("kb_article")
    out = await _session(reader, "741526").call("kb_articles_search", {"query": "what is an EOB"})
    top = out["items"][0]
    assert top["article_id"] == "KB-CL-010"
    assert set(t.fields) <= set(top)
    assert out["source"] == t.source
