"""Seed data shape checks that need no database (always run)."""

import datetime as dt

from agent_assist_server.db import seed

AS_OF = dt.date(2026, 11, 15)


def test_seed_is_deterministic():
    assert seed.build_seed(AS_OF) == seed.build_seed(AS_OF)


def test_seed_sizes_match_card():
    data = seed.build_seed(AS_OF)
    assert 20 <= len(data.members) <= 30
    assert 12 <= len(data.kb_articles) <= 20
    assert len({p.plan_id for p in data.plans}) >= 4
    assert len({m.plan_id for m in data.members}) >= 4


def test_every_member_has_one_to_five_interactions():
    data = seed.build_seed(AS_OF)
    per_member: dict[str, int] = {}
    for i in data.interactions:
        per_member[i.member_id] = per_member.get(i.member_id, 0) + 1
    assert set(per_member) == {m.member_id for m in data.members}
    assert all(1 <= n <= 5 for n in per_member.values())


def test_some_repeat_callers_within_30_days():
    data = seed.build_seed(AS_OF)
    cutoff = dt.datetime.combine(AS_OF, dt.time(), tzinfo=dt.UTC) - dt.timedelta(days=30)
    recent: dict[str, int] = {}
    for i in data.interactions:
        if i.started_at >= cutoff:
            recent[i.member_id] = recent.get(i.member_id, 0) + 1
    repeaters = [m for m, n in recent.items() if n >= 2]
    assert 2 <= len(repeaters) <= 8


def test_claims_include_denials_with_codes():
    data = seed.build_seed(AS_OF)
    denied = [c for c in data.claims if c.status == "denied"]
    assert len(denied) >= 4
    assert all(c.denial_code for c in denied)
    assert all(c.denial_code is None for c in data.claims if c.status != "denied")


def test_five_demo_personas_exist_in_members():
    data = seed.build_seed(AS_OF)
    ids = {m.member_id for m in data.members}
    assert len(seed.DEMO_PERSONAS) == 5
    for p in seed.DEMO_PERSONAS:
        assert p.member_id in ids
        assert p.reason


def test_all_phones_use_fictional_555_01xx_range():
    data = seed.build_seed(AS_OF)
    for m in data.members:
        assert m.phone[5:12].startswith("5550"), m.phone
        assert 100 <= int(m.phone[-4:]) <= 199, m.phone


def test_premium_history_covers_three_consecutive_months():
    data = seed.build_seed(dt.date(2027, 3, 10))
    months = {
        (p.paid_at.year, p.paid_at.month)
        for p in data.payments
        if p.member_id == "EVM100231" and p.kind == "premium"
    }
    assert months == {(2027, 3), (2027, 2), (2027, 1)}


def test_foreign_keys_resolve_in_memory():
    data = seed.build_seed(AS_OF)
    members = {m.member_id for m in data.members}
    plans = {p.plan_id for p in data.plans}
    assert {m.plan_id for m in data.members} <= plans
    for rows in (data.claims, data.payments, data.referrals, data.prior_authorizations):
        assert {r.member_id for r in rows} <= members
