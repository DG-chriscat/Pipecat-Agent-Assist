"""Stall guards for intake (Nov 16 prod call: stuck in identity_confirm, never queued).

- Confirm nodes force a tool call (tool_choice "required"); say_to_caller is the
  only way to speak there and refuses transfer promises.
- A plain yes in a confirm node with no function call is confirmed in code.
- The watchdog hands off with intake_stalled after 3 caller turns without a
  function call, or when intake runs over its time budget.
- Every node change and function call logs a [flow] line.
"""

import asyncio
import datetime as dt

import pytest
from agent_assist_bot import intake
from agent_assist_bot.calls import FLAG_INTAKE_STALLED, Member, MemoryCallStore
from agent_assist_bot.packs import load_pack
from loguru import logger
from pipecat.flows import NO_RESPONSE
from pipecat.frames.frames import LLMSetToolChoiceFrame, TTSSpeakFrame

JAMES = Member("EVM100232", "James", "Chen", dt.date(1979, 7, 4))
FAST = 0.05  # check_after_turn_secs in tests


class FakeWorker:
    def __init__(self):
        self.frames = []

    async def queue_frames(self, frames):
        self.frames.extend(frames)


class FakeFlowManager:
    """Runs a node's intake entry action like FlowManager._set_node does."""

    def __init__(self):
        self.state = {}
        self.worker = FakeWorker()
        self.nodes = []

    async def set_node_from_config(self, node):
        self.nodes.append(node["name"])
        await enter(node, self)


class RecordingHook:
    def __init__(self):
        self.handoffs = []

    async def on_intake_complete(self, handoff):
        self.handoffs.append(handoff)


async def enter(node, fm):
    for action in node.get("pre_actions", []):
        if action["type"] == "intake_node_entered":
            await action["handler"](action, fm)


def fn(node, name):
    return next(f for f in node["functions"] if f.name == name)


async def call(node, name, fm, **args):
    return await fn(node, name).handler(args, fm)


@pytest.fixture
def store():
    return MemoryCallStore([JAMES])


@pytest.fixture
def hook():
    return RecordingHook()


@pytest.fixture
def fm():
    return FakeFlowManager()


@pytest.fixture
async def session(store, hook, fm):
    s = intake.IntakeSession(
        pack=load_pack("claims_status"),
        store=store,
        session_id="sess-stall",
        handoff=hook,
        check_after_turn_secs=FAST,
    )
    await s.start()
    s.flow_manager = fm
    yield s
    await s.close()


@pytest.fixture
def logs():
    lines = []
    sink = logger.add(lambda m: lines.append(m.record["message"]), level="INFO")
    yield lines
    logger.remove(sink)


async def settle():
    await asyncio.sleep(FAST * 4)


async def at_identity_confirm(session, fm):
    """The Nov 16 call's read back (James Chen), waiting for the caller's yes."""
    await fm.set_node_from_config(session.identity_node())
    _, confirm = await call(
        session.identity_node(),
        "save_identity",
        fm,
        full_name="James Chen",
        date_of_birth="1979-07-04",
        member_id="EVM100232",
    )
    await fm.set_node_from_config(confirm)
    return confirm


async def at_readback(session, fm):
    await at_identity_confirm(session, fm)
    _, reason = await call(session.reason_node(), "save_reason", fm, reason="charged twice")
    for q in session.pack.questions:
        await call(session.question_node(q.key), "save_answer", fm, answer="unknown")
    node = session.readback_node()
    await fm.set_node_from_config(node)
    return node


# ---- affirmative replies --------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Correctamundo.",
        "Correctamundo!",
        "Correct-a-mundo",
        "Correct, Amundo.",
        "Correct, Amanda.",
        "yep",
        "Yep.",
        "that is right",
        "That's right.",
        "Yes.",
        "Yeah, that's correct.",
        "Uh huh.",
        "Mm-hmm",
        "Sounds good, thanks.",
        "Yes, that's right. Thank you.",
        "right",
    ],
)
def test_affirmative_replies(text):
    assert intake.is_affirmative(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "No.",
        "No, my name is James Chen.",
        "Yes, but my member ID is E V M one zero zero two three two.",
        "Not quite.",
        "um",
        "thanks",
        "That's wrong.",
        "Is that right?",
        "I'm calling about my premium",
    ],
)
def test_not_affirmative(text):
    assert not intake.is_affirmative(text)


