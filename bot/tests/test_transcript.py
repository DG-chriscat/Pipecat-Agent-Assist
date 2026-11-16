"""P3-4: live transcript to the agent portal (role mapping, message shaping, routing)."""

from __future__ import annotations

import asyncio

from agent_assist_bot.calls import MemoryCallStore
from agent_assist_bot.transcript import (
    AGENT,
    CALLER,
    MESSAGE_TYPE,
    AudioRoute,
    CallRoles,
    LineIds,
    TranscriptRecorder,
    TranscriptRelay,
    rtvi_server_message,
    transcript_message,
)
from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    TextFrame,
    TranscriptionFrame,
    UserAudioRawFrame,
)
from pipecat.tests.utils import run_test
from pipecat.transports.daily.transport import DailyOutputTransportMessageUrgentFrame

PCM = b"\x00\x00" * 320


def _audio(user_id: str) -> UserAudioRawFrame:
    return UserAudioRawFrame(user_id=user_id, audio=PCM, sample_rate=16000, num_channels=1)


# ---- role mapping -------------------------------------------------------------------


def test_first_participant_is_the_caller():
    roles = CallRoles()
    roles.joined("c1", "caller")
    roles.set_caller("c1")
    assert roles.role("c1") == CALLER
    assert roles.caller_id == "c1"


def test_agent_is_the_participant_with_the_accepting_agents_name():
    roles = CallRoles()
    roles.set_caller("c1")
    roles.joined("c1", "Agent Ana")  # the caller's display name never makes them the agent
    roles.joined("x9", "Someone Else")
    roles.joined("a1", "Agent Ana")
    roles.agent_accepted("Agent Ana")
    assert roles.agent_id == "a1"
    assert roles.role("a1") == AGENT
    assert roles.role("x9") is None
    assert roles.role("c1") == CALLER


def test_agent_can_join_after_accept_message():
    roles = CallRoles()
    roles.set_caller("c1")
    roles.agent_accepted("Agent Ana")
    assert roles.agent_id is None
    roles.joined("a1", "Agent Ana")
    assert roles.agent_id == "a1"


def test_agent_leaving_clears_the_agent():
    roles = CallRoles()
    roles.set_caller("c1")
    roles.agent_accepted("Agent Ana")
    roles.joined("a1", "Agent Ana")
    roles.left("a1")
    assert roles.agent_id is None
    assert roles.role("a1") is None
    # A rejoin with the same name is the agent again.
    roles.joined("a2", "Agent Ana")
    assert roles.agent_id == "a2"


def test_names_are_not_roles_before_accept():
    roles = CallRoles()
    roles.joined("a1", "agent")
    assert roles.role("a1") is None
    assert not roles.listening


def test_accept_switches_to_listening():
    roles = CallRoles()
    assert not roles.listening
    roles.agent_accepted("Ana")
    assert roles.listening


# ---- message shaping ----------------------------------------------------------------


def test_transcript_message_shape():
    assert transcript_message(CALLER, "  Hi there. ", True, 12.5, "caller-3") == {
        "type": MESSAGE_TYPE,
        "role": "caller",
        "text": "Hi there.",
        "is_final": True,
        "ts": 12.5,
        "line_id": "caller-3",
    }
    assert MESSAGE_TYPE == "transcript"


def test_rtvi_server_message_envelope():
    data = transcript_message(AGENT, "ok", False, 1.0, "agent-1")
    assert rtvi_server_message(data) == {
        "label": "rtvi-ai",
        "type": "server-message",
        "data": data,
    }


def test_line_ids_interims_share_an_id_until_final():
    ids = LineIds(CALLER)
    assert ids.current() == "caller-1"
    assert ids.current() == "caller-1"
    assert ids.commit() == "caller-1"
    assert ids.current() == "caller-2"


# ---- audio routing ------------------------------------------------------------------


async def test_intake_route_passes_only_caller_audio_before_accept():
    roles = CallRoles()
    roles.set_caller("c1")
    down, _ = await run_test(
        AudioRoute(None, roles),
        frames_to_send=[_audio("c1"), _audio("x1"), TextFrame("keep")],
        expected_down_frames=[UserAudioRawFrame, TextFrame],
    )
    assert down[0].user_id == "c1"


async def test_intake_route_drops_all_audio_after_accept():
    roles = CallRoles()
    roles.set_caller("c1")
    roles.agent_accepted("Ana")
    await run_test(
        AudioRoute(None, roles),
        frames_to_send=[_audio("c1"), TextFrame("keep")],
        expected_down_frames=[TextFrame],
    )


async def test_listen_route_is_closed_before_accept():
    roles = CallRoles()
    roles.set_caller("c1")
    await run_test(
        AudioRoute(CALLER, roles),
        frames_to_send=[_audio("c1"), TextFrame("keep")],
        expected_down_frames=[TextFrame],
    )


