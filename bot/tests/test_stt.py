"""STT factory (P5-7, PRD IN-14): one Deepgram service per side and model, the intake turn
taking that goes with it, and the identity step tuning for each model. No network."""

from __future__ import annotations

import pytest
from agent_assist_bot import stt
from agent_assist_bot.stt_tuning import FluxIdentitySTTTuning, IdentitySTTTuning
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import STTUpdateSettingsFrame
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies

KEYTERMS = ["EVM", "CLM", "Summit PPO Gold"]


class Queue:
    def __init__(self):
        self.frames = []

    async def __call__(self, frames):
        self.frames.extend(frames)


def test_model_lists_and_defaults():
    assert stt.INTAKE_MODELS == ("flux-general-en", "nova-3-general")
    assert stt.ASSIST_MODELS == ("nova-3-general", "nova-3-medical", "flux-general-en")
    assert stt.DEFAULT_INTAKE_MODEL == "flux-general-en"
    assert stt.DEFAULT_ASSIST_MODEL == "nova-3-general"


# ---- intake ---------------------------------------------------------------------------


def test_flux_intake_is_the_flux_service_with_keyterms_numerals_and_interruptions():
    s = stt.build_intake_stt("k", "flux-general-en", KEYTERMS)
    assert isinstance(s, DeepgramFluxSTTService)
    assert s._url == "wss://api.deepgram.com/v2/listen"
    assert s._settings.model == "flux-general-en"
    assert s._settings.keyterm == KEYTERMS
    assert s._settings.numerals is True
    assert s._should_interrupt is True
    # Eager end of turn stays off: it can add 50 to 70 percent more LLM calls.
    assert s.eager_end_of_turn_enabled is False
    assert s._settings.eager_eot_threshold is None


def test_flux_intake_recommends_external_turns_that_interrupt():
    s = stt.build_intake_stt("k", "flux-general-en", KEYTERMS)
    rec = s.recommended_user_turn_strategies(enable_interruptions=s._should_interrupt)
    assert isinstance(rec, ExternalUserTurnStrategies) and rec.enable_interruptions


def test_nova_intake_is_todays_service():
    s = stt.build_intake_stt("k", "nova-3-general", KEYTERMS)
    assert type(s) is DeepgramSTTService
    assert s._settings.model == "nova-3-general"
    assert s._settings.keyterm == KEYTERMS
    assert s._settings.numerals is False


def test_intake_without_keyterms():
    assert stt.build_intake_stt("k", "nova-3-general", [])._settings.keyterm is None
    assert stt.build_intake_stt("k", "flux-general-en", [])._settings.keyterm == []


def test_unknown_intake_model_builds_the_default():
    s = stt.build_intake_stt("k", "nova-3-medical", KEYTERMS)
    assert isinstance(s, DeepgramFluxSTTService)


def test_flux_intake_turns_belong_to_flux_no_vad_no_smart_turn():
    turns = stt.intake_turn_taking("flux-general-en")
    assert turns.vad is None
    assert turns.user_params.vad_analyzer is None
    strategies = turns.user_params.user_turn_strategies
    assert isinstance(strategies, ExternalUserTurnStrategies)
    assert strategies.enable_interruptions is True
    names = {type(s).__name__ for s in [*strategies.start, *strategies.stop]}
    assert "TurnAnalyzerUserTurnStopStrategy" not in names
    assert "VADUserTurnStartStrategy" not in names


def test_nova_intake_keeps_silero_vad_and_smart_turn():
    turns = stt.intake_turn_taking("nova-3-general")
    assert isinstance(turns.vad, SileroVADAnalyzer)
    assert turns.user_params.vad_analyzer is turns.vad
    # None: Pipecat's defaults, VAD start plus smart turn (LocalSmartTurnAnalyzerV3) stop.
    assert turns.user_params.user_turn_strategies is None


# ---- assist (listen only) ---------------------------------------------------------------


@pytest.mark.parametrize("model", ["nova-3-general", "nova-3-medical"])
def test_nova_listen_stt_per_model(model):
    s = stt.build_listen_stt("k", model, KEYTERMS)
    assert type(s) is DeepgramSTTService
    assert s._settings.model == model
    assert s._settings.keyterm == KEYTERMS