# ---- 1. confirm nodes force a function call --------------------------------------


async def test_confirm_nodes_force_a_tool_call_and_others_do_not(session, fm):
    await at_readback(session, fm)
    choices = {}
    names = iter(fm.nodes)
    for f in fm.worker.frames:
        if isinstance(f, LLMSetToolChoiceFrame):
            choices.setdefault(next(names), f.tool_choice)
    assert choices["identity_confirm"] == "required"
    assert choices["readback"] == "required"
    assert choices["identity"] == "auto"
    # The choice must survive a caller barging in while it is queued.
    assert all(
        not f.interruptible for f in fm.worker.frames if isinstance(f, LLMSetToolChoiceFrame)
    )


async def test_entry_action_runs_before_the_read_back_speech(session, fm):
    confirm = await at_identity_confirm(session, fm)
    types = [a["type"] for a in confirm["pre_actions"]]
    assert types.index("intake_node_entered") < types.index("tts_say")


@pytest.mark.parametrize("getter", ["identity_confirm_node", "readback_node"])
def test_confirm_nodes_offer_say_to_caller(session, getter):
    node = getattr(session, getter)()
    assert intake.SAY_TO_CALLER in {f.name for f in node["functions"]}
    assert "Every reply in this step is a function call" in node["task_messages"][0]["content"]


async def test_say_to_caller_speaks_and_waits_for_the_caller(session, fm):
    confirm = await at_identity_confirm(session, fm)
    result, nxt = await call(confirm, "say_to_caller", fm, text="Sorry, is that right?")
    assert nxt is NO_RESPONSE
    spoken = [f.text for f in fm.worker.frames if isinstance(f, TTSSpeakFrame)]
    assert spoken[-1] == "Sorry, is that right?"


@pytest.mark.parametrize(
    "text",
    [
        "Great, thank you, James. A person will join the call shortly to help you.",
        "I'm transferring you now.",
        "Let me connect you with someone.",
        "Someone will be with you shortly.",
    ],
)
async def test_say_to_caller_refuses_transfer_promises(session, fm, text):
    confirm = await at_identity_confirm(session, fm)
    before = len(fm.worker.frames)
    result, nxt = await call(confirm, "say_to_caller", fm, text=text)
    assert "error" in result
    assert not any(isinstance(f, TTSSpeakFrame) for f in fm.worker.frames[before:])
    # A second refusal does not loop the LLM again.
    _, nxt2 = await call(confirm, "say_to_caller", fm, text=text)
    assert nxt is None and nxt2 is NO_RESPONSE


def test_role_message_forbids_promising_a_transfer():
    assert "Never promise a transfer yourself" in intake.ROLE_MESSAGE


def test_only_the_handoff_node_says_a_person_is_coming(session):
    for node in session.all_nodes()[1:]:
        for a in node.get("pre_actions", []):
            assert "person" not in a.get("text", "")
    assert "person" in intake.HANDOFF_LINE


async def test_not_verified_note_does_not_promise_a_person(store, hook, fm):
    s = intake.IntakeSession(
        pack=load_pack("claims_status"), store=store, session_id="s-nv", handoff=hook
    )
    await s.start()
    await call(
        s.identity_node(),
        "save_identity",
        fm,
        full_name="James Chen",
        date_of_birth="1980-01-01",
        member_id="EVM100232",
    )
    result, _ = await call(s.identity_confirm_node(), "verify_identity", fm)
    assert result["identity"] == "not_verified"
    assert "person" not in result["note"]


# ---- 2. deterministic fallback ----------------------------------------------------