async def test_listen_routes_pass_their_roles_audio_after_accept():
    roles = CallRoles()
    roles.set_caller("c1")
    roles.agent_accepted("Ana")
    roles.joined("a1", "Ana")
    frames = [_audio("c1"), _audio("a1"), _audio("x1")]
    down, _ = await run_test(
        AudioRoute(CALLER, roles), frames_to_send=frames, expected_down_frames=[UserAudioRawFrame]
    )
    assert down[0].user_id == "c1"
    down, _ = await run_test(
        AudioRoute(AGENT, roles), frames_to_send=frames, expected_down_frames=[UserAudioRawFrame]
    )
    assert down[0].user_id == "a1"


# ---- relay --------------------------------------------------------------------------


def _accepted_roles() -> CallRoles:
    roles = CallRoles()
    roles.set_caller("c1")
    roles.agent_accepted("Ana")
    roles.joined("a1", "Ana")
    return roles


async def test_relay_sends_interim_then_final_to_the_agent_only():
    roles = _accepted_roles()
    finals: list[dict] = []
    clock = iter([100.0, 101.0, 102.0])
    down, _ = await run_test(
        TranscriptRelay(CALLER, roles, on_final=finals.append, clock=lambda: next(clock)),
        frames_to_send=[
            InterimTranscriptionFrame("I already", "c1", "t0"),
            TranscriptionFrame("I already called.", "c1", "t1"),
            InterimTranscriptionFrame("nobody", "c1", "t2"),
        ],
        expected_down_frames=[DailyOutputTransportMessageUrgentFrame] * 3,
    )
    assert all(f.participant_id == "a1" for f in down)
    data = [f.message["data"] for f in down]
    assert [(d["line_id"], d["is_final"], d["text"]) for d in data] == [
        ("caller-1", False, "I already"),
        ("caller-1", True, "I already called."),
        ("caller-2", False, "nobody"),
    ]
    assert all(d["role"] == "caller" and d["type"] == "transcript" for d in data)
    assert [d["ts"] for d in data] == [100.0, 101.0, 102.0]
    assert finals == [data[1]]


async def test_relay_never_sends_without_an_agent_in_the_room():
    roles = CallRoles()
    roles.set_caller("c1")
    roles.agent_accepted("Ana")  # accepted, but the agent has not joined yet
    finals: list[dict] = []
    await run_test(
        TranscriptRelay(CALLER, roles, on_final=finals.append, clock=lambda: 1.0),
        frames_to_send=[TranscriptionFrame("hello?", "c1", "t")],
        expected_down_frames=[],
    )
    # Not delivered, but still kept for wrap up.
    assert [f["text"] for f in finals] == ["hello?"]


async def test_relay_message_never_targets_the_caller():
    roles = _accepted_roles()
    down, _ = await run_test(
        TranscriptRelay(AGENT, roles, clock=lambda: 1.0),
        frames_to_send=[TranscriptionFrame("Let me check.", "a1", "t")],
        expected_down_frames=[DailyOutputTransportMessageUrgentFrame],
    )
    assert down[0].participant_id == "a1" != roles.caller_id
    assert down[0].message["data"]["role"] == "agent"


async def test_relay_skips_blank_text_and_consumes_stt_frames():
    roles = _accepted_roles()
    await run_test(
        TranscriptRelay(CALLER, roles, clock=lambda: 1.0),
        frames_to_send=[TranscriptionFrame("   ", "c1", "t"), TextFrame("keep")],
        expected_down_frames=[TextFrame],
    )


# ---- persistence --------------------------------------------------------------------


async def test_recorder_persists_final_lines_in_order():
    store = MemoryCallStore()
    call_id = await store.create_call("s1")
    rec = TranscriptRecorder(store, lambda: call_id)
    rec.add(transcript_message(CALLER, "Hi.", True, 10.0, "caller-1"))
    rec.add(transcript_message(AGENT, "Hello.", True, 11.0, "agent-1"))
    await rec.close()
    assert store.calls[call_id].transcript == [
        ("caller", "Hi.", 10.0),
        ("agent", "Hello.", 11.0),
    ]


async def test_recorder_skips_without_a_call_row_and_survives_store_errors():
    class Broken(MemoryCallStore):
        async def add_transcript_line(self, *a):
            raise RuntimeError("db down")

    rec = TranscriptRecorder(Broken(), lambda: "c-1")
    rec.add(transcript_message(CALLER, "Hi.", True, 10.0, "caller-1"))
    await asyncio.wait_for(rec.close(), 2)  # logged, not raised

    rec = TranscriptRecorder(MemoryCallStore(), lambda: None)
    rec.add(transcript_message(CALLER, "Hi.", True, 10.0, "caller-1"))
    await asyncio.wait_for(rec.close(), 2)
