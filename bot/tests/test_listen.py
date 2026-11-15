"""Unit tests for the S2 listen only bot (no network)."""

from __future__ import annotations

import json

import pytest
from agent_assist_bot.listen import (
    ROLES,
    ParticipantRoles,
    RoleAudioFilter,
    TranscriptEmitter,
    TranscriptLine,
    build_listen_worker,
    role_for_user_name,
)
from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    TextFrame,
    TranscriptionFrame,
    UserAudioRawFrame,
)
from pipecat.pipeline.worker import PipelineWorker
from pipecat.tests.utils import run_test
from pipecat.transports.daily.transport import DailyTransport

PCM = b"\x00\x00" * 320


def _audio(user_id: str) -> UserAudioRawFrame:
    return UserAudioRawFrame(user_id=user_id, audio=PCM, sample_rate=16000, num_channels=1)


@pytest.mark.parametrize(
    ("name", "role"),
    [
        ("caller", "caller"),
        ("Agent", "agent"),
        (" agent ", "agent"),
        ("Vitals Assistant", None),
        ("", None),
        (None, None),
    ],
)
def test_role_for_user_name(name, role):
    assert role_for_user_name(name) == role


def test_roles_are_caller_and_agent():
    assert ROLES == ("caller", "agent")


def test_participant_roles_tracks_join_and_leave():
    roles = ParticipantRoles()
    assert roles.add("p1", "caller") == "caller"
    assert roles.add("p2", "agent") == "agent"
    assert roles.add("p3", "Supervisor") is None

    assert roles.role("p1") == "caller"
    assert roles.role("p2") == "agent"
    assert roles.role("p3") is None
    assert roles.role("nobody") is None

    roles.remove("p1")
    assert roles.role("p1") is None
    assert roles.active() == {"p2": "agent"}


async def test_role_audio_filter_only_passes_its_roles_audio():
    roles = ParticipantRoles()
    roles.add("c1", "caller")
    roles.add("a1", "agent")
    caller_audio, agent_audio, stranger_audio = _audio("c1"), _audio("a1"), _audio("x1")

    down, _ = await run_test(
        RoleAudioFilter("caller", roles),
        frames_to_send=[caller_audio, agent_audio, stranger_audio, TextFrame("keep")],
        expected_down_frames=[UserAudioRawFrame, TextFrame],
    )
    assert down[0].user_id == "c1"


async def test_transcript_emitter_labels_lines_by_participant_role():
    roles = ParticipantRoles()
    roles.add("a1", "agent")
    lines: list[TranscriptLine] = []
    clock = iter([100.0, 101.5])

    await run_test(
        TranscriptEmitter("agent", roles, lines.append, clock=lambda: next(clock)),
        frames_to_send=[
            InterimTranscriptionFrame("let me", "a1", "t0"),
            TranscriptionFrame("Let me check that.", "a1", "t1"),
        ],
        expected_down_frames=[InterimTranscriptionFrame, TranscriptionFrame],
    )

    assert lines == [
        TranscriptLine("agent", "a1", "let me", False, 100.0),
        TranscriptLine("agent", "a1", "Let me check that.", True, 101.5),
    ]


async def test_transcript_emitter_skips_blank_text():
    roles = ParticipantRoles()
    lines: list[TranscriptLine] = []
    await run_test(
        TranscriptEmitter("caller", roles, lines.append),
        frames_to_send=[TranscriptionFrame("  ", "c1", "t")],
    )
    assert lines == []


async def test_transcript_emitter_falls_back_to_branch_role():
    # A line for a participant that already left still gets its branch's role.
    lines: list[TranscriptLine] = []
    await run_test(
        TranscriptEmitter("caller", ParticipantRoles(), lines.append, clock=lambda: 5.0),
        frames_to_send=[TranscriptionFrame("bye", "gone", "t")],
    )
    assert lines == [TranscriptLine("caller", "gone", "bye", True, 5.0)]


def test_transcript_line_json_has_card_fields():
    line = TranscriptLine("caller", "c1", "hello", True, 12.5)
    assert json.loads(line.to_json()) == {
        "speaker_role": "caller",
        "participant_id": "c1",
        "text": "hello",
        "is_final": True,
        "ts": 12.5,
    }


def test_build_listen_worker_wires_without_network(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")

    worker, transport, roles = build_listen_worker(
        "https://dg-chriscat.daily.co/unit-test", on_line=lambda line: None
    )

    assert isinstance(worker, PipelineWorker)
    assert isinstance(transport, DailyTransport)
    assert isinstance(roles, ParticipantRoles)
    # Listen only: no audio out, per participant tracks in.
    assert transport._params.audio_out_enabled is False
    assert transport._params.audio_in_enabled is True
    assert transport._params.audio_in_user_tracks is True
