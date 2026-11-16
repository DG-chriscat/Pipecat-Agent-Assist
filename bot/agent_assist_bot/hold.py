"""Hold behaviour while queued (PRD QH-5), the accept line (QH-6), agent drop (QH-9),
and the agent's End (P3-5).

The server's queue sends Daily app messages into the call room (REST
send-app-message, so the sender is "api"):
    {"type": MESSAGE_TYPE, "event": "queued", "position": n}
    {"type": MESSAGE_TYPE, "event": "position", "position": n}
    {"type": MESSAGE_TYPE, "event": "agent_accepted", "agent_name": "..."}
    {"type": MESSAGE_TYPE, "event": "requeued", "position": n}
    {"type": MESSAGE_TYPE, "event": "call_ended"}

QH-5b: agent_accepted, requeued and call_ended also carry an "id" and arrive first on
the bot's own socket to the server (`server_channel.py`, `on_server_message`); the
Daily app message is the fallback copy. The first copy of an id wins, the second is
dropped, so a duplicate never speaks or toggles the music twice.

HoldController turns those into speech: a short update about every 60 s while
queued, then "Connecting you now." on accept, after which BotGate stops the LLM from
answering, so the bot goes quiet and the agent talks. If the agent drops off
(`requeued`), the gate reopens, the bot tells the caller they are back at the front of
the line, and hold updates resume until the next agent accepts. On `call_ended` (the
agent pressed End) it says goodbye. Messages from anyone other than the REST API
(e.g. a participant's browser) are ignored.

Hold music (`hold_music.py`) plays under all of that while the caller waits: `music`
is the output transport's mixer. It is turned on by `queued` and `requeued` (it waits
for the handoff or requeue line to finish, then fades in, and ducks about 12 dB under
each hold update) and off on `agent_accepted` before "Connecting you now." is queued,
on `call_ended`, and when the controller stops (the caller hung up). It never plays
during intake. HOLD_MUSIC=off builds no mixer and the updates alone are the hold audio.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping
from enum import StrEnum
from typing import Any, Protocol

from loguru import logger
from pipecat.frames.frames import Frame, LLMContextFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

MESSAGE_TYPE = "agent_assist.queue"
SERVER_SENDER = "api"
HOLD_INTERVAL_SECS = 60
HOLD_INTERVAL_ENV = "HOLD_UPDATE_SECS"  # optional override (live tests use a short one)
CONNECTING_LINE = "Connecting you now."
# QH-9: the agent dropped off; the caller is back at the front of the queue.
REQUEUED_LINE = (
    "Sorry, we lost the connection to your agent. You're back at the front of the line, "
    "and the next person will be right with you."
)
# P3-5: the agent pressed End while the caller was still on the line.
GOODBYE_LINE = "Thanks for calling. Goodbye."

QueueFrames = Callable[[list[Frame]], Awaitable[None]]


class HoldMusic(Protocol):
    """What the controller needs from the mixer (HoldMusicMixer)."""

    def enable(self) -> None: ...

    def disable(self) -> None: ...


def hold_interval_from_env(env: Mapping[str, str] | None = None) -> float:
    """Seconds between hold updates: HOLD_UPDATE_SECS when a positive number, else 60."""
    raw = (os.environ if env is None else env).get(HOLD_INTERVAL_ENV)
    try:
        secs = float(raw) if raw else HOLD_INTERVAL_SECS
    except ValueError:
        secs = HOLD_INTERVAL_SECS
    return float(secs if secs > 0 else HOLD_INTERVAL_SECS)


def place_line(position: int | None) -> str:
    """The caller's place in line, the {place} part of the hold line (P5-5 setting)."""
    if position == 1:
        return "You're next in line."
    if position:
        return f"You're number {position} in line."
    return "You're still in line."


def hold_line(position: int | None) -> str:
    return f"Thanks for waiting. {place_line(position)} A person will be with you shortly."


class HoldState(StrEnum):
    INTAKE = "intake"
    QUEUED = "queued"
    WITH_AGENT = "with_agent"
    ENDED = "ended"  # the agent ended the call (P3-5); the bot leaves


def _must_say(text: str) -> TTSSpeakFrame:
    """A line the caller must hear in full (QH-9 requeue, P3-5 goodbye): the caller
    talking over it does not cut it off or hand the turn to the LLM."""
    frame = TTSSpeakFrame(text)
    frame.interruptible = False
    return frame


