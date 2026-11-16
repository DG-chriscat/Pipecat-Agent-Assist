"""Assist in the bot (P4-3): accept starts it, lines feed it, messages reach the agent only."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent_assist_bot.assist_contract import ASK_TYPE, FEEDBACK_TYPE
from agent_assist_bot.assist_runtime import AssistSession, EventRecorder
from agent_assist_bot.calls import IdentityCheck, MemoryCallStore
from agent_assist_bot.packs import load_pack, load_packs
from agent_assist_bot.transcript import CallRoles, transcript_message
from assist_fakes import FakeConnectors, FakeLLM
from pipecat.transports.daily.transport import DailyOutputTransportMessageUrgentFrame

INTAKE = {
    "full_name": "Maria Alvarez",
    "dob": "1984-03-12",
    "member_id": "EVM100231",
    "reason": "MRI claim denied",
    "claim_number": "CLM-900001",
}


class Out:
    def __init__(self):
        self.frames: list[DailyOutputTransportMessageUrgentFrame] = []

    async def send_message(self, frame):
        self.frames.append(frame)

    def data(self) -> list[dict]:
        return [f.message["data"] for f in self.frames]


async def make(identity="verified", *, agent_joined=True, llm=None, member_id="EVM100231"):
    store = MemoryCallStore()
    call_id = await store.create_call("s1")
    intake = SimpleNamespace(
        pack=load_pack("claims_status"),
        intake=dict(INTAKE),
        identity=IdentityCheck(identity, member_id),
        call_id=call_id,
    )
    roles = CallRoles()
    roles.set_caller("c1")
    roles.joined("c1", "caller")
    if agent_joined:
        roles.joined("a1", "Agent Ana")
    out = Out()
    made = {}

    def connectors(call_id, member_id):
        made["member_id"] = member_id
        made["call_id"] = call_id
        return FakeConnectors()

    s = AssistSession(
        packs=load_packs(),
        roles=roles,
        output=out,
        store=store,
        intake=intake,
        connectors_factory=connectors,
        llm=llm,
        llm_debounce_secs=None,
    )
    return s, roles, out, store, call_id, made


async def accept(s, roles):
    roles.agent_accepted("Agent Ana")
    await s.on_accept()


async def test_accept_starts_the_engine_with_the_verified_member():
    s, roles, out, _, call_id, made = await make()
    await accept(s, roles)
    assert made == {"call_id": call_id, "member_id": "EVM100231"}
    assert [d["event"] for d in out.data()] == ["assist_status", "plan_update", "identity"]
    assert all(f.participant_id == "a1" for f in out.frames)
    assert all(f.message["label"] == "rtvi-ai" for f in out.frames)
    assert all(f.message["type"] == "server-message" for f in out.frames)
    # dob is not handed to the assist
    assert "dob" not in s.engine.intake


async def test_unverified_caller_is_scoped_to_the_id_match_but_locked():
    # IN-13: records of the member matched by member ID are fetched, but go to the
    # agent as locked tiles until the agent marks the caller verified.
    s, roles, out, store, _, made = await make("close_match")
    await accept(s, roles)
    assert made["member_id"] == "EVM100231"
    assert s.engine.locked is True and s.engine.agent_name == "Agent Ana"
    hero = [d for d in out.data() if d["event"] == "hero"][0]
    assert hero["card"]["kind"] == "verify"
    s.on_final(transcript_message("caller", "It was denied.", True, 1.0, "caller-1"))
    await s.drain()
    added = [d["card"] for d in out.data() if d["event"] == "card_added"]
    assert added and all(c["locked"] for c in added)
    await s.on_app_message(_feedback("verify:identity", "mark_verified"), "a1")
    assert s.engine.locked is False
    await s.close()
    decisions = [w for w in store.writes if w == ("assist", "identity_decision")]
    assert decisions


async def test_not_found_caller_gets_no_member_scope():
    s, roles, _, _, _, made = await make("not_found", member_id=None)
    await accept(s, roles)
    assert made["member_id"] is None


async def test_verified_caller_is_not_gated():
    s, roles, out, _, _, _ = await make()
    await accept(s, roles)
    assert s.engine.locked is False
    assert [d["state"] for d in out.data() if d["event"] == "identity"] == ["verified"]


async def test_final_lines_make_cards_for_the_agent_only():
    s, roles, out, store, call_id, _ = await make()
    await accept(s, roles)
    s.on_final(transcript_message("caller", "It was denied.", True, 1.0, "caller-1"))
    await s.drain()
    events = [d["event"] for d in out.data()]
    assert "hero" in events
    assert all(f.participant_id == "a1" for f in out.frames)
    await s.close()
    kinds = [w for w in store.writes if w[0] == "assist"]
    assert [k[1] for k in kinds].count("card_sent") == 2


async def test_nothing_is_sent_before_the_agent_joins_then_resync_on_join():
    s, roles, out, _, _, _ = await make(agent_joined=False)
    await accept(s, roles)
    s.on_final(transcript_message("caller", "It was denied.", True, 1.0, "caller-1"))
    await s.drain()
    assert out.frames == []
    roles.joined("a1", "Agent Ana")
    await s.on_participant_joined("a1")
    events = [d["event"] for d in out.data()]
    assert events[:2] == ["assist_status", "plan_update"] and "hero" in events
    assert all(f.participant_id == "a1" for f in out.frames)


async def test_lines_before_accept_are_ignored():
    s, roles, out, _, _, _ = await make()
    s.on_final(transcript_message("caller", "It was denied.", True, 1.0, "caller-1"))
    await s.drain()
    assert out.frames == []


def _feedback(card_id, action):
    return {
        "label": "rtvi-ai",
        "type": "client-message",
        "id": "x",
        "data": {"t": FEEDBACK_TYPE, "d": {"v": 1, "card_id": card_id, "action": action}},
    }


async def test_agent_feedback_is_applied_and_logged_but_caller_feedback_is_not():
    s, roles, out, store, call_id, _ = await make()
    await accept(s, roles)
    s.on_final(transcript_message("caller", "It was denied.", True, 1.0, "caller-1"))
    await s.drain()
    hero = s.engine.hero
    await s.on_app_message(_feedback(hero.id, "accept"), "c1")  # the caller
    assert s.engine.hero is hero
    await s.on_app_message(_feedback(hero.id, "accept"), "a1")  # the agent
    assert s.engine.hero is None
    await s.close()
    rec = store.calls[call_id]
    accepts = [e for e in rec.assist_events if e[0] == "accept"]
    assert accepts == [
        (
            "accept",
            hero.id,
            {
                "kind": "next_step",
                "pack_id": "claims_status",
                "action_id": "request_retro_auth_review",
                "simulated": True,
            },
        )
    ]


async def test_close_stops_the_engine():
    s, roles, _, _, _, _ = await make(llm=FakeLLM())
    await accept(s, roles)
    await s.close()
    assert s.engine.stopped


def _ask(ask_id, text):
    return {
        "label": "rtvi-ai",
        "type": "client-message",
        "id": "x",
        "data": {"t": ASK_TYPE, "d": {"v": 1, "ask_id": ask_id, "text": text}},
    }


async def test_agent_ask_answers_to_the_agent_only_and_caller_ask_is_ignored():
    s, roles, out, store, call_id, _ = await make()
    await accept(s, roles)
    await s.on_app_message(_ask("q1", "Show the last 3 claims"), "c1")  # the caller
    await s.on_app_message(_ask("q2", "Show the last 3 claims"), "a1")  # the agent
    await s.drain_asks()
    data = out.data()
    added = [d["card"]["id"] for d in data if d["event"] == "card_added"]
    assert added == ["ask:q2"]
    [res] = [d for d in data if d["event"] == "ask_result"]
    assert res["ask_id"] == "q2" and res["status"] == "answered"
    assert all(f.participant_id == "a1" for f in out.frames)
    await s.close()
    sent = [e for e in store.calls[call_id].assist_events if e[0] == "card_sent"]
    assert [e[1] for e in sent] == ["ask:q2"]


async def test_ask_before_accept_is_ignored():
    s, roles, out, _, _, _ = await make()
    await s.on_app_message(_ask("q1", "Show the last 3 claims"), "a1")
    await s.drain_asks()
    assert out.frames == []


async def test_event_recorder_survives_store_errors():
    class Broken(MemoryCallStore):
        async def add_assist_event(self, *a):
            raise RuntimeError("db down postgres://secret")

    store = Broken()
    call_id = await store.create_call("s")
    rec = EventRecorder(store, lambda: call_id)
    rec.add("card_sent", "x", {})
    await rec.close()


async def test_event_recorder_writes_in_order():
    store = MemoryCallStore()
    call_id = await store.create_call("s")
    rec = EventRecorder(store, lambda: call_id)
    for i in range(3):
        rec.add("card_sent", f"c{i}", {"i": i})
    await asyncio.wait_for(rec.close(), 2)
    assert [e[1] for e in store.calls[call_id].assist_events] == ["c0", "c1", "c2"]
