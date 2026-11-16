"""Caller tone (P4-5, PRD IN-10, AP-14): Pipecat Tone readings, the mood trend, the tip rule.

The reader is Pipecat's `PipecatToneReader` (expressive conversation, alpha; the PRD's
"RCS perception"). It goes right after the caller's STT, reads each final caller line
from its **words** (not the audio), with the bot's previous reply as context, and sets
`TranscriptionFrame.perception`: the two likeliest emotions with their confidences,
and the chance the line is sarcastic. Model `pipecat-ai/tone-models` revision
`reader-rc2`, fp16 weights, one CPU thread per reader (one bot process per call).

Only the caller is read: the intake branch (before accept) and the caller listen branch
(after accept). The agent's branch has no reader.

One reading per caller line (`read_tone`) maps the reader's emotion onto a five step
mood scale, calmest first:

    0 Pleased     happy, amused (confident)
    1 Calm        neutral, surprised; anything the reader is unsure of
    2 Uneasy      a negative emotion under 0.5 confidence, or a sarcastic line
    3 Worried     sad, afraid
    4 Frustrated  angry, disgusted, contemptuous

`MoodTrend` keeps every reading of the call. Its label is the current mood plus the
direction against the two readings before it ("Worried, easing"). The tip rule
(AP-14): when a reading is two or more steps above the calmest reading since the
last tip (Calm when the call starts), the agent gets a tone tip card. Intake readings
count toward the baseline but never tip, since no agent is on the call yet.

Switch (PRD risk table, alpha API): `TONE_READER=0` turns the reader off; the
transcript, assist, and portal run without tone.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from typing import Any

from loguru import logger
from pipecat.expressive.perception import PerceptionSignal
from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

TONE_ENV = "TONE_READER"
TONE_REVISION = "reader-rc2"
TONE_PRECISION = "fp16"
TONE_THREADS = 1
SOURCE_LABEL = "Pipecat Tone reader, words only"

MOODS = ("Pleased", "Calm", "Uneasy", "Worried", "Frustrated")
CALM = 1
UNEASY = 2
TOP = len(MOODS) - 1
DROP_STEPS = 2
SURE = 0.5
SARCASTIC = 0.5

_STEP = {
    "happy": 0,
    "amused": 0,
    "neutral": 1,
    "surprised": 1,
    "sad": 3,
    "afraid": 3,
    "angry": 4,
    "disgusted": 4,
    "contemptuous": 4,
}

_TIPS = {
    2: (
        "Check in",
        "The caller's words turned uneasy. Ask if anything is unclear before going on.",
    ),
    3: (
        "Reassure first",
        "The caller sounds worried. Say what happens next and when, then the details.",
    ),
    4: (
        "Acknowledge, then act",
        "The caller sounds frustrated. Name the problem in their words, then give one "
        "concrete next step.",
    ),
}


def mood_step(emotion: str, confidence: float, sarcasm: float) -> int:
    """Mood step 0 (Pleased) to 4 (Frustrated) for one reading."""
    step = _STEP.get(emotion, CALM)
    if confidence < SURE:
        step = UNEASY if step > CALM else CALM
    if sarcasm >= SARCASTIC:
        step = max(step, UNEASY)
    return step


def read_tone(signals: list[PerceptionSignal] | None) -> dict[str, Any] | None:
    """One caller line's reading from the reader's perception, or None without one."""
    emotion = next((s for s in signals or [] if s.name == "emotion" and s.labels), None)
    if emotion is None:
        return None
    sarcasm = next((s.value for s in signals or [] if s.name == "sarcasm"), None) or 0.0
    label, confidence = emotion.labels[0]
    step = mood_step(label, confidence, sarcasm)
    return {
        "emotion": label,
        "confidence": round(float(confidence), 4),
        "labels": [[name, round(float(c), 4)] for name, c in emotion.labels],
        "sarcasm": round(float(sarcasm), 4),
        "step": step,
        "mood": MOODS[step],
        "score": step / TOP,
    }


def tone_tip(step: int) -> dict[str, str]:
    """Headline and why for a tone tip at this mood step (Uneasy and up)."""
    headline, why = _TIPS[max(UNEASY, min(TOP, step))]
    return {"headline": headline, "why": why}


_READING_KEYS = ("line_id", "ts", "emotion", "confidence", "sarcasm", "step", "mood", "score")


class MoodTrend:
    """Every caller reading of the call, its label, and the two step drop rule."""

    def __init__(self) -> None:
        self.readings: list[dict[str, Any]] = []
        self._calmest = CALM

    def add(self, reading: Mapping[str, Any], *, can_tip: bool = True) -> bool:
        """Add one reading. True when it is a two step drop that should tip the agent."""
        step = int(reading["step"])
        self.readings.append({k: reading.get(k) for k in _READING_KEYS})
        drop = step - self._calmest >= DROP_STEPS
        if drop and can_tip:
            self._calmest = step  # re-arm: the next tip needs a new drop from here
            return True
        self._calmest = min(self._calmest, step)
        return False

    def current(self) -> dict[str, Any] | None:
        return self.readings[-1] if self.readings else None

    def label(self) -> str | None:
        cur = self.current()
        if cur is None:
            return None
        if len(self.readings) == 1:
            return cur["mood"]
        before = [r["step"] for r in self.readings[-3:-1]]
        delta = cur["step"] - sum(before) / len(before)
        way = "worsening" if delta >= 0.5 else "easing" if delta <= -0.5 else "steady"
        return f"{cur['mood']}, {way}"

    def tone(self) -> str | None:
        """Chip tone: ok (Pleased, Calm), warn (Uneasy, Worried), bad (Frustrated)."""
        cur = self.current()
        if cur is None:
            return None
        return "ok" if cur["step"] <= CALM else "bad" if cur["step"] >= TOP else "warn"

    def fields(self) -> dict[str, Any]:
        cur = self.current()
        return {
            "label": self.label(),
            "tone": self.tone(),
            "step": cur["step"] if cur else None,
            "readings": [dict(r) for r in self.readings],
        }


def tone_on(env: Mapping[str, str] | None = None) -> bool:
    raw = (os.environ if env is None else env).get(TONE_ENV, "").strip().lower()
    return raw not in ("0", "off", "false", "no")


def _reader_class():
    from pipecat.processors.tone.reader import PipecatToneReader

    class CallerToneReader(PipecatToneReader):
        """PipecatToneReader on the caller's track, logging each read's time.

        `set_previous` gives it the line the caller is answering: the bot's reply in
        intake (the reader takes it from the assistant turn itself), the agent's last
        line after accept.
        """

        def set_previous(self, text: str) -> None:
            self._previous = text

        async def _read(self, text: str):
            started = time.perf_counter()
            out = await super()._read(text)
            logger.info(f"[tone] read_ms={(time.perf_counter() - started) * 1000:.1f}")
            return out

    return CallerToneReader


def tone_reader_from_env(env: Mapping[str, str] | None = None, *, name: str | None = None):
    """A caller tone reader, or None when the switch is off (TONE_READER=0)."""
    if not tone_on(env):
        logger.info("[tone] TONE_READER is off: no tone readings")
        return None
    return _reader_class()(
        revision=TONE_REVISION, precision=TONE_PRECISION, threads=TONE_THREADS, name=name
    )


class IntakeToneTap(FrameProcessor):
    """After the intake branch's reader: each final caller line, with its reading, is
    saved (`on_final`, the transcript recorder) and added to the mood trend. Intake
    readings never tip (no agent yet). Every frame passes through unchanged.
    """

    def __init__(
        self,
        trend: MoodTrend,
        *,
        on_final: Callable[[dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        super().__init__(name="IntakeToneTap")
        self._trend = trend
        self._on_final = on_final
        self._clock = clock
        self._n = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            self._n += 1
            data: dict[str, Any] = {
                "type": "transcript",
                "role": "caller",
                "text": frame.text.strip(),
                "is_final": True,
                "ts": self._clock(),
                "line_id": f"intake-caller-{self._n}",
            }
            reading = read_tone(frame.perception)
            if reading is not None:
                data["tone"] = reading
                self._trend.add(
                    reading | {"line_id": data["line_id"], "ts": data["ts"]}, can_tip=False
                )
                logger.info(f"[tone] intake caller: {reading['emotion']}/{reading['mood']}")
            if self._on_final is not None:
                self._on_final(data)
        await self.push_frame(frame, direction)
