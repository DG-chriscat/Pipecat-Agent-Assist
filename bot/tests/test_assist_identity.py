"""Human verification on handoff (PRD IN-13): verify hero, record gate, decision log.

A caller who is not fully verified gets a verification step as the first hero. Until
the agent marks them verified, cards built from member records go to the agent as
locked tiles (no record values), Ask answers the same way; knowledge base articles
and the call plan still show. Mark verified unlocks; Could not verify keeps the lock
and adds a callback step to the plan.
"""

from __future__ import annotations

import pytest
from agent_assist_bot.assist import VERIFY_CARD_ID, identity_gate
from agent_assist_bot.assist_contract import Ask, Feedback
from agent_assist_bot.calls import IdentityCheck
from agent_assist_bot.packs import load_packs
from test_assist import MARIA_INTAKE, Harness

LINDA_GATE = {
    "result": "close_match",
    "checks": ["dob", "member_id"],
    "heard_name": "Linda Yuen",
    "on_file_name": "Linda Nguyen",
}


@pytest.fixture(scope="module")
def packs():
    return load_packs()


async def gated(packs, gate=LINDA_GATE, **kw) -> Harness:
    h = Harness(packs, **kw)
    h.engine.agent_name = "Ana"
    await h.engine.start(MARIA_INTAKE, default_pack="claims_status", identity=gate)
    return h


def verify_hero(h):
    return [m for m in h.heroes() if m["card"] and m["card"]["kind"] == "verify"]


# --- the gate input --------------------------------------------------------------------


def test_identity_gate_from_the_intake_check():
    check = IdentityCheck("close_match", "741526", ("dob", "member_id"), "Linda Nguyen")
    assert identity_gate(check, "Linda Yuen") == LINDA_GATE
    assert identity_gate(None, "") == {
        "result": "incomplete",
        "checks": [],
        "heard_name": None,
        "on_file_name": None,
    }
    assert identity_gate(IdentityCheck("verified", "418302"), "Maria Lopez") is None


# --- verify hero -------------------------------------------------------------------------


async def test_close_match_starts_with_the_verify_hero(packs):
    h = await gated(packs)
    [hero] = verify_hero(h)
    card = hero["card"]
    assert card["id"] == VERIFY_CARD_ID and card["title"] == "Verify caller"
    body = card["body"]
    assert body["headline"] == "Confirm who you're speaking with."
    assert body["result"] == "close_match"
    assert body["items"] == [
        {"field": "member_id", "label": "Member ID", "ok": True, "heard": None, "on_file": None},
        {"field": "dob", "label": "Date of birth", "ok": True, "heard": None, "on_file": None},
        {
            "field": "full_name",
            "label": "Name",
            "ok": False,
            "heard": "Linda Yuen",
            "on_file": "Linda Nguyen",
        },
    ]
    assert "spell your last name" in body["script"]
    assert body["actions"] == [
        {"id": "mark_verified", "label": "Mark verified"},
        {"id": "could_not_verify", "label": "Could not verify"},
    ]
    identity = h.events_of("identity")[-1]
    assert identity["state"] == "pending" and identity["result"] == "close_match"


@pytest.mark.parametrize(
    "gate, script",
    [
        ({"result": "not_verified", "checks": ["full_name", "member_id"]}, "date of birth"),
        ({"result": "not_found", "checks": []}, "member ID"),
        ({"result": "incomplete", "checks": ["full_name"]}, "member ID"),
    ],
)
async def test_script_asks_for_the_item_that_did_not_match(packs, gate, script):
    h = await gated(packs, gate=gate | {"heard_name": "x y", "on_file_name": None})
    body = verify_hero(h)[0]["card"]["body"]
    assert script in body["script"]
    assert body["result"] == gate["result"]


async def test_verified_caller_has_no_gate(packs):
    h = Harness(packs)
    await h.engine.start(MARIA_INTAKE, default_pack="claims_status")
    assert verify_hero(h) == [] and h.engine.locked is False
    assert h.events_of("identity")[-1]["state"] == "verified"


async def test_a_next_step_never_takes_the_hero_while_verifying(packs):
    h = await gated(packs)
    await h.say("caller", "The letter said it was denied, no prior authorization.")
    assert h.engine.hero.id == VERIFY_CARD_ID
    assert len(h.heroes()) == 1


# --- locked tiles --------------------------------------------------------------------------


