"""Hold behaviour while queued (PRD QH-5), the accept line (QH-6), agent drop (QH-9),
and the agent's End (P3-5).

The server's queue sends Daily app messages into the call room (REST
send-app-message, so the sender is "api"):
    {"type": MESSAGE_TYPE, "event": "queued", "position": n}
    {"type": MESSAGE_TYPE, "event": "position", "position": n}
    {"type": MESSAGE_TYPE, "event": "agent_accepted", "agent_name": "..."}
    {"type": MESSAGE_TYPE, "event": "requeued", "position": n}
    {"type": MESSAGE_TYPE, "event": "call_ended"}

HoldController turns those into speech: a short update about every 60 s while
queued, then "Connecting you now." on accept, after which BotGate stops the LLM from
answering, so the bot goes quiet and the agent talks. If the agent drops off
(`requeued`), the gate reopens, the bot tells the caller they are back at the front of
the line, and hold updates resume until the next agent accepts. On `call_ended` (the
agent pressed End) it says goodbye. Messages from anyone other than the REST API
(e.g. a participant's browser) are ignored.

Hold music is not played; the periodic update is the hold audio for now.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from loguru import logger
from pipecat.frames.frames import Frame, LLMContextFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

MESSAGE_TYPE = "agent_assist.queue"
SERVER_SENDER = "api"
HOLD_INTERVAL_SECS = 60
CONNECTING_LINE = "Connecting you now."
# QH-9: the agent dropped off; the caller is back at the front of the queue.
REQUEUED_LINE = (
    "Sorry, we lost the connection to your agent. You're back at the front of the line, "
    "and the next person will be right with you."
)
# P3-5: the agent pressed End while the caller was still on the line.
GOODBYE_LINE = "Thanks for calling. Goodbye."

QueueFrames = Callable[[list[Frame]], Awaitable[None]]


def hold_line(position: int | None) -> str:
    if position == 1:
        where = "You're next in line."
    elif position:
        where = f"You're number {position} in line."
    else:
        where = "You're still in line."
    return f"Thanks for waiting. {where} A person will be with you shortly."


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
    ):
        self._queue_frames = queue_frames
        self.gate = gate or BotGate()
        self._interval = interval
        self.state = HoldState.INTAKE
        self.position: int | None = None
        self.agent_name: str | None = None
        self._task: asyncio.Task | None = None

    async def on_app_message(self, message: Any, sender: str) -> None:
        if sender != SERVER_SENDER or not isinstance(message, dict):
            return
        if message.get("type") != MESSAGE_TYPE:
            return
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
            self.stop()
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
        self.stop()
        self._task = asyncio.create_task(self._hold_loop())

    async def _hold_loop(self) -> None:
        while self.state is HoldState.QUEUED:
            await asyncio.sleep(self._interval)
            if self.state is HoldState.QUEUED:
                await self._queue_frames([TTSSpeakFrame(hold_line(self.position))])

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