@pytest.mark.parametrize("reply", ["Correctamundo", "yep", "that is right"])
async def test_identity_confirm_yes_without_a_function_call_verifies(session, fm, store, reply):
    # The LLM answers in text (no function call), as on Nov 16.
    await at_identity_confirm(session, fm)
    await session.on_caller_turn(reply)
    await settle()
    rec = store.calls[session.call_id]
    assert rec.identity_result == "verified"
    assert rec.member_id == "EVM100232"
    assert fm.nodes[-1] == "reason"
    assert session.node == "reason"


@pytest.mark.parametrize("reply", ["Correctamundo", "yep", "that is right"])
async def test_readback_yes_without_a_function_call_confirms_and_queues(
    session, fm, store, hook, reply
):
    await at_readback(session, fm)
    await session.on_caller_turn(reply)
    await settle()
    assert store.calls[session.call_id].status == "ready_for_queue"
    assert len(hook.handoffs) == 1
    h = hook.handoffs[0]
    assert h.outcome == "completed"
    assert FLAG_INTAKE_STALLED not in h.flags
    assert "James Chen" in h.summary
    assert fm.nodes[-1] == "handoff"


async def test_no_fallback_when_the_llm_calls_the_function(session, fm, store, logs):
    confirm = await at_identity_confirm(session, fm)
    await session.on_caller_turn("Correctamundo")
    _, nxt = await call(confirm, "verify_identity", fm)
    await fm.set_node_from_config(nxt)
    await settle()
    assert fm.nodes.count("reason") == 1
    assert not any("confirming in code" in line for line in logs)


async def test_say_to_caller_is_not_progress(session, fm, store):
    # Forced tool use could pick say_to_caller ("Great, thanks!") over verify_identity.
    confirm = await at_identity_confirm(session, fm)
    await session.on_caller_turn("Correctamundo")
    await call(confirm, "say_to_caller", fm, text="Great, thanks.")
    await settle()
    assert store.calls[session.call_id].identity_result == "verified"
    assert fm.nodes[-1] == "reason"


async def test_no_fallback_for_a_correction(session, fm, store):
    await at_identity_confirm(session, fm)
    await session.on_caller_turn("No, my last name is Chen.")
    await settle()
    assert store.calls[session.call_id].identity_result is None
    assert fm.nodes[-1] == "identity_confirm"


async def test_no_fallback_outside_confirm_nodes(session, fm, store):
    await at_identity_confirm(session, fm)
    await fm.set_node_from_config(session.reason_node())
    await session.on_caller_turn("yes")
    await settle()
    assert "reason" not in store.calls[session.call_id].intake
    assert fm.nodes[-1] == "reason"


async def test_a_late_llm_call_after_the_fallback_is_ignored(session, fm, store, hook):
    confirm = await at_identity_confirm(session, fm)
    await session.on_caller_turn("Correctamundo")
    await settle()
    assert fm.nodes[-1] == "reason"
    result, nxt = await call(confirm, "verify_identity", fm)
    assert nxt is NO_RESPONSE and "ignored" in result["status"]
    assert fm.nodes.count("reason") == 1


# ---- 4. watchdog --------------------------------------------------------------


async def test_three_turns_without_a_function_call_hand_off_as_stalled(session, fm, store, hook):
    await at_identity_confirm(session, fm)
    _, nxt = await call(session.identity_confirm_node(), "verify_identity", fm)
    await fm.set_node_from_config(nxt)  # reason
    for text in ["Hmm, well.", "I'm not sure how to say it.", "Can you hear me?"]:
        await session.on_caller_turn(text)
        await settle()
        session.on_bot_turn()
    assert len(hook.handoffs) == 1
    h = hook.handoffs[0]
    assert FLAG_INTAKE_STALLED in h.flags
    assert h.identity_result == "verified" and h.member_id == "EVM100232"
    assert "James Chen" in h.summary
    rec = store.calls[session.call_id]
    assert rec.status == "ready_for_queue" and FLAG_INTAKE_STALLED in rec.flags
    assert fm.nodes[-1] == "handoff"


