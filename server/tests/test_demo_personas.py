"""Demo personas (P5-10): the Demos page data, checked against the seed. No network."""

from __future__ import annotations

import datetime as dt

import pytest
from agent_assist_server import auth, demo_personas
from agent_assist_server.app import create_app
from agent_assist_server.db import seed
from fastapi.testclient import TestClient

DATA = seed.build_seed(dt.date(2026, 11, 15))
MEMBERS = {m.member_id: m for m in DATA.members}
PLANS = {p.plan_id: p for p in DATA.plans}
CLAIMS = {c.claim_id: c for c in DATA.claims}
KB = {a.article_id for a in DATA.kb_articles}
PERSONAS = demo_personas.load()
PASSWORD = "pw-for-tests"
SECRET = "test-session-secret-0123456789abcdef"


def test_eight_to_ten_personas_with_unique_keys():
    assert 8 <= len(PERSONAS) <= 10
    keys = [p["key"] for p in PERSONAS]
    assert len(set(keys)) == len(keys)


@pytest.mark.parametrize("p", [p for p in PERSONAS if p["in_seed"]], ids=lambda p: p["key"])
def test_persona_matches_the_seed(p):
    m = MEMBERS[p["member_id"]]
    assert p["name"] == f"{m.first_name} {m.last_name}"
    assert p["dob"] == m.dob.isoformat()
    assert p["plan_id"] == m.plan_id
    assert p["plan"] == PLANS[m.plan_id].name
    if p.get("claim_id"):
        c = CLAIMS[p["claim_id"]]
        assert c.member_id == p["member_id"]
        assert p["claim_id"] in p["case"]
    for kb in p.get("kb_articles", []):
        assert kb in KB


def test_not_found_caller_is_not_in_the_seed():
    (p,) = [p for p in PERSONAS if not p["in_seed"]]
    assert "not_found" in p["features"]
    assert p["member_id"] not in MEMBERS
    names = {(m.first_name, m.last_name) for m in DATA.members}
    assert tuple(p["name"].split(" ", 1)) not in names


def test_every_seed_demo_persona_has_a_card():
    keyed = {p["member_id"] for p in PERSONAS if p["in_seed"]}
    assert {p.member_id for p in seed.DEMO_PERSONAS} <= keyed


@pytest.mark.parametrize(
    "feature",
    [
        "identity_verified",
        "close_match",
        "repeat_caller",
        "duplicate_charge",
        "kb_article",
        "tone_tip",
        "wrap_up",
        "name_collision",
        "not_found",
        "emergency",
    ],
)
def test_every_feature_is_shown_by_some_persona(feature):
    assert any(feature in p["features"] for p in PERSONAS)


def test_features_and_packs_come_from_the_vocabulary():
    from agent_assist_bot.packs import load_packs

    packs = set(load_packs())
    for p in PERSONAS:
        assert set(p["features"]) <= set(demo_personas.FEATURES), p["key"]
        assert p["pack"] in packs


def test_required_cases_are_covered():
    by = {p["key"]: p for p in PERSONAS}
    assert by["maria_lopez"]["hero"] is True
    assert "duplicate_charge" in by["james_carter"]["features"]
    assert "tone_tip" in by["sarah_miller"]["features"]
    assert "close_match" in by["linda_nguyen"]["features"]
    assert "name_collision" in by["mark_hill"]["features"]
    emergency = [p for p in PERSONAS if "emergency" in p["features"]]
    assert emergency and all("warning" in p for p in emergency)
    assert "do not use in a customer demo without warning" in emergency[0]["warning"].lower()


def test_scripts_have_intake_then_agent_lines():
    for p in PERSONAS:
        steps = p["script"]
        assert steps, p["key"]
        whos = [s["to"] for s in steps]
        assert whos[0] == "bot" and set(whos) <= {"bot", "agent"}, p["key"]
        assert whos == sorted(whos, key=lambda w: w != "bot"), "intake lines first"
        assert all(s["say"].strip() for s in steps)


def test_member_ids_are_shown_in_groups_and_digits_only():
    for p in PERSONAS:
        assert p["member_id"].isdigit() and len(p["member_id"]) == 6
        assert p["member_id_groups"] == f"{p['member_id'][:3]} {p['member_id'][3:]}"


def test_endpoint_returns_personas_and_the_phone_line():
    client = TestClient(create_app(env={}))
    body = client.get("/demo/personas").json()
    assert [p["key"] for p in body["personas"]] == [p["key"] for p in PERSONAS]
    assert body["phone_line"] == {"number": "+1 (612) 746-7133", "password_hint": "ask Chris"}


def test_endpoint_needs_the_login():
    app = create_app(env={auth.PASSWORD_ENV: PASSWORD, auth.SECRET_ENV: SECRET})
    c = TestClient(app, base_url="http://localhost")
    assert c.get("/demo/personas").status_code == 401
    c.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    assert c.get("/demo/personas").status_code == 200
