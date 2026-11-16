"""Assist message contract v1 (docs/assist.md): shapes the portal (P4-4) renders."""

import json

import pytest
from agent_assist_bot import assist_contract as c


def _trigger():
    return c.Trigger(
        phrase="prior authorization",
        text="The letter said something about prior authorization.",
        role="caller",
        line_id="caller-3",
        start=34,
        end=53,
        heard_at=100.0,
    )


def _nbs_card():
    return c.Card(
        id="nbs:retro_auth_review:CLM-900001",
        kind="next_step",
        title="Next best step",
        pack_id="claims_status",
        rank=10,
        trigger=_trigger(),
        source=c.Source(label="Claims system, KB-CL-002", records=[("claims", "CLM-900001")]),
        rule_id="retro_auth_review",
        body={
            "headline": "Start a retro authorization review for CLM-900001",
            "why": "w",
            "script": "s",
            "action": {"id": "request_retro_auth_review", "label": "Request retro auth review"},
        },
    )


def test_message_envelope_is_versioned_and_typed():
    m = c.assist_message("assist_status", seq=3, ts=1.5, state="listening", pack_id="p")
    assert m == {
        "type": "assist",
        "v": 1,
        "event": "assist_status",
        "seq": 3,
        "ts": 1.5,
        "state": "listening",
        "pack_id": "p",
    }


def test_unknown_event_is_rejected():
    with pytest.raises(ValueError):
        c.assist_message("cards", seq=1, ts=0)


def test_card_dict_carries_trigger_source_and_ai_label():
    d = _nbs_card().to_dict()
    assert d["id"] == "nbs:retro_auth_review:CLM-900001"
    assert d["kind"] == "next_step"
    assert d["ai_suggested"] is True
    assert d["trigger"] == {
        "phrase": "prior authorization",
        "text": "The letter said something about prior authorization.",
        "role": "caller",
        "line_id": "caller-3",
        "start": 34,
        "end": 53,
        "heard_at": 100.0,
    }
    assert d["source"] == {
        "label": "Claims system, KB-CL-002",
        "records": [{"connector": "claims", "id": "CLM-900001"}],
    }
    assert d["rule_id"] == "retro_auth_review" and d["template_id"] is None
    assert d["body"]["action"]["id"] == "request_retro_auth_review"
    json.dumps(d)  # plain JSON


def test_hero_and_card_messages():
    card = _nbs_card()
    hero = c.hero_message(card, seq=1, ts=2.0, reason="new", replaces=None)
    assert hero["event"] == "hero" and hero["card"]["id"] == card.id
    assert hero["reason"] == "new" and hero["replaces"] is None
    cleared = c.hero_message(None, seq=2, ts=3.0, reason="accepted", replaces=card.id)
    assert cleared["card"] is None and cleared["replaces"] == card.id
    added = c.card_added_message(card, seq=3, ts=4.0)
    assert added["event"] == "card_added" and added["card"]["id"] == card.id
    removed = c.card_removed_message(card.id, "dismissed", seq=4, ts=5.0)
    assert removed == {
        "type": "assist",
        "v": 1,
        "event": "card_removed",
        "seq": 4,
        "ts": 5.0,
        "card_id": card.id,
        "reason": "dismissed",
    }


def test_hero_reason_and_remove_reason_are_checked():
    with pytest.raises(ValueError):
        c.hero_message(None, seq=1, ts=0, reason="because", replaces=None)
    with pytest.raises(ValueError):
        c.card_removed_message("x", "gone", seq=1, ts=0)


def test_plan_update_message():
    m = c.plan_update_message(
        "claims_status",
        "Claim status",
        [
            {"id": "confirm_claim", "label": "Confirm", "done": True, "by": "transcript"},
            {"id": "explain_status", "label": "Explain", "done": False, "by": None},
        ],
        seq=1,
        ts=0.0,
    )
    assert m["event"] == "plan_update"
    assert m["pack_id"] == "claims_status" and m["pack_name"] == "Claim status"
    assert m["done"] == 1 and m["total"] == 2
    assert m["steps"][0]["by"] == "transcript"


def _client_message(d, t=c.FEEDBACK_TYPE):
    return {"label": "rtvi-ai", "type": "client-message", "id": "m1", "data": {"t": t, "d": d}}


def test_parse_feedback_from_the_agent():
    fb = c.parse_feedback(
        _client_message({"v": 1, "card_id": "nbs:x:1", "action": "accept"}), "agent-1", "agent-1"
    )
    assert fb == c.Feedback(card_id="nbs:x:1", action="accept", rating=None)
    rate = c.parse_feedback(
        _client_message({"card_id": "rec:claims_table", "action": "rate", "rating": "up"}),
        "agent-1",
        "agent-1",
    )
    assert rate.rating == "up"


@pytest.mark.parametrize(
    "message, sender",
    [
        # not from the agent participant (the caller, or the server)
        (_client_message({"card_id": "a", "action": "accept"}), "caller-1"),
        (_client_message({"card_id": "a", "action": "accept"}), "api"),
        # not a feedback message
        (_client_message({"card_id": "a", "action": "accept"}, t="other"), "agent-1"),
        ({"type": "agent_assist.queue", "event": "queued"}, "agent-1"),
        ("hello", "agent-1"),
        # bad fields
        (_client_message({"card_id": "a", "action": "explode"}), "agent-1"),
        (_client_message({"card_id": "", "action": "accept"}), "agent-1"),
        (_client_message({"card_id": "a", "action": "rate", "rating": "meh"}), "agent-1"),
        (_client_message({"card_id": "a", "action": "rate"}), "agent-1"),
        (_client_message({"v": 2, "card_id": "a", "action": "accept"}), "agent-1"),
    ],
)
def test_parse_feedback_rejects(message, sender):
    assert c.parse_feedback(message, sender, "agent-1") is None


def test_parse_feedback_needs_a_known_agent():
    msg = _client_message({"card_id": "a", "action": "accept"})
    assert c.parse_feedback(msg, "agent-1", None) is None
