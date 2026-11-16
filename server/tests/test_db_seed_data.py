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


def test_demo_personas_exist_in_members():
    data = seed.build_seed(AS_OF)
    by_id = {m.member_id: m for m in data.members}
    # 9 seed members; the Demos page adds a not found caller who is not a member.
    assert len(seed.DEMO_PERSONAS) == 9
    for p in seed.DEMO_PERSONAS:
        m = by_id[p.member_id]
        assert (m.first_name, m.last_name, m.dob, m.plan_id) == (
            p.first_name,
            p.last_name,
            p.dob,
            p.plan_id,
        )
        assert p.reason


# ---- P5-10: demo friendly IDs and names (Chris, Nov 16) ---------------------------------


def _speakable(digits: str) -> bool:
    """No leading 0 and no digit said twice in a row ("one one" / "double one")."""
    return digits[0] != "0" and not any(a == b for a, b in zip(digits, digits[1:], strict=False))


def test_member_ids_are_six_speakable_digits():
    data = seed.build_seed(AS_OF)
    ids = [m.member_id for m in data.members]
    assert len(set(ids)) == len(ids)
    for mid in ids:
        assert mid.isdigit() and len(mid) == 6, mid
        assert _speakable(mid), mid


def test_member_id_groups_are_not_shared():
    # Read as two groups of three: no two members share either group.
    data = seed.build_seed(AS_OF)
    ids = [m.member_id for m in data.members]
    assert len({i[:3] for i in ids}) == len(ids)
    assert len({i[3:] for i in ids}) == len(ids)


def test_claim_ids_are_five_speakable_digits():
    data = seed.build_seed(AS_OF)
    ids = [c.claim_id for c in data.claims]
    assert len(set(ids)) == len(ids)
    for cid in ids:
        assert cid.isdigit() and len(cid) == 5, cid
        assert cid[0] != "0", cid
    personas = {c.claim_id for c in data.claims if c.member_id in seed.PERSONA_IDS}
    assert all(_speakable(c) for c in personas), personas


def test_claim_ids_never_look_like_member_ids():
    data = seed.build_seed(AS_OF)
    members = {m.member_id for m in data.members}
    assert not any(c.claim_id in m for c in data.claims for m in members)


def test_only_linda_nguyen_and_aisha_patel_have_hard_names():
    data = seed.build_seed(AS_OF)
    hard = {
        f"{m.first_name} {m.last_name}"
        for m in data.members
        if seed.syllables(m.first_name) > 2
        or seed.syllables(m.last_name) > 2
        or f"{m.first_name} {m.last_name}" in seed.HARD_NAMES
    }
    assert hard == {"Linda Nguyen", "Aisha Patel"}


def test_one_same_name_pair_with_different_dobs():
    data = seed.build_seed(AS_OF)
    names: dict[str, list] = {}
    for m in data.members:
        names.setdefault(f"{m.first_name} {m.last_name}", []).append(m)
    pairs = {n: ms for n, ms in names.items() if len(ms) > 1}
    assert list(pairs) == ["Mark Hill"]
    a, b = pairs["Mark Hill"]
    assert a.dob != b.dob


def test_seed_counts_are_stable():
    data = seed.build_seed(AS_OF)
    assert len(data.members) == 25
    assert len(data.plans) == 6
    assert len(data.kb_articles) == 15
    assert len(data.prior_authorizations) == 5
    assert len(data.referrals) == 6


def test_docs_db_md_row_counts_match_the_seed():
    import pathlib

    doc = (pathlib.Path(__file__).parents[2] / "docs" / "db.md").read_text()
    data = seed.build_seed(AS_OF)
    for table in ("claims", "payments", "interactions", "members"):
        assert f"{table} {len(getattr(data, table))}" in doc, table
    for p in seed.DEMO_PERSONAS:
        assert f"| {p.first_name} {p.last_name} | {p.dob.isoformat()} | {p.member_id} |" in doc


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
        if p.member_id == "418302" and p.kind == "premium"
    }
    assert months == {(2027, 3), (2027, 2), (2027, 1)}


def test_foreign_keys_resolve_in_memory():
    data = seed.build_seed(AS_OF)
    members = {m.member_id for m in data.members}
    plans = {p.plan_id for p in data.plans}
    assert {m.plan_id for m in data.members} <= plans
    for rows in (data.claims, data.payments, data.referrals, data.prior_authorizations):
        assert {r.member_id for r in rows} <= members
