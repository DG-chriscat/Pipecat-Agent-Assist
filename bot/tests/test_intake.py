"""Intake flow (PRD IN-3 to IN-9): node configs, transitions, field saving, handoff.

Handlers are exercised directly with a fake FlowManager; the pipeline is not run.
"""

import datetime as dt

import pytest
from agent_assist_bot import intake
from agent_assist_bot.calls import (
    FLAG_ASKED_FOR_PERSON,
    FLAG_EMERGENCY,
    FLAG_NOT_FOUND,
    FLAG_NOT_VERIFIED,
    Member,
    MemoryCallStore,
)
from agent_assist_bot.packs import load_pack

MARIA = Member("EVM100231", "Maria", "Alvarez", dt.date(1984, 3, 12))


class FakeFlowManager:
    def __init__(self):
        self.state = {}


class RecordingHook:
    def __init__(self):
        self.handoffs = []

    async def on_intake_complete(self, handoff):
        self.handoffs.append(handoff)


@pytest.fixture
def pack():
    return load_pack("claims_status")


@pytest.fixture
def store():
    return MemoryCallStore([MARIA])


@pytest.fixture
def hook():
    return RecordingHook()


@pytest.fixture
async def session(pack, store, hook):
    s = intake.IntakeSession(pack=pack, store=store, session_id="sess-1", handoff=hook)
    await s.start()
    return s


def fn(node, name):
    for f in node["functions"]:
        if f.name == name:
            return f
    raise KeyError(f"{name} not in {[f.name for f in node['functions']]}")


async def call(node, name, fm=None, **args):
    return await fn(node, name).handler(args, fm or FakeFlowManager())


def record(session, store):
    return store.calls[session.call_id]


# ---- structure -------------------------------------------------------------


def test_step_order_follows_prd(session, pack):
    # IN-3: greeting, identity, reason, use case questions, read back.
    names = [n["name"] for n in session.all_nodes()]
    assert names == [
        "greeting",
        "identity",
        "reason",
        *[f"question_{q.key}" for q in pack.questions],
        "readback",
    ]


def test_greeting_speaks_fixed_line_then_waits(session):
    node = session.greeting_node()
    assert node["pre_actions"][0] == {"type": "tts_say", "text": intake.GREETING}
    assert "AI assistant" in intake.GREETING and "person will join" in intake.GREETING
    assert intake.GREETING == (
        "Hi, thanks for calling. You're speaking with Deepgram Vitals, an AI assistant. "
        "I'll ask a few quick questions, and then a person will join the call to help you."
    )
    assert node["respond_immediately"] is False


def test_role_message_forbids_medical_advice_and_routes_emergency_and_person(session):
    role = session.greeting_node()["role_message"]
    assert "medical advice" in role
    assert "911" in role
    assert "report_emergency" in role and "request_person" in role


@pytest.mark.parametrize("getter", ["identity_node", "reason_node", "readback_node"])
def test_every_intake_node_offers_person_and_emergency(session, getter):
    node = getattr(session, getter)()
    names = {f.name for f in node["functions"]}
    assert {"request_person", "report_emergency"} <= names


def test_question_nodes_use_pack_wording(session, pack):
    # IN-4
    for q in pack.questions:
        node = session.question_node(q.key)
        assert q.ask in node["task_messages"][0]["content"]
        assert {"save_answer", "request_person", "report_emergency"} <= {
            f.name for f in node["functions"]
        }


async def test_start_creates_call_row(session, store):
    rec = record(session, store)
    assert rec.session_id == "sess-1" and rec.status == "intake"


def test_greeting_node_is_tts_only(session):
    node = session.greeting_node()
    assert node["functions"] == []
    assert "post_actions" not in node


# ---- identity (IN-6) --------------------------------------------------------


