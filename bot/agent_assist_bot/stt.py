"""Speech to text per side (P5-7, PRD IN-14): which Deepgram model, which service.

Two sides, each with its own model from the call's settings (call_settings.py):

- Intake (the voice bot, `build_intake_stt`): `nova-3-general` by default, or
  `flux-general-en`. (The card's plan was Flux by default; the live identity harness
  kept Nova-3, see DEFAULT_INTAKE_MODEL.)
  - Flux (`DeepgramFluxSTTService`, /v2/listen) owns turn detection: it reports start
    and end of turn itself, so the intake has no Silero VAD and no smart turn
    (`intake_turn_taking`), and the user aggregator resolves Flux's proposals with
    `ExternalUserTurnStrategies(enable_interruptions=True)`, so the caller can still
    barge in. Pack keyterms and numerals on for the whole call. Eager end of turn is
    off (Flux can answer a predicted end of turn early, but that can add 50 to 70
    percent more LLM calls; docs/spikes/stt-flux-intake.md).
  - Nova-3 (`DeepgramSTTService`, /v1/listen) is the bot before P5-7: Silero VAD plus
    smart turn (Pipecat's default turn strategies), pack keyterms.
- Assist (listen only, one stream per person track after accept, `build_listen_stt`):
  `nova-3-general` by default, `nova-3-medical`, or `flux-general-en`. Flux here never
  interrupts (`should_interrupt=False`) and needs no VAD; its interim (Update) and final
  (EndOfTurn) lines reach TranscriptRelay like Nova's. No Flux medical model exists.

Identity step tuning (`identity_tuning`) differs per intake model; see stt_tuning.py.

Flux wants about 80 ms of audio per message; Daily hands the bot 20 ms frames, so the
Flux services here gather FLUX_CHUNK_MS before sending (`ChunkedFluxSTTService`).

The model lists match agent_assist_server.bot_settings (tested).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADAnalyzer
from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregatorParams
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.stt_service import STTService
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies

from agent_assist_bot.stt_tuning import FluxIdentitySTTTuning, IdentitySTTTuning, QueueFrames

FLUX = "flux-general-en"
NOVA_GENERAL = "nova-3-general"
NOVA_MEDICAL = "nova-3-medical"
INTAKE_MODELS = (NOVA_GENERAL, FLUX)
ASSIST_MODELS = (NOVA_GENERAL, NOVA_MEDICAL, FLUX)
# Nova-3 stays the intake default: Flux intake missed the hard name (Linda Nguyen) on
# every run, identity 8 of 10 vs 10 of 10 (docs/spikes/stt-flux-intake.md). The card
# keeps Nova-3 when Flux regresses identity. Flux is one click away in Settings.
DEFAULT_INTAKE_MODEL = NOVA_GENERAL
DEFAULT_ASSIST_MODEL = NOVA_GENERAL

FLUX_CHUNK_MS = 80
# Flux's own defaults (Pipecat docs for DeepgramFluxSTTService): end of turn confidence
# 0.7, and a turn ends FLUX_EOT_TIMEOUT_MS after speech whatever the confidence.
FLUX_EOT_THRESHOLD = 0.7
FLUX_EOT_TIMEOUT_MS = 5000
# Identity step (spelled member IDs and names, pauses between digit groups): wait for a
# surer end of turn, and longer before ending a turn on the timeout.
FLUX_IDENTITY_EOT_THRESHOLD = 0.85
FLUX_IDENTITY_EOT_TIMEOUT_MS = 7000


def is_flux(model: str) -> bool:
    return model == FLUX


class ChunkedFluxSTTService(DeepgramFluxSTTService):
    """DeepgramFluxSTTService that sends audio in FLUX_CHUNK_MS messages."""

    def __init__(self, *args, chunk_ms: int = FLUX_CHUNK_MS, **kwargs):
        super().__init__(*args, **kwargs)
        self._chunk_ms = chunk_ms
        self._pending = bytearray()

    async def run_stt(self, audio: bytes):
        self._pending.extend(audio)
        chunk = self.sample_rate * 2 * self._chunk_ms // 1000
        if len(self._pending) < chunk:
            yield None
            return
        out, self._pending = bytes(self._pending), bytearray()
        async for frame in super().run_stt(out):
            yield frame


def _flux(api_key: str, keyterms: Sequence[str] | None, *, should_interrupt: bool, **settings):
    return ChunkedFluxSTTService(
        api_key=api_key,
        should_interrupt=should_interrupt,
        enable_eager_end_of_turn=False,
        settings=DeepgramFluxSTTService.Settings(
            model=FLUX, keyterm=list(keyterms or []), **settings
        ),
    )


def _nova(api_key: str, model: str, keyterms: Sequence[str] | None) -> DeepgramSTTService:
    return DeepgramSTTService(
        api_key=api_key,
        settings=DeepgramSTTService.Settings(model=model, keyterm=list(keyterms or []) or None),
    )


def build_intake_stt(api_key: str, model: str, keyterms: Sequence[str] | None = None) -> STTService:
    """The intake STT for `model` (unknown: the default) with the pack's keyterms."""
    model = model if model in INTAKE_MODELS else DEFAULT_INTAKE_MODEL
    if is_flux(model):
        return _flux(api_key, keyterms, should_interrupt=True, numerals=True)
    return _nova(api_key, model, keyterms)


def build_listen_stt(api_key: str, model: str, keyterms: Sequence[str] | None = None) -> STTService:
    """One listen only branch's STT for `model` (unknown: the default)."""
    model = model if model in ASSIST_MODELS else DEFAULT_ASSIST_MODEL
    if is_flux(model):
        return _flux(api_key, keyterms, should_interrupt=False)
    return _nova(api_key, model, keyterms)


def listen_uses_vad(model: str) -> bool:
    """Nova-3 listen branches run their own Silero VAD; Flux detects turns itself."""
    return not is_flux(model if model in ASSIST_MODELS else DEFAULT_ASSIST_MODEL)


@dataclass(frozen=True)
class IntakeTurnTaking:
    """The user aggregator's params, and the VAD (None with Flux) for identity tuning."""

    user_params: LLMUserAggregatorParams
    vad: VADAnalyzer | None


def intake_turn_taking(model: str) -> IntakeTurnTaking:
    if is_flux(model if model in INTAKE_MODELS else DEFAULT_INTAKE_MODEL):
        # Flux's StartOfTurn/EndOfTurn become the turn; a start interrupts the bot.
        strategies = ExternalUserTurnStrategies(enable_interruptions=True)
        return IntakeTurnTaking(LLMUserAggregatorParams(user_turn_strategies=strategies), None)
    vad = SileroVADAnalyzer()
    return IntakeTurnTaking(LLMUserAggregatorParams(vad_analyzer=vad), vad)


def identity_tuning(stt: STTService, vad: VADAnalyzer | None, queue_frames: QueueFrames):
    """The identity step's STT tuning for this intake STT (stt_tuning.py)."""
    if isinstance(stt, DeepgramFluxSTTService):
        return FluxIdentitySTTTuning(
            stt,
            queue_frames,
            on=(FLUX_IDENTITY_EOT_THRESHOLD, FLUX_IDENTITY_EOT_TIMEOUT_MS),
            off=(FLUX_EOT_THRESHOLD, FLUX_EOT_TIMEOUT_MS),
        )
    assert vad is not None, "Nova-3 intake runs with a VAD"
    return IdentitySTTTuning(stt, vad, queue_frames)
