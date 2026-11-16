"""Live transcript for the agent portal after accept (P3-4, S2 approach).

One bot per call. Until an agent accepts, the caller's audio feeds intake (STT -> LLM
-> TTS). On accept the bot goes listen only: intake hears nothing more, and two
listen branches, one per role, each with its own Silero VAD and Deepgram stream,
transcribe the caller and the agent on their own Daily tracks
(`audio_in_user_tracks`, docs/spikes/S2.md). No diarization: a line's role is the
track it was heard on.

    transport.input() -> ParallelPipeline(
        [AudioRoute(intake), STT, ...],                                 # before accept
        [AudioRoute(caller), VAD, Deepgram, TranscriptRelay(caller)],   # after accept
        [AudioRoute(agent),  VAD, Deepgram, TranscriptRelay(agent)],    # after accept
    ) -> user aggregator -> gate -> LLM -> TTS -> transport.output()

Roles (CallRoles) come from the call flow, not from display names anyone can set:
- caller: the first participant (the dial-in SIP leg or the browser caller);
- agent: the participant whose Daily user_name is the accepting agent's name, as
  given by the server's `agent_accepted` app message (sender "api", REST only). The
  server mints the agent's token with that same user_name.

Delivery: each line is an RTVI server message sent as a Daily app message to the
agent's participant id only, never broadcast, so the caller never receives it:
    {"label": "rtvi-ai", "type": "server-message",
     "data": {"type": "transcript", "role", "text", "is_final", "ts", "line_id"}}
Interim lines reuse the line_id of the line in progress; the final with that id
commits it. Final lines are also saved to `transcript_lines` for wrap up. No audio is
stored.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserAudioRawFrame,
    UserSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi.models import MESSAGE_LABEL
from pipecat.transports.daily.transport import DailyOutputTransportMessageUrgentFrame

CALLER = "caller"
AGENT = "agent"
ROLES = (CALLER, AGENT)
MESSAGE_TYPE = "transcript"
RTVI_SERVER_MESSAGE = "server-message"

OnFinal = Callable[[dict[str, Any]], None]


class CallRoles:
    """Who is the caller and who is the agent in this call's room."""

    def __init__(self) -> None:
        self.caller_id: str | None = None
        self.agent_id: str | None = None
        self.agent_name: str | None = None
        self._names: dict[str, str] = {}

    @property
    def listening(self) -> bool:
        """True once an agent accepted: intake stops, the listen branches run."""
        return self.agent_name is not None

    def set_caller(self, participant_id: str) -> None:
        self.caller_id = participant_id

    def joined(self, participant_id: str, user_name: str | None) -> None:
        self._names[participant_id] = (user_name or "").strip()
        self._match_agent(participant_id)

    def left(self, participant_id: str) -> None:
        self._names.pop(participant_id, None)
        if participant_id == self.agent_id:
            self.agent_id = None

    def agent_accepted(self, agent_name: str | None) -> None:
        self.agent_name = (agent_name or "").strip()
        for pid in self._names:
            self._match_agent(pid)

    def _match_agent(self, participant_id: str) -> None:
        if (
            self.agent_name
            and self.agent_id is None
            and participant_id != self.caller_id
            and self._names.get(participant_id) == self.agent_name
        ):
            self.agent_id = participant_id
            logger.info(f"[transcript] agent {self.agent_name!r} is {participant_id}")

    def role(self, participant_id: str | None) -> str | None:
        if participant_id is None:
            return None
        if participant_id == self.caller_id:
            return CALLER
        if participant_id == self.agent_id:
            return AGENT
        return None


def transcript_message(
    role: str, text: str, is_final: bool, ts: float, line_id: str
) -> dict[str, Any]:
    return {
        "type": MESSAGE_TYPE,
        "role": role,
        "text": text.strip(),
        "is_final": is_final,
        "ts": ts,
        "line_id": line_id,
    }


