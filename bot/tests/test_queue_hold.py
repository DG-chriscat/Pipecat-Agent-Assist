"""Bot side of the queue: handoff hook, hold updates (QH-5), accept line + quiet (QH-6)."""

import asyncio
import json

import httpx
import pytest
from agent_assist_bot import handoff, hold
from pipecat.frames.frames import LLMContextFrame, TTSSpeakFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

# ---- handoff hook -------------------------------------------------------------


async def test_noop_handoff_does_nothing():
    h = handoff.NoopHandoff()
    await h.on_intake_complete("s1", {"reason": "x"})
    await h.on_call_ended("s1")


def test_handoff_from_env_defaults_to_noop():
    assert isinstance(handoff.handoff_from_env({}), handoff.NoopHandoff)


def test_handoff_from_env_uses_server_url():
    h = handoff.handoff_from_env({handoff.SERVER_URL_ENV: "http://127.0.0.1:9/"})
    assert isinstance(h, handoff.QueueHandoff)
    assert h.server_url == "http://127.0.0.1:9"


async def test_queue_handoff_posts_summary_then_end():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"position": 1})

    h = handoff.QueueHandoff("http://srv", transport=httpx.MockTransport(handler))
    await h.on_intake_complete("s1", {"reason": "claim denied"})
    await h.on_call_ended("s1")
    assert [(r.method, r.url.path) for r in seen] == [
        ("POST", "/queue/calls"),
        ("POST", "/queue/calls/s1/end"),
    ]
    assert json.loads(seen[0].content) == {
        "session_id": "s1",
        "summary": {"reason": "claim denied"},
    }


async def test_queue_handoff_raises_on_server_error():
    h = handoff.QueueHandoff(
        "http://srv", transport=httpx.MockTransport(lambda r: httpx.Response(404))
    )
    with pytest.raises(handoff.HandoffError):
        await h.on_intake_complete("s1", {})


async def test_queue_handoff_end_swallows_errors():
    def boom(_req):
        raise httpx.ConnectError("down")

    h = handoff.QueueHandoff("http://srv", transport=httpx.MockTransport(boom))
    await h.on_call_ended("s1")  # best effort; caller already gone


# ---- hold controller ---------------------------------------------------------------


class Spoken:
    def __init__(self):
        self.lines: list[str] = []

    async def __call__(self, frames):
        for f in frames:
            assert isinstance(f, TTSSpeakFrame)
            self.lines.append(f.text)


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