async def test_identity_verified_saves_fields_and_moves_to_reason(session, store):
    result, nxt = await call(
        session.identity_node(),
        "verify_identity",
        full_name="Maria Alvarez",
        date_of_birth="1984-03-12",
        member_id="EVM 100231",
    )
    rec = record(session, store)
    assert rec.intake == {
        "full_name": "Maria Alvarez",
        "dob": "1984-03-12",
        "member_id": "EVM100231",
    }
    assert (rec.identity_result, rec.member_id) == ("verified", "EVM100231")
    assert rec.flags == []
    assert result["identity"] == "verified"
    assert nxt["name"] == "reason"


async def test_identity_fields_saved_before_the_check(session, store):
    await call(
        session.identity_node(),
        "verify_identity",
        full_name="Maria Alvarez",
        date_of_birth="1984-03-12",
        member_id="EVM100231",
    )
    kinds = [k for k, _ in store.writes]
    assert kinds.index("intake") < kinds.index("identity")


@pytest.mark.parametrize(
    "dob, member_id, want, flag",
    [
        ("1984-03-21", "EVM100231", "not_verified", FLAG_NOT_VERIFIED),
        ("1984-03-12", "ZZZ000000", "not_verified", FLAG_NOT_VERIFIED),
        ("2001-01-01", "ZZZ000000", "not_found", FLAG_NOT_FOUND),
    ],
)
async def test_failed_identity_is_flagged_and_still_continues(
    session, store, dob, member_id, want, flag
):
    result, nxt = await call(
        session.identity_node(),
        "verify_identity",
        full_name="Maria Alvarez",
        date_of_birth=dob,
        member_id=member_id,
    )
    rec = record(session, store)
    assert rec.identity_result == want
    assert flag in rec.flags
    assert result["identity"] == want
    assert nxt["name"] == "reason"
    # The caller is not told which detail was wrong.
    assert "1984" not in str(result) and "Alvarez" not in str(result)


# ---- reason, questions, read back (IN-5, IN-8) ------------------------------


async def test_reason_saved_then_first_question(session, store, pack):
    _, nxt = await call(session.reason_node(), "save_reason", reason="My MRI claim was denied")
    assert record(session, store).intake["reason"] == "My MRI claim was denied"
    assert nxt["name"] == f"question_{pack.questions[0].key}"


async def test_each_question_saved_as_answered_then_readback(session, store, pack):
    keys = [q.key for q in pack.questions]
    for i, key in enumerate(keys):
        _, nxt = await call(session.question_node(key), "save_answer", answer=f"answer {i}")
        assert record(session, store).intake[key] == f"answer {i}"
        want = f"question_{keys[i + 1]}" if i + 1 < len(keys) else "readback"
        assert nxt["name"] == want