async def test_record_cards_are_sent_locked_without_record_values(packs):
    h = await gated(packs)
    await h.say("caller", "The letter said it was denied, no prior authorization.")
    added = h.added()
    assert {c["kind"] for c in added} >= {"next_step", "record"}
    for c in added:
        assert c["locked"] is True
        assert c["body"] == {"locked": True}
        assert c["source"]["records"] == []
        assert "70412" not in str(c) and "MRI" not in str(c["body"])


async def test_articles_are_not_locked(packs):
    h = Harness(packs)
    await h.engine.start(
        {"reason": "What is an explanation of benefits?"},
        default_pack="claims_status",
        identity={"result": "not_found", "checks": []},
    )
    await h.say("caller", "What is an explanation of benefits?")
    articles = [c for c in h.added() if c["kind"] == "article"]
    assert articles and all(c["locked"] is False for c in articles)


async def test_ask_answer_is_withheld_while_locked(packs):
    h = await gated(packs)
    await h.engine.on_ask(Ask("a1", "Show the last 3 claims"))
    [card] = [c for c in h.added() if c["id"] == "ask:a1"]
    assert card["locked"] is True and card["body"] == {"locked": True}
    assert h.events_of("ask_result")[-1]["status"] == "answered"


# --- mark verified -------------------------------------------------------------------------


async def test_mark_verified_unlocks_records_and_promotes_the_next_step(packs):
    h = await gated(packs)
    await h.say("caller", "The letter said it was denied, no prior authorization.")
    n = len(h.sent)
    await h.engine.on_feedback(Feedback(card_id=VERIFY_CARD_ID, action="mark_verified"))
    after = h.sent[n:]
    assert h.engine.locked is False
    resent = [m["card"] for m in after if m["event"] == "card_added"]
    assert resent and all(c["locked"] is False for c in resent)
    assert any("70412" in str(c["body"]) for c in resent)
    hero = [m for m in after if m["event"] == "hero"][-1]
    assert hero["reason"] == "verified" and hero["replaces"] == VERIFY_CARD_ID
    assert hero["card"]["id"] == "nbs:retro_auth_review:70412"
    identity = [m for m in after if m["event"] == "identity"][-1]
    assert identity["state"] == "verified" and identity["by"] == "Ana"
    assert identity["at"] == h.clock()


async def test_mark_verified_is_logged_on_the_call_record(packs):
    h = await gated(packs)
    await h.engine.on_feedback(Feedback(card_id=VERIFY_CARD_ID, action="mark_verified"))
    kind, card_id, data = h.events[-1]
    assert (kind, card_id) == ("identity_decision", VERIFY_CARD_ID)
    assert data == {
        "decision": "verified",
        "by": "Ana",
        "at": h.clock(),
        "intake_result": "close_match",
    }


async def test_mark_verified_with_no_next_step_clears_to_listening(packs):
    h = await gated(packs)
    await h.engine.on_feedback(Feedback(card_id=VERIFY_CARD_ID, action="mark_verified"))
    hero = h.heroes()[-1]
    assert hero["card"] is None and hero["reason"] == "verified"


async def test_new_cards_after_verify_are_unlocked(packs):
    h = await gated(packs)
    await h.engine.on_feedback(Feedback(card_id=VERIFY_CARD_ID, action="mark_verified"))
    await h.say("caller", "The letter said it was denied, no prior authorization.")
    assert h.engine.hero.id == "nbs:retro_auth_review:70412"
    assert all(c["locked"] is False for c in h.added())


async def test_decision_counts_once(packs):
    h = await gated(packs)
    await h.engine.on_feedback(Feedback(card_id=VERIFY_CARD_ID, action="mark_verified"))
    n = len(h.events)
    await h.engine.on_feedback(Feedback(card_id=VERIFY_CARD_ID, action="could_not_verify"))
    assert len(h.events) == n and h.engine.locked is False


# The P5-4 Linda run: STT heard "Linda Ewan", member ID and DOB matched, the name did
# not even come close, so intake said not_verified (not close_match).
LINDA_EWAN_GATE = {
    "result": "not_verified",
    "checks": ["dob", "member_id"],
    "heard_name": "Linda Ewan",
    "on_file_name": "Linda Nguyen",
}