def test_flux_listen_stt_never_interrupts():
    s = stt.build_listen_stt("k", "flux-general-en", KEYTERMS)
    assert isinstance(s, DeepgramFluxSTTService)
    assert s._should_interrupt is False
    assert s.eager_end_of_turn_enabled is False
    assert s._settings.keyterm == KEYTERMS


def test_unknown_listen_model_builds_the_default():
    s = stt.build_listen_stt("k", "flux-general-multi", None)
    assert type(s) is DeepgramSTTService and s._settings.model == "nova-3-general"


def test_listen_stages_per_model():
    # Nova-3 listen branches keep their own Silero VAD; Flux needs none.
    assert stt.listen_uses_vad("nova-3-general") and stt.listen_uses_vad("nova-3-medical")
    assert not stt.listen_uses_vad("flux-general-en")


# ---- 80 ms chunks for Flux ----------------------------------------------------------------


async def test_flux_sends_audio_in_80_ms_chunks():
    s = stt.build_intake_stt("k", "flux-general-en", KEYTERMS)
    sent: list[bytes] = []

    async def send(audio: bytes):
        sent.append(audio)

    s._transport_send_audio = send
    s._transport_is_active = lambda: True
    s._sample_rate = 16000
    frame_20ms = b"\x01\x00" * 320
    for _ in range(7):
        async for _ in s.run_stt(frame_20ms):
            pass
    # 7 x 20 ms in: one 80 ms chunk sent, 60 ms waiting for the next one.
    assert [len(c) for c in sent] == [2560]


# ---- identity step tuning ------------------------------------------------------------------


async def test_identity_tuning_for_flux_raises_eot_and_restores_defaults():
    s = stt.build_intake_stt("k", "flux-general-en", KEYTERMS)
    q = Queue()
    tuning = stt.identity_tuning(s, None, q)
    assert isinstance(tuning, FluxIdentitySTTTuning)
    await tuning(True)
    await tuning(True)  # repeated: no second update
    await tuning(False)
    on, off = q.frames
    assert isinstance(on, STTUpdateSettingsFrame) and on.service is s
    assert on.delta.eot_timeout_ms == stt.FLUX_IDENTITY_EOT_TIMEOUT_MS
    assert on.delta.eot_threshold == stt.FLUX_IDENTITY_EOT_THRESHOLD
    assert on.delta.eot_timeout_ms > stt.FLUX_EOT_TIMEOUT_MS
    assert on.delta.eot_threshold > stt.FLUX_EOT_THRESHOLD
    assert off.service is s
    assert off.delta.eot_timeout_ms == stt.FLUX_EOT_TIMEOUT_MS
    assert off.delta.eot_threshold == stt.FLUX_EOT_THRESHOLD


async def test_flux_identity_update_goes_out_as_a_configure_message():
    s = stt.build_intake_stt("k", "flux-general-en", KEYTERMS)
    sent: list[dict] = []

    async def send_json(message: dict):
        sent.append(message)

    s._transport_send_json = send_json
    s._transport_is_active = lambda: True
    q = Queue()
    await stt.identity_tuning(s, None, q)(True)
    await s._update_settings(q.frames[0].delta)
    assert sent == [
        {
            "type": "Configure",
            "thresholds": {
                "eot_threshold": stt.FLUX_IDENTITY_EOT_THRESHOLD,
                "eot_timeout_ms": stt.FLUX_IDENTITY_EOT_TIMEOUT_MS,
            },
        }
    ]


def test_identity_tuning_for_nova_is_todays_endpointing_and_vad_tweak():
    s = stt.build_intake_stt("k", "nova-3-general", KEYTERMS)
    vad = SileroVADAnalyzer()
    assert isinstance(stt.identity_tuning(s, vad, Queue()), IdentitySTTTuning)


def test_flux_identity_values_are_inside_fluxs_ranges():
    assert 0.5 <= stt.FLUX_IDENTITY_EOT_THRESHOLD <= 1.0
    assert 500 <= stt.FLUX_IDENTITY_EOT_TIMEOUT_MS <= 60000
