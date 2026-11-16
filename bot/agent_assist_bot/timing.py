"""Call timeline log lines (PRD IN-1: greeting within 5 s of the bot joining).

Each line looks like `[timing] event=bot_joined t=1794779110.758 session=abc`, with
`t` in epoch seconds so bot and server lines line up. The dial-in live test parses
these to measure greeting latency.
"""

from __future__ import annotations

import re
import time

from loguru import logger
from pipecat.frames.frames import BotStartedSpeakingFrame, Frame, TTSAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

TIMING_RE = re.compile(r"\[timing\] event=(?P<event>\w+) t=(?P<t>\d+\.\d+)(?P<rest>.*)")


def format_timing(event: str, t: float, **fields: object) -> str:
    extra = "".join(f" {k}={v}" for k, v in fields.items() if v not in (None, ""))
    return f"[timing] event={event} t={t:.3f}{extra}"


def log_timing(event: str, **fields: object) -> float:
    t = time.time()
    logger.info(format_timing(event, t, **fields))
    return t


def parse_timing(line: str) -> tuple[str, float, dict[str, str]] | None:
    m = TIMING_RE.search(line)
    if not m:
        return None
    fields = dict(kv.split("=", 1) for kv in m["rest"].split() if "=" in kv)
    return m["event"], float(m["t"]), fields


class FirstAudioTimer(FrameProcessor):
    """Sits just before transport.output(). Logs the first TTS audio frame handed to
    the output (`greeting_tts_audio`) and the output's first BotStartedSpeakingFrame,
    which it pushes upstream once that audio is written to Daily
    (`greeting_first_audio`). Each is logged once per call."""

    def __init__(self, session_id: str = ""):
        super().__init__()
        self._session_id = session_id
        self._seen: set[str] = set()

    def _once(self, event: str) -> None:
        if event not in self._seen:
            self._seen.add(event)
            log_timing(event, session=self._session_id)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSAudioRawFrame) and direction == FrameDirection.DOWNSTREAM:
            self._once("greeting_tts_audio")
        elif isinstance(frame, BotStartedSpeakingFrame) and direction == FrameDirection.UPSTREAM:
            self._once("greeting_first_audio")
        await self.push_frame(frame, direction)
