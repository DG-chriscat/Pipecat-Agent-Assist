"""Identity step STT tuning (identity card, docs/spikes/identity.md).

Spelled member IDs and names were misheard in Phase 2 (docs/spikes/phase2.md): STT
split "E V M, one zero zero, two three one" across endpoints and the turn ended on
the pause between digit groups. While the identity node is active the intake STT
gets:

- Deepgram `numerals` and `smart_format` on, so "one zero zero two three one" comes
  through as digits and dates are formatted.
- Deepgram `endpointing` raised from the default (10 ms) to IDENTITY_ENDPOINTING_MS.
- The intake VAD's `stop_secs` raised from 0.2 s to IDENTITY_VAD_STOP_SECS, so a short
  pause between digit groups does not end the caller's turn.

All of it goes back to the defaults when the identity check runs (or intake ends), so
the rest of intake keeps its fast turn taking. The Deepgram settings change is an
`STTUpdateSettingsFrame` aimed at the intake STT only (`service=`), so the listen only
transcript branches keep theirs; Pipecat reconnects the Deepgram stream between
turns and buffers audio meanwhile.

Fixed keyterms from the active pack (`stt_keyterms`) are set on the intake STT for the
whole call (Nova 3 keyterm prompting).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from loguru import logger
from pipecat.audio.vad.vad_analyzer import VADAnalyzer
from pipecat.frames.frames import Frame, STTUpdateSettingsFrame
from pipecat.services.deepgram.stt import DeepgramSTTService

IDENTITY_ENDPOINTING_MS = 800
IDENTITY_VAD_STOP_SECS = 0.8


def build_intake_stt(
    api_key: str, model: str, keyterms: Sequence[str] | None = None
) -> DeepgramSTTService:
    """The intake STT: Deepgram with the pack's fixed keyterms (None when empty)."""
    return DeepgramSTTService(
        api_key=api_key,
        settings=DeepgramSTTService.Settings(model=model, keyterm=list(keyterms or []) or None),
    )


class IdentitySTTTuning:
    """`await tuning(True)` on entering identity, `await tuning(False)` on leaving."""

    def __init__(
        self,
        stt: DeepgramSTTService,
        vad: VADAnalyzer,
        queue_frames: Callable[[list[Frame]], Awaitable[None]],
    ):
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
