"""Bot side of the queue: handoff hook, hold updates (QH-5), accept line + quiet (QH-6)."""

import asyncio
import json

import httpx
import pytest
from agent_assist_bot import handoff, hold, intake
from pipecat.frames.frames import LLMContextFrame, TTSSpeakFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

# ---- handoff hook -------------------------------------------------------------

HANDOFF = intake.IntakeHandoff(
    call_id="c-1",
    session_id="s1",
    outcome="emergency",
    summary="Caller Maria Alvarez, calling about: claim denied.",
    intake={"full_name": "Maria Alvarez", "reason": "claim denied", "emergency": "chest pain"},
    identity_result="not_verified",
    member_id=None,
    flags=("identity_not_verified", "emergency"),
)


def test_one_hook_contract():
    # P2-3's HandoffHook in intake.py is the only contract; handoff.py re-uses it.
    assert handoff.HandoffHook is intake.HandoffHook
    assert handoff.NoopHandoff is intake.NoopHandoff
    assert isinstance(handoff.QueueHandoff("http://srv"), intake.HandoffHook)


async def test_end_call_skips_hooks_without_on_call_ended():
    await handoff.end_call(intake.NoopHandoff(), "s1")  # no error


def test_handoff_from_env_defaults_to_noop():
    assert isinstance(handoff.handoff_from_env({}), intake.NoopHandoff)


def test_handoff_from_env_uses_server_url():
    h = handoff.handoff_from_env({handoff.SERVER_URL_ENV: "http://127.0.0.1:9/"})
    assert isinstance(h, handoff.QueueHandoff)
    assert h.server_url == "http://127.0.0.1:9"


async def test_queue_handoff_posts_intake_handoff_then_end():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"position": 1})

    h = handoff.QueueHandoff("http://srv", transport=httpx.MockTransport(handler))
    await h.on_intake_complete(HANDOFF)
    await handoff.end_call(h, "s1")
    assert [(r.method, r.url.path) for r in seen] == [
        ("POST", "/queue/calls"),
        ("POST", "/queue/calls/s1/end"),
    ]
    assert json.loads(seen[0].content) == {
        "session_id": "s1",
        "call_id": "c-1",
        "outcome": "emergency",
        "summary": {
            "full_name": "Maria Alvarez",
            "reason": "claim denied",
            "emergency": "chest pain",
            "summary": "Caller Maria Alvarez, calling about: claim denied.",
        },
        "identity_result": "not_verified",
        "member_id": None,
        "flags": ["identity_not_verified", "emergency"],
    }


async def test_queue_handoff_sends_bot_key_from_env():
    # P2-6: with APP_PASSWORD on, the server gives its bots AGENT_ASSIST_BOT_KEY.
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"position": 1})

    h = handoff.handoff_from_env(
        {handoff.SERVER_URL_ENV: "http://srv", handoff.BOT_KEY_ENV: "k123"}
    )
    h._transport = httpx.MockTransport(handler)
    await h.on_intake_complete(HANDOFF)
    await h.on_call_ended("s1")
    assert [r.headers.get("authorization") for r in seen] == ["Bearer k123"] * 2


async def test_queue_handoff_no_auth_header_without_key():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"position": 1})

    h = handoff.QueueHandoff("http://srv", transport=httpx.MockTransport(handler))
    await h.on_intake_complete(HANDOFF)
    assert "authorization" not in seen[0].headers


async def test_queue_handoff_raises_on_server_error():
    h = handoff.QueueHandoff(
        "http://srv", transport=httpx.MockTransport(lambda r: httpx.Response(404))
    )
    with pytest.raises(handoff.HandoffError):
        await h.on_intake_complete(HANDOFF)


async def test_queue_handoff_end_swallows_errors():
    def boom(_req):
        raise httpx.ConnectError("down")

    h = handoff.QueueHandoff("http://srv", transport=httpx.MockTransport(boom))
    await h.on_call_ended("s1")  # best effort; caller already gone


# ---- hold controller ---------------------------------------------------------------


class Spoken:
    def __init__(self):
        self.lines: list[str] = []
        self.frames: list[TTSSpeakFrame] = []

    async def __call__(self, frames):
        for f in frames:
            assert isinstance(f, TTSSpeakFrame)
            self.lines.append(f.text)
            self.frames.append(f)


def msg(event, **kw):
    return {"type": hold.MESSAGE_TYPE, "event": event, **kw}


@pytest.fixture
def spoken():
    return Spoken()


@pytest.fixture
def ctl(spoken):
    c = hold.HoldController(spoken, interval=0.05)
    yield c
    c.stop()


async def test_ignores_messages_not_from_server(ctl, spoken):
    await ctl.on_app_message(msg("agent_accepted", agent_name="Ana"), "some-participant-id")
    assert ctl.state == hold.HoldState.INTAKE
    assert ctl.gate.open


async def test_ignores_other_message_types(ctl):
    await ctl.on_app_message({"type": "other", "event": "queued"}, "api")
    await ctl.on_app_message("not a dict", "api")
    assert ctl.state == hold.HoldState.INTAKE


async def test_queued_gives_an_update_about_every_interval(ctl, spoken):
    await ctl.on_app_message(msg("queued", position=2), "api")
    assert ctl.state == hold.HoldState.QUEUED
    assert spoken.lines == []  # no update right away, the first one after the interval
    await asyncio.sleep(0.13)
    assert len(spoken.lines) == 2
    assert all("number 2 in line" in line for line in spoken.lines)


