"""Slow read backs on Flux TTS (P5-8, PRD AP-22).

With slow read backs on (Settings, Flux only), the bot says read backs slower: the
identity read back, the final summary read back, and any line with a member ID, date
of birth, or claim number. Just before such a line the speed goes to readback_speed and
right after it back to tts_speed, each with a TTSUpdateSettingsFrame. Flux applies a
speed change on the open connection with a Configure message, so there is no reconnect
and the voice keeps its cross turn acoustic state (pipecat DeepgramFluxTTSBase).

- `ReadbackPacer` sits between the LLM and the TTS. A fixed line (TTSSpeakFrame: the
  identity read back, say_to_caller) gets the slow frame before it and the restore
  frame after it. In a streamed LLM reply, the speed drops at the first token where the
  reply so far reads as a read back (a member ID, date of birth, or claim number is
  coming) and comes back after the reply ends. The TTS holds frames while a line's
  audio plays, so the restore applies after the slow line has been spoken.
- Both speed frames are uninterruptible, so a barge-in cannot leave the call slow.
- `FluxTTSService` is the call's Flux TTS. It logs every speed change on the timing
  log: `[timing] event=tts_speed t=... speed=0.9 reason=readback session=...`.

Flux rejects inline pronunciation markup when speed is not 1.0 (DATA-0002); the bot
uses none, so speaking at 0.9 or 1.1 is safe.
"""

from __future__ import annotations

import re

from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSSpeakFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.deepgram.flux.tts import DeepgramFluxTTSService

from agent_assist_bot.timing import log_timing

READBACK_KEY = "readback"
REASON_KEY = "tts_speed_reason"

_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
_DIGIT = r"(?:\d|zero|oh|one|two|three|four|five|six|seven|eight|nine)"
_SEP = r"[\s,]+"
# Values only: a line that asks for a detail ("What's your date of birth?") is not a
# read back. Seen live, keying on the words slowed those questions down.
_READBACK = re.compile(
    r"|".join(
        (
            # A member ID or claim number as written: EVM100231, CLM-900001.
            r"\b[a-z]{2,4}-?\d{5,}\b",
            # Spelled out: letters, then digits one at a time ("E V M, one zero zero").
            rf"\b[a-z](?:{_SEP}[a-z]){{1,3}}\b[\s,-]+(?:dash{_SEP})?{_DIGIT}(?:{_SEP}{_DIGIT}){{2,}}",
            # Six or more digits said one at a time.
            rf"\b{_DIGIT}(?:{_SEP}{_DIGIT}){{5,}}\b",
            # A date of birth: 03/12/1984, 1984-03-12, March 12, 1984, 12th of March, 1984.
            r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",
            r"\b\d{4}-\d{2}-\d{2}\b",
            rf"\b({_MONTHS})\s+\d{{1,2}}(st|nd|rd|th)?,?\s+\d{{4}}\b",
            rf"\b\d{{1,2}}(st|nd|rd|th)?\s+of\s+({_MONTHS}),?\s+\d{{4}}\b",
        )
    ),
    re.IGNORECASE,
)


def is_readback_line(text: str) -> bool:
    """True when the line reads back a member ID, date of birth, or claim number."""
    return bool(_READBACK.search(text or ""))


def mark_readback(frame: Frame) -> Frame:
    """Mark a line as a read back whatever its words (the final summary read back)."""
    frame.metadata[READBACK_KEY] = True
    return frame


def is_readback_frame(frame: Frame) -> bool:
    return isinstance(frame, TTSSpeakFrame) and (
        bool(frame.metadata.get(READBACK_KEY)) or is_readback_line(frame.text)
    )


def speed_frame(speed: float, reason: str) -> TTSUpdateSettingsFrame:
    frame = TTSUpdateSettingsFrame(delta=DeepgramFluxTTSService.Settings(speed=speed))
    frame.metadata[REASON_KEY] = reason
    # A barge-in must not drop a speed change (the restore above all).
    frame.interruptible = False
    return frame


class ReadbackPacer(FrameProcessor):
    """Slows read back lines to readback_speed and restores tts_speed after (module doc)."""

    def __init__(self, *, tts_speed: float, readback_speed: float, **kwargs):
        super().__init__(**kwargs)
        self.tts_speed = tts_speed
        self.readback_speed = readback_speed
        self._reply = ""
        self._slowed = False

    def frames_for(self, frame: TTSSpeakFrame) -> list[Frame]:
        """A fixed line: the line, slowed and restored when it is a read back."""
        if not is_readback_frame(frame):
            return [frame]
        return [
            speed_frame(self.readback_speed, "readback"),
            frame,
            speed_frame(self.tts_speed, "restore"),
        ]

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, TTSSpeakFrame) and not self._slowed:
            for f in self.frames_for(frame):
                await self.push_frame(f, direction)
            return
        if isinstance(frame, LLMFullResponseStartFrame):
            self._reply = ""
        elif isinstance(frame, LLMTextFrame) and not frame.skip_tts and not self._slowed:
            self._reply += frame.text
            if is_readback_line(self._reply):
                self._slowed = True
                await self.push_frame(speed_frame(self.readback_speed, "readback"), direction)
        elif isinstance(frame, LLMFullResponseEndFrame | InterruptionFrame):
            await self.push_frame(frame, direction)
            self._reply = ""
            if self._slowed:
                self._slowed = False
                await self.push_frame(speed_frame(self.tts_speed, "restore"), direction)
            return
        await self.push_frame(frame, direction)


class FluxTTSService(DeepgramFluxTTSService):
    """The call's Flux TTS: logs each speed change with the timing log."""

    def __init__(self, *, session_id: str = "", **kwargs):
        super().__init__(**kwargs)
        self._session_id = session_id
        self._speed_reason = "settings"

    async def _handle_update_settings(self, frame: TTSUpdateSettingsFrame, direction):
        self._speed_reason = frame.metadata.get(REASON_KEY, "settings")
        await super()._handle_update_settings(frame, direction)

    async def _update_settings(self, delta):
        changed = await super()._update_settings(delta)
        if "speed" in changed:
            log_timing(
                "tts_speed",
                speed=self._settings.speed,
                reason=self._speed_reason,
                session=self._session_id,
            )
        return changed