def rtvi_server_message(data: dict[str, Any]) -> dict[str, Any]:
    """RTVI envelope the Pipecat client SDK hands to onServerMessage."""
    return {"label": MESSAGE_LABEL, "type": RTVI_SERVER_MESSAGE, "data": data}


class LineIds:
    """line_id for one role: interims share the open line's id until its final."""

    def __init__(self, role: str):
        self._role = role
        self._n = 1

    def current(self) -> str:
        return f"{self._role}-{self._n}"

    def commit(self) -> str:
        line_id = self.current()
        self._n += 1
        return line_id


class AudioRoute(FrameProcessor):
    """Lets through only the participant audio one branch should hear. Other frames pass.

    role None is the intake branch: the caller's audio until an agent accepts, then
    nothing. role caller/agent is a listen branch: that role's audio, after accept only.
    """

    def __init__(self, role: str | None, roles: CallRoles):
        super().__init__(name=f"AudioRoute[{role or 'intake'}]")
        self._role = role
        self._roles = roles

    def wants(self, participant_id: str) -> bool:
        if self._role is None:
            return not self._roles.listening and participant_id == self._roles.caller_id
        return self._roles.listening and self._roles.role(participant_id) == self._role

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, UserAudioRawFrame) and not self.wants(frame.user_id):
            return
        await self.push_frame(frame, direction)


# Frames a listen branch must not let out: its audio and VAD would otherwise reach the
# intake user aggregator downstream and start (or interrupt) bot turns.
_LISTEN_ONLY = (
    UserAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
    UserSpeakingFrame,
)


class TranscriptRelay(FrameProcessor):
    """End of a listen branch: STT frames -> transcript messages to the agent only."""

    def __init__(
        self,
        role: str,
        roles: CallRoles,
        *,
        on_final: OnFinal | None = None,
        clock: Callable[[], float] = time.time,
    ):
        super().__init__(name=f"TranscriptRelay[{role}]")
        self._role = role
        self._roles = roles
        self._on_final = on_final
        self._clock = clock
        self._ids = LineIds(role)
        self.sent = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
            await self._relay(frame)
            return
        if direction is FrameDirection.DOWNSTREAM and isinstance(frame, _LISTEN_ONLY):
            return
        await self.push_frame(frame, direction)

    async def _relay(self, frame: TranscriptionFrame | InterimTranscriptionFrame) -> None:
        if not frame.text.strip():
            return
        is_final = isinstance(frame, TranscriptionFrame)
        line_id = self._ids.commit() if is_final else self._ids.current()
        data = transcript_message(self._role, frame.text, is_final, self._clock(), line_id)
        if is_final:
            logger.info(f"[transcript] {self._role}: {data['text']!r}")
            if self._on_final is not None:
                self._on_final(data)
        agent_id = self._roles.agent_id
        if agent_id is None:
            return  # nobody to show it to; never fall back to a broadcast
        await self.push_frame(
            DailyOutputTransportMessageUrgentFrame(
                message=rtvi_server_message(data), participant_id=agent_id
            )
        )
        self.sent += 1


class TranscriptRecorder:
    """Saves final lines to the calls record in order, off the audio path."""

    def __init__(self, store, call_id: Callable[[], str | None]):
        self._store = store
        self._call_id = call_id
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def add(self, data: dict[str, Any]) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run())
        self._queue.put_nowait(data)

    async def _run(self) -> None:
        while (data := await self._queue.get()) is not None:
            call_id = self._call_id()
            if call_id is None:
                logger.warning("[transcript] no calls row yet; line not saved")
                continue
            try:
                await self._store.add_transcript_line(
                    call_id, data["role"], data["text"], data["ts"]
                )
            except Exception as e:  # noqa: BLE001 - the live call goes on
                logger.error(f"[transcript] could not save a line: {type(e).__name__}")

    async def close(self, timeout: float = 10) -> None:
        if self._task is None:
            return
        self._queue.put_nowait(None)
        try:
            await asyncio.wait_for(self._task, timeout)
        except TimeoutError:
            logger.error("[transcript] saving lines timed out")
        self._task = None