async def test_position_update_changes_next_hold_line(ctl, spoken):
    await ctl.on_app_message(msg("queued", position=3), "api")
    await ctl.on_app_message(msg("position", position=1), "api")
    await asyncio.sleep(0.07)
    assert "next in line" in spoken.lines[-1]


async def test_accept_says_connecting_then_goes_quiet(ctl, spoken):
    await ctl.on_app_message(msg("queued", position=1), "api")
    await ctl.on_app_message(msg("agent_accepted", agent_name="Ana"), "api")
    assert ctl.state == hold.HoldState.WITH_AGENT
    assert spoken.lines == [hold.CONNECTING_LINE]
    assert not ctl.gate.open
    await asyncio.sleep(0.12)
    assert spoken.lines == [hold.CONNECTING_LINE]  # hold updates stopped


async def test_accept_twice_speaks_once(ctl, spoken):
    await ctl.on_app_message(msg("agent_accepted", agent_name="Ana"), "api")
    await ctl.on_app_message(msg("agent_accepted", agent_name="Ana"), "api")
    assert spoken.lines == [hold.CONNECTING_LINE]


def test_connecting_line_text():
    assert hold.CONNECTING_LINE == "Connecting you now."
    assert hold.HOLD_INTERVAL_SECS == 60


# ---- gate --------------------------------------------------------------------------


async def test_gate_drops_llm_triggers_only_when_closed():
    gate = hold.BotGate()
    ctx_frame = LLMContextFrame(context=LLMContext())
    speak = TTSSpeakFrame("hi")

    assert gate.passes(ctx_frame, FrameDirection.DOWNSTREAM)
    gate.close()
    assert not gate.passes(ctx_frame, FrameDirection.DOWNSTREAM)
    assert gate.passes(speak, FrameDirection.DOWNSTREAM)
    assert gate.passes(ctx_frame, FrameDirection.UPSTREAM)


# ---- QH-9 agent drop and agent End (P3-5) -------------------------------------------


async def test_requeued_speaks_again_and_reopens_the_gate(ctl, spoken):
    await ctl.on_app_message(msg("queued", position=1), "api")
    await ctl.on_app_message(msg("agent_accepted", agent_name="Ana"), "api")
    await ctl.on_app_message(msg("requeued", position=1), "api")
    assert ctl.state == hold.HoldState.QUEUED
    assert ctl.agent_name is None
    assert ctl.gate.open
    assert spoken.lines == [hold.CONNECTING_LINE, hold.REQUEUED_LINE]
    await asyncio.sleep(0.07)
    assert "next in line" in spoken.lines[-1]  # hold updates are back


async def test_requeued_then_a_new_agent_accepts(ctl, spoken):
    await ctl.on_app_message(msg("agent_accepted", agent_name="Ana"), "api")
    await ctl.on_app_message(msg("requeued", position=1), "api")
    await ctl.on_app_message(msg("agent_accepted", agent_name="Ben"), "api")
    assert ctl.state == hold.HoldState.WITH_AGENT
    assert ctl.agent_name == "Ben"
    assert spoken.lines.count(hold.CONNECTING_LINE) == 2


async def test_requeued_while_not_with_an_agent_is_ignored(ctl, spoken):
    await ctl.on_app_message(msg("requeued", position=1), "api")
    assert ctl.state == hold.HoldState.INTAKE
    assert spoken.lines == []


async def test_call_ended_by_agent_says_goodbye_once(ctl, spoken):
    await ctl.on_app_message(msg("agent_accepted", agent_name="Ana"), "api")
    await ctl.on_app_message(msg("call_ended"), "api")
    await ctl.on_app_message(msg("call_ended"), "api")
    await ctl.on_app_message(msg("requeued", position=1), "api")
    assert ctl.state == hold.HoldState.ENDED
    assert spoken.lines == [hold.CONNECTING_LINE, hold.GOODBYE_LINE]


async def test_requeued_and_goodbye_lines_are_not_cut_off_by_the_caller(ctl, spoken):
    # Live run: the caller's speech interrupted the requeue line and the LLM answered
    # instead. These lines must play in full.
    await ctl.on_app_message(msg("agent_accepted", agent_name="Ana"), "api")
    await ctl.on_app_message(msg("requeued", position=1), "api")
    await ctl.on_app_message(msg("agent_accepted", agent_name="Ben"), "api")
    await ctl.on_app_message(msg("call_ended"), "api")
    by_text = {f.text: f for f in spoken.frames}
    assert by_text[hold.REQUEUED_LINE].interruptible is False
    assert by_text[hold.GOODBYE_LINE].interruptible is False


async def test_call_ended_before_an_agent_is_ignored(ctl, spoken):
    await ctl.on_app_message(msg("queued", position=1), "api")
    await ctl.on_app_message(msg("call_ended"), "api")
    assert ctl.state == hold.HoldState.QUEUED
    assert spoken.lines == []


def test_requeued_line_text():
    assert "front of the line" in hold.REQUEUED_LINE


async def test_gate_reopens():
    gate = hold.BotGate()
    gate.close()
    gate.reopen()
    assert gate.open


async def test_queue_handoff_reports_agent_left():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"status": "ok"})

    h = handoff.QueueHandoff("http://srv", transport=httpx.MockTransport(handler))
    await handoff.agent_left(h, "s1")
    assert [(r.method, r.url.path) for r in seen] == [("POST", "/queue/calls/s1/agent-left")]
    await handoff.agent_left(intake.NoopHandoff(), "s1")  # no method, no error