async def test_pack_without_questions_goes_reason_to_readback(store, hook, tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(
        "id: x\nname: X\nidentity: {prompt: a}\nreason: {prompt: b}\nreadback: {prompt: c}\n"
    )
    s = intake.IntakeSession(pack=load_pack(p), store=store, session_id="s", handoff=hook)
    await s.start()
    _, nxt = await call(s.reason_node(), "save_reason", reason="billing")
    assert nxt["name"] == "readback"


async def test_readback_lists_captured_fields(session):
    await call(session.reason_node(), "save_reason", reason="denied MRI claim")
    content = session.readback_node()["task_messages"][0]["content"]
    assert "denied MRI claim" in content


async def test_readback_correction_saves_and_stays(session, store):
    await call(session.reason_node(), "save_reason", reason="denied claim")
    result, nxt = await call(
        session.readback_node(), "correct_field", field="reason", value="denied MRI claim"
    )
    assert record(session, store).intake["reason"] == "denied MRI claim"
    assert nxt is None
    assert result["saved"] == "reason"


async def test_readback_correction_rejects_unknown_field(session, store):
    result, nxt = await call(session.readback_node(), "correct_field", field="ssn", value="x")
    assert "ssn" not in record(session, store).intake
    assert nxt is None and "error" in result


async def test_confirm_marks_ready_for_queue_and_hands_off(session, store, hook):
    await call(session.reason_node(), "save_reason", reason="denied MRI claim")
    _, nxt = await call(
        session.readback_node(), "confirm_summary", summary="Maria Alvarez, denied MRI claim."
    )
    rec = record(session, store)
    assert rec.status == "ready_for_queue"
    assert rec.intake["summary"] == "Maria Alvarez, denied MRI claim."
    assert nxt["name"] == "handoff"
    assert nxt["functions"] == []
    assert len(hook.handoffs) == 1
    h = hook.handoffs[0]
    assert (h.call_id, h.session_id, h.outcome) == (session.call_id, "sess-1", "completed")
    assert h.intake["reason"] == "denied MRI claim"


# ---- ask for a person (IN-7) -----------------------------------------------


async def test_request_person_mid_intake_skips_to_handoff_with_what_we_have(session, store, hook):
    await call(
        session.identity_node(),
        "verify_identity",
        full_name="Maria Alvarez",
        date_of_birth="1984-03-12",
        member_id="EVM100231",
    )
    _, nxt = await call(session.reason_node(), "request_person")
    rec = record(session, store)
    assert rec.status == "ready_for_queue"
    assert FLAG_ASKED_FOR_PERSON in rec.flags
    assert "Maria Alvarez" in rec.intake["summary"]
    assert "verified" in rec.intake["summary"]
    assert hook.handoffs[0].identity_result == "verified"
    assert nxt["name"] == "handoff"
    assert hook.handoffs[0].outcome == "asked_for_person"


async def test_request_person_from_a_question_node(session, store, pack):
    _, nxt = await call(session.question_node(pack.questions[0].key), "request_person")
    assert nxt["name"] == "handoff"
    assert record(session, store).status == "ready_for_queue"


# ---- emergency (IN-9) -------------------------------------------------------


async def test_emergency_says_911_flags_and_hands_off(session, store, hook):
    _, nxt = await call(
        session.reason_node(), "report_emergency", description="chest pain right now"
    )
    rec = record(session, store)
    assert FLAG_EMERGENCY in rec.flags
    assert rec.intake["emergency"] == "chest pain right now"
    assert rec.status == "ready_for_queue"
    assert nxt["name"] == "emergency"
    say = nxt["pre_actions"][0]
    assert say["type"] == "tts_say" and "911" in say["text"] and "hang up" in say["text"]
    assert nxt["functions"] == []
    assert hook.handoffs[0].outcome == "emergency"


async def test_handoff_happens_once(session, store, hook):
    await call(session.reason_node(), "report_emergency", description="can't breathe")
    await call(session.identity_node(), "request_person")
    assert len(hook.handoffs) == 1
    assert FLAG_ASKED_FOR_PERSON in record(session, store).flags


# ---- resilience --------------------------------------------------------------


async def test_default_handoff_hook_is_noop(pack, store):
    s = intake.IntakeSession(pack=pack, store=store, session_id="s2")
    await s.start()
    _, nxt = await call(s.identity_node(), "request_person")
    assert nxt["name"] == "handoff"
    assert isinstance(intake.NoopHandoff(), intake.HandoffHook)


async def test_failing_hook_does_not_break_the_call(pack, store):
    class Boom:
        async def on_intake_complete(self, handoff):
            raise RuntimeError("queue down")

    s = intake.IntakeSession(pack=pack, store=store, session_id="s3", handoff=Boom())
    await s.start()
    _, nxt = await call(s.identity_node(), "request_person")
    assert nxt["name"] == "handoff"
    assert store.calls[s.call_id].status == "ready_for_queue"


async def test_store_errors_do_not_break_the_flow(pack, hook):
    class BrokenStore(MemoryCallStore):
        async def save_intake(self, call_id, fields):
            raise OSError("db unreachable")

    s = intake.IntakeSession(pack=pack, store=BrokenStore(), session_id="s4", handoff=hook)
    await s.start()
    _, nxt = await call(s.reason_node(), "save_reason", reason="billing")
    assert nxt["name"].startswith("question_")
