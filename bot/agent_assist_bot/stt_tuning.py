"""Identity step STT tuning (identity card, docs/spikes/identity.md; Flux: P5-7, IN-14).

Spelled member IDs and names were misheard in Phase 2 (docs/spikes/phase2.md): STT
split "E V M, one zero zero, two three one" across endpoints and the turn ended on
the pause between digit groups. While the identity node is active the intake STT
gets a longer end of turn, and goes back to its defaults when the identity check runs
(or intake ends), so the rest of intake keeps its fast turn taking.

Nova-3 intake (`IdentitySTTTuning`):
- Deepgram `numerals` and `smart_format` on, so "one zero zero two three one" comes
  through as digits and dates are formatted.
- Deepgram `endpointing` raised from the default (10 ms) to IDENTITY_ENDPOINTING_MS.
- The intake VAD's `stop_secs` raised from 0.2 s to IDENTITY_VAD_STOP_SECS, so a short
  pause between digit groups does not end the caller's turn.

Flux intake (`FluxIdentitySTTTuning`): Flux owns turn detection (no VAD, no smart
turn), so the levers are Flux's own: `eot_threshold` and `eot_timeout_ms` raised for
the identity step, sent mid stream as a Flux Configure message (Pipecat's
`_update_settings` does this for threshold fields; no reconnect). Numerals are on for
the whole call (`stt.build_intake_stt`); Flux has no smart_format.

Either way the change is an `STTUpdateSettingsFrame` aimed at the intake STT only
(`service=`), so the listen only transcript branches keep theirs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from loguru import logger
from pipecat.audio.vad.vad_analyzer import VADAnalyzer
from pipecat.frames.frames import Frame, STTUpdateSettingsFrame
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.stt_service import STTService

IDENTITY_ENDPOINTING_MS = 800
IDENTITY_VAD_STOP_SECS = 0.8

QueueFrames = Callable[[list[Frame]], Awaitable[None]]


class IdentitySTTTuning:
    """Nova-3: `await tuning(True)` on entering identity, `await tuning(False)` on leaving."""

    def __init__(self, stt: DeepgramSTTService, vad: VADAnalyzer, queue_frames: QueueFrames):
        self._stt = stt
        self._vad = vad
        self._queue_frames = queue_frames
        self._default_stop_secs = vad.params.stop_secs
        self._active = False

    async def __call__(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        if active:
            delta = DeepgramSTTService.Settings(
                numerals=True, smart_format=True, endpointing=IDENTITY_ENDPOINTING_MS
            )
            stop_secs = IDENTITY_VAD_STOP_SECS
        else:
            delta = DeepgramSTTService.Settings(
                numerals=False, smart_format=False, endpointing=None
            )
            stop_secs = self._default_stop_secs
        self._vad.set_params(self._vad.params.model_copy(update={"stop_secs": stop_secs}))
        await self._queue_frames([STTUpdateSettingsFrame(delta=delta, service=self._stt)])
        logger.info(f"[intake] identity STT tuning {'on' if active else 'off'}")


class FluxIdentitySTTTuning:
    """Flux: the identity step's end of turn thresholds on and off (Configure message).

    `on` and `off` are (eot_threshold, eot_timeout_ms) pairs; `off` must be explicit
    values (Flux's defaults), since a Configure carries numbers, not "unset".
    """

    def __init__(
        self,
        stt: STTService,
        queue_frames: QueueFrames,
        *,
        on: tuple[float, int],
        off: tuple[float, int],
    ):
        self._stt = stt
        self._queue_frames = queue_frames
        self._on = on
        self._off = off
        self._active = False

    async def __call__(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        threshold, timeout_ms = self._on if active else self._off
        delta = type(self._stt).Settings(eot_threshold=threshold, eot_timeout_ms=timeout_ms)
        await self._queue_frames([STTUpdateSettingsFrame(delta=delta, service=self._stt)])
        logger.info(
            f"[intake] identity STT tuning {'on' if active else 'off'} (flux "
            f"eot_threshold={threshold} eot_timeout_ms={timeout_ms})"
        )