class BotGate(FrameProcessor):
    """Sits between the user aggregator and the LLM. Closed after accept: caller turns
    no longer reach the LLM, so the bot stops replying. TTSSpeakFrames still pass.
    Reopened if the agent drops off and the caller is queued again (QH-9)."""

    def __init__(self):
        super().__init__()
        self.open = True
        self.dropped = 0

    def close(self) -> None:
        self.open = False

    def reopen(self) -> None:
        self.open = True

    def passes(self, frame: Frame, direction: FrameDirection) -> bool:
        return (
            self.open
            or direction is not FrameDirection.DOWNSTREAM
            or not isinstance(frame, LLMContextFrame)
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if self.passes(frame, direction):
            await self.push_frame(frame, direction)
        else:
            self.dropped += 1
            logger.info(f"[gate] agent on the call; caller turn not sent to LLM ({self.dropped})")


class HoldController:
    def __init__(
        self,
        queue_frames: QueueFrames,
        *,
        gate: BotGate | None = None,
        interval: float = HOLD_INTERVAL_SECS,
        music: HoldMusic | None = None,
        line: Callable[[int | None], str] = hold_line,
    ):
        self._queue_frames = queue_frames
        self.gate = gate or BotGate()
        self._interval = interval
        self.music = music
        # The hold update for a place in line (P5-5: the Settings page's hold message).
        self._line = line
        self.state = HoldState.INTAKE
        self.position: int | None = None
        self.agent_name: str | None = None
        self._task: asyncio.Task | None = None
        # QH-5b: ids of server messages already handled. The server sends
        # agent_accepted, requeued and call_ended twice (bot socket and Daily app
        # message) with the same id; the first copy wins, the second is dropped.
        self._seen_ids: set[Any] = set()

    async def on_server_message(self, message: Any) -> None:
        """A message from the server's bot socket (QH-5b): trusted like sender "api"."""
        await self.on_app_message(message, SERVER_SENDER)

    async def on_app_message(self, message: Any, sender: str) -> None:
        if sender != SERVER_SENDER or not isinstance(message, dict):
            return
        if message.get("type") != MESSAGE_TYPE:
            return
        msg_id = message.get("id")
        if msg_id is not None:
            if msg_id in self._seen_ids:
                logger.debug(f"[hold] duplicate {message.get('event')!r} (id {msg_id}) dropped")
                return
            self._seen_ids.add(msg_id)
        event = message.get("event")
        if self.state is HoldState.ENDED:
            return
        if event in ("queued", "position") and self.state is not HoldState.WITH_AGENT:
            self.position = message.get("position")
            if self.state is HoldState.INTAKE:
                self.state = HoldState.QUEUED
                logger.info(f"[hold] queued at position {self.position}")
                self._start_loop()
        elif event == "agent_accepted" and self.state is not HoldState.WITH_AGENT:
            self.state = HoldState.WITH_AGENT
            self.agent_name = message.get("agent_name")
            self.stop()  # music off before the connecting line is queued
            self.gate.close()
            logger.info(f"[hold] agent {message.get('agent_name')!r} accepted; going quiet")
            await self._queue_frames([TTSSpeakFrame(CONNECTING_LINE)])
        elif event == "requeued" and self.state is HoldState.WITH_AGENT:
            # QH-9: the agent dropped off. Speak again and wait for the next agent.
            self.state = HoldState.QUEUED
            self.agent_name = None
            self.position = message.get("position")
            self.gate.reopen()
            logger.info(f"[hold] agent dropped; requeued at position {self.position}")
            await self._queue_frames([_must_say(REQUEUED_LINE)])
            self._start_loop()
        elif event == "call_ended" and self.state is HoldState.WITH_AGENT:
            # The agent pressed End (P3-5): say goodbye; the bot then leaves.
            self.state = HoldState.ENDED
            self.stop()
            logger.info("[hold] the agent ended the call; saying goodbye")
            await self._queue_frames([_must_say(GOODBYE_LINE)])

    def _start_loop(self) -> None:
        self._cancel_updates()
        self._task = asyncio.create_task(self._hold_loop())
        if self.music is not None:
            self.music.enable()

    async def _hold_loop(self) -> None:
        while self.state is HoldState.QUEUED:
            await asyncio.sleep(self._interval)
            if self.state is HoldState.QUEUED:
                await self._queue_frames([TTSSpeakFrame(self._line(self.position))])

    def _cancel_updates(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def stop(self) -> None:
        """Stop hold updates and hold music."""
        self._cancel_updates()
        if self.music is not None:
            self.music.disable()
