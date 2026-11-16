"""Spike S2: a listen only bot that transcribes each Daily participant on their own track.

No TTS, no LLM. The Daily transport delivers every remote participant's microphone as a
separate `UserAudioRawFrame` stream (`audio_in_user_tracks=True`, Pipecat's default).
A `ParallelPipeline` has one branch per role ("caller", "agent"); each branch keeps only
its role's audio, runs its own Silero VAD and its own Deepgram live STT stream, and
emits labeled transcript lines. Roles come from the participant's Daily user_name.
The STT model is any assist model (P5-7, `stt.py`): Nova-3 general (default), Nova-3
Medical, or Flux (no VAD; Flux detects turns itself).

    transport.input() -> ParallelPipeline(
        [RoleAudioFilter(caller), VAD, Deepgram STT, TranscriptEmitter(caller)],
        [RoleAudioFilter(agent),  VAD, Deepgram STT, TranscriptEmitter(agent)],
    )
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserAudioRawFrame,
)
from pipecat.pipeline.parallel_pipeline import ParallelPipeline
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.daily.transport import DailyParams, DailyTransport
from pipecat.workers.runner import WorkerRunner

from agent_assist_bot.stt import DEFAULT_ASSIST_MODEL, build_listen_stt, listen_uses_vad

LISTENER_NAME = "Assist Listener"
ROLES = ("caller", "agent")
LINE_PREFIX = "S2LINE "


def role_for_user_name(user_name: str | None) -> str | None:
    """Spike rule: the Daily user_name is the role. Anything else is not transcribed."""
    name = (user_name or "").strip().lower()
    return name if name in ROLES else None


class ParticipantRoles:
    """participant_id -> role for everyone currently in the room with a known role."""

    def __init__(self) -> None:
        self._roles: dict[str, str] = {}

    def add(self, participant_id: str, user_name: str | None) -> str | None:
        role = role_for_user_name(user_name)
        if role:
            self._roles[participant_id] = role
        return role

    def remove(self, participant_id: str) -> None:
        self._roles.pop(participant_id, None)

    def role(self, participant_id: str) -> str | None:
        return self._roles.get(participant_id)

    def active(self) -> dict[str, str]:
        return dict(self._roles)


@dataclass(frozen=True)
class TranscriptLine:
    speaker_role: str
    participant_id: str
    text: str
    is_final: bool
    ts: float

    def to_json(self) -> str:
        return json.dumps(asdict(self))


class RoleAudioFilter(FrameProcessor):
    """Drops participant audio that does not belong to `role`. Other frames pass."""

    def __init__(self, role: str, roles: ParticipantRoles):
        super().__init__(name=f"RoleAudioFilter[{role}]")
        self._role = role
        self._roles = roles

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, UserAudioRawFrame) and self._roles.role(frame.user_id) != self._role:
            return
        await self.push_frame(frame, direction)


class TranscriptEmitter(FrameProcessor):
    """Turns this branch's STT frames into `TranscriptLine`s. Frames pass through."""

    def __init__(
        self,
        role: str,
        roles: ParticipantRoles,
        on_line: Callable[[TranscriptLine], None],
        *,
        clock: Callable[[], float] = time.time,
    ):
        super().__init__(name=f"TranscriptEmitter[{role}]")
        self._role = role
        self._roles = roles
        self._on_line = on_line
        self._clock = clock

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        is_text = isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame))
        if is_text and frame.text.strip():
            role = self._roles.role(frame.user_id) or self._role
            self._on_line(
                TranscriptLine(
                    speaker_role=role,
                    participant_id=frame.user_id,
                    text=frame.text.strip(),
                    is_final=isinstance(frame, TranscriptionFrame),
                    ts=self._clock(),
                )
            )
        await self.push_frame(frame, direction)


def print_line(line: TranscriptLine) -> None:
    """Default sink: one JSON line per transcript line on stdout (final lines only)."""
    if line.is_final:
        print(LINE_PREFIX + line.to_json(), flush=True)


def _branch(
    role: str, roles: ParticipantRoles, on_line, model: str = DEFAULT_ASSIST_MODEL
) -> list[FrameProcessor]:
    vad = [VADProcessor(vad_analyzer=SileroVADAnalyzer())] if listen_uses_vad(model) else []
    return [
        RoleAudioFilter(role, roles),
        *vad,
        build_listen_stt(os.environ["DEEPGRAM_API_KEY"], model),
        TranscriptEmitter(role, roles, on_line),
    ]


def build_listen_worker(
    room_url: str,
    token: str | None = None,
    *,
    on_line: Callable[[TranscriptLine], None] = print_line,
    model: str = DEFAULT_ASSIST_MODEL,
) -> tuple[PipelineWorker, DailyTransport, ParticipantRoles]:
    """`model` (P5-7): the assist STT model, as in the bot's listen branches (stt.py)."""
    transport = DailyTransport(
        room_url,
        token,
        LISTENER_NAME,
        DailyParams(
            audio_in_enabled=True,
            audio_in_user_tracks=True,
            audio_out_enabled=False,
            camera_out_enabled=False,
        ),
    )
    roles = ParticipantRoles()
    pipeline = Pipeline(
        [
            transport.input(),
            ParallelPipeline(*(_branch(role, roles, on_line, model) for role in ROLES)),
        ]
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True),
        idle_timeout_secs=None,
    )

    @transport.event_handler("on_participant_joined")
    async def on_participant_joined(transport, participant):
        name = participant.get("info", {}).get("userName", "")
        role = roles.add(participant["id"], name)
        logger.info(f"[listen] joined {participant['id']} name={name!r} role={role}")

    @transport.event_handler("on_participant_left")
    async def on_participant_left(transport, participant, reason):
        roles.remove(participant["id"])
        logger.info(f"[listen] left {participant['id']} ({reason})")
        if not roles.active():
            logger.info("[listen] no labeled participants left; ending")
            await worker.cancel()

    return worker, transport, roles


async def run_listen_bot(
    room_url: str, token: str | None = None, *, model: str = DEFAULT_ASSIST_MODEL
) -> None:
    worker, _, _ = build_listen_worker(room_url, token, model=model)
    runner = WorkerRunner(handle_sigint=True)
    await runner.add_workers(worker)
    await runner.run()