async def test_two_turns_without_a_function_call_do_not_hand_off(session, fm, hook):
    await at_identity_confirm(session, fm)
    _, nxt = await call(session.identity_confirm_node(), "verify_identity", fm)
    await fm.set_node_from_config(nxt)
    for text in ["Hmm.", "Sorry, what?"]:
        await session.on_caller_turn(text)
        await settle()
        session.on_bot_turn()
    assert hook.handoffs == []


async def test_a_function_call_resets_the_turn_count(session, fm, hook):
    await fm.set_node_from_config(session.reason_node())
    for text in ["Hmm.", "Well."]:
        await session.on_caller_turn(text)
        session.on_bot_turn()
    await settle()
    _, nxt = await call(session.reason_node(), "save_reason", fm, reason="premium")
    await fm.set_node_from_config(nxt)
    for text in ["Hmm.", "Well."]:
        await session.on_caller_turn(text)
        session.on_bot_turn()
    await settle()
    assert hook.handoffs == []


async def test_intake_time_budget_hands_off_as_stalled(store, hook, fm):
    s = intake.IntakeSession(
        pack=load_pack("claims_status"),
        store=store,
        session_id="sess-timer",
        handoff=hook,
        intake_max_secs=0.05,
    )
    await s.start()
    await fm.set_node_from_config(s.identity_node())
    s.start_watchdog(fm)
    await asyncio.sleep(0.2)
    assert len(hook.handoffs) == 1
    assert FLAG_INTAKE_STALLED in hook.handoffs[0].flags
    assert fm.nodes[-1] == "handoff"
    await s.close()


async def test_watchdog_does_nothing_after_handoff(session, fm, hook):
    await fm.set_node_from_config(session.reason_node())
    await call(session.reason_node(), "request_person", fm)
    for _ in range(4):
        await session.on_caller_turn("hello?")
        session.on_bot_turn()
    await settle()
    assert len(hook.handoffs) == 1
    assert FLAG_INTAKE_STALLED not in hook.handoffs[0].flags


async def test_close_cancels_the_timer(store, hook, fm):
    s = intake.IntakeSession(
        pack=load_pack("claims_status"),
        store=store,
        session_id="sess-close",
        handoff=hook,
        intake_max_secs=0.05,
    )
    await s.start()
    s.start_watchdog(fm)
    await s.close()
    await asyncio.sleep(0.15)
    assert hook.handoffs == []


# ---- 5. [flow] log lines -------------------------------------------------------


async def test_flow_lines_for_node_changes_and_function_calls(session, fm, logs):
    confirm = await at_identity_confirm(session, fm)
    await call(confirm, "correct_identity", fm, full_name="James Chen")
    flow = [line for line in logs if line.startswith("[flow]")]
    assert "[flow] node -> identity (tool_choice=auto)" in flow
    assert "[flow] node -> identity_confirm (tool_choice=required)" in flow
    assert any(line.startswith("[flow] identity: function save_identity(") for line in flow)
    assert "[flow] identity: save_identity -> identity_confirm" in flow
    assert any("function correct_identity(full_name)" in line for line in flow)
    # Argument names only, never values (PHI).
    assert not any("James" in line or "1979" in line for line in flow)


# ---- one reply split into several turn events -------------------------------------


async def test_split_reply_counts_as_one_turn(session, fm, hook):
    # Live run: STT ended "Correctamundo." in two turn events with no bot speech.
    await fm.set_node_from_config(session.reason_node())
    for text in ["I was charged", "twice", "for my premium"]:
        await session.on_caller_turn(text)
    await settle()
    assert hook.handoffs == []
    assert session._turns_without_progress == 1


async def test_yes_is_judged_on_the_whole_reply(session, fm, store):
    await at_identity_confirm(session, fm)
    await session.on_caller_turn("Yes,")
    await session.on_caller_turn("but my member ID is E V M one zero zero two three two.")
    await settle()
    assert store.calls[session.call_id].identity_result is None
    assert fm.nodes[-1] == "identity_confirm"


async def test_yes_split_in_two_still_confirms(session, fm, store):
    await at_identity_confirm(session, fm)
    await session.on_caller_turn("Correct")
    await session.on_caller_turn("amundo.")
    await settle()
    assert store.calls[session.call_id].identity_result == "verified"