async def test_mark_verified_on_not_verified_with_id_and_dob_unlocks_and_reranks(packs):
    h = await gated(packs, gate=LINDA_EWAN_GATE)
    await h.say("caller", "The letter said it was denied, no prior authorization.")
    n = len(h.sent)
    await h.engine.on_feedback(Feedback(card_id=VERIFY_CARD_ID, action="mark_verified"))
    after = h.sent[n:]
    assert h.engine.locked is False
    resent = [m["card"] for m in after if m["event"] == "card_added"]
    assert resent and all(c["locked"] is False for c in resent)
    hero = [m for m in after if m["event"] == "hero"][-1]
    assert hero["card"]["id"] == "nbs:retro_auth_review:70412"
    assert [m for m in after if m["event"] == "identity"][-1]["state"] == "verified"


async def test_ask_answer_unlocks_after_mark_verified(packs):
    h = await gated(packs, gate=LINDA_EWAN_GATE)
    await h.engine.on_ask(Ask("a1", "Show the last 3 claims"))
    await h.engine.on_feedback(Feedback(card_id=VERIFY_CARD_ID, action="mark_verified"))
    [card] = [c for c in h.added() if c["id"] == "ask:a1" and c["locked"] is False]
    assert "70412" in str(card["body"])


@pytest.mark.parametrize(
    "gate",
    [
        # Ben with the wrong DOB: only the member ID matched.
        {"result": "not_verified", "checks": ["member_id"]},
        # Member ID unknown; a member matched by name and DOB only.
        {"result": "not_verified", "checks": ["full_name", "dob"]},
        {"result": "not_found", "checks": []},
        {"result": "incomplete", "checks": ["member_id"]},
    ],
)
async def test_mark_verified_without_an_id_and_dob_match_keeps_records_locked(packs, gate):
    # No member is resolved unless the member ID and DOB both matched it; the name
    # alone (or the ID alone) never unlocks a member's records.
    h = await gated(packs, gate=gate | {"heard_name": "x y", "on_file_name": None})
    await h.say("caller", "The letter said it was denied, no prior authorization.")
    n = len(h.sent)
    await h.engine.on_feedback(Feedback(card_id=VERIFY_CARD_ID, action="mark_verified"))
    after = h.sent[n:]
    assert h.engine.locked is True
    assert all(m["card"]["locked"] for m in after if m["event"] == "card_added")
    assert "70412" not in str(after)
    hero = [m for m in after if m["event"] == "hero"][-1]
    assert hero["card"] is None and hero["reason"] == "verified"
    identity = [m for m in after if m["event"] == "identity"][-1]
    assert identity["state"] == "verified" and identity["by"] == "Ana"
    await h.say("caller", "Another one was out of network.")
    assert all(c["locked"] is True for c in h.added())


async def test_verify_actions_only_apply_to_the_verify_card(packs):
    h = await gated(packs)
    await h.say("caller", "It was denied.")
    other = next(iter(h.engine.supporting))
    await h.engine.on_feedback(Feedback(card_id=other, action="mark_verified"))
    assert h.engine.locked is True


# --- could not verify ----------------------------------------------------------------------


async def test_could_not_verify_keeps_records_locked_and_adds_a_callback_step(packs):
    h = await gated(packs)
    await h.say("caller", "The letter said it was denied, no prior authorization.")
    await h.engine.on_feedback(Feedback(card_id=VERIFY_CARD_ID, action="could_not_verify"))
    assert h.engine.locked is True
    hero = h.heroes()[-1]
    assert hero["replaces"] == VERIFY_CARD_ID and hero["reason"] == "not_verified"
    # Record based next steps never become the hero while the records are locked.
    assert hero["card"] is None
    plan = h.events_of("plan_update")[-1]
    assert plan["steps"][-1] == {
        "id": "offer_callback",
        "label": "Offer a callback after identity is confirmed",
        "done": False,
        "by": None,
    }
    assert h.events[-1][1] == VERIFY_CARD_ID and h.events[-1][2]["decision"] == "not_verified"
    identity = h.events_of("identity")[-1]
    assert identity["state"] == "not_verified" and identity["by"] == "Ana"
    await h.say("caller", "Another one was out of network.")
    assert all(c["locked"] is True for c in h.added())


async def test_resync_sends_the_gate_state_again(packs):
    h = await gated(packs)
    await h.say("caller", "It was denied.")
    n = len(h.sent)
    await h.engine.resync()
    again = h.sent[n:]
    assert [m["event"] for m in again if m["event"] == "identity"] == ["identity"]
    hero = [m for m in again if m["event"] == "hero"][0]
    assert hero["card"]["id"] == VERIFY_CARD_ID
    assert all(m["card"]["locked"] for m in again if m["event"] == "card_added")
