"""Identity step STT tuning (identity card): numerals, smart format, longer endpointing
only while the identity node is active, and fixed keyterms from the active pack."""

import pytest
from agent_assist_bot import stt_tuning
from agent_assist_bot.packs import load_pack
from agent_assist_bot.stt import build_intake_stt
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import STTUpdateSettingsFrame
from pipecat.services.deepgram.stt import DeepgramSTTService


class Queue:
    def __init__(self):
        self.frames = []

    async def __call__(self, frames):
        self.frames.extend(frames)


@pytest.fixture
def stt():
    return build_intake_stt("fake-key", "nova-3-general", keyterms=["EVM"])


@pytest.fixture
def vad():
    v = SileroVADAnalyzer(params=VADParams(stop_secs=0.2, confidence=0.6))
    v.set_sample_rate(16000)  # done by the pipeline at start
    return v


async def test_on_sends_numerals_smart_format_and_endpointing_to_the_intake_stt(stt, vad):
    q = Queue()
    tuning = stt_tuning.IdentitySTTTuning(stt, vad, q)
    await tuning(True)
    [frame] = q.frames
    assert isinstance(frame, STTUpdateSettingsFrame)
    assert frame.service is stt  # listen branch STTs ignore it
    assert frame.delta.numerals is True
    assert frame.delta.smart_format is True
    assert frame.delta.endpointing == stt_tuning.IDENTITY_ENDPOINTING_MS


async def test_on_lengthens_vad_stop_and_keeps_other_vad_params(stt, vad):
    tuning = stt_tuning.IdentitySTTTuning(stt, vad, Queue())
    await tuning(True)
    assert vad.params.stop_secs == stt_tuning.IDENTITY_VAD_STOP_SECS
    assert vad.params.confidence == 0.6


async def test_off_restores_defaults(stt, vad):
    q = Queue()
    tuning = stt_tuning.IdentitySTTTuning(stt, vad, q)
    await tuning(True)
    await tuning(False)
    off = q.frames[-1]
    assert off.service is stt
    assert off.delta.numerals is False
    assert off.delta.smart_format is False
    assert off.delta.endpointing is None
    assert vad.params.stop_secs == 0.2


async def test_repeated_calls_are_no_ops(stt, vad):
    q = Queue()
    tuning = stt_tuning.IdentitySTTTuning(stt, vad, q)
    await tuning(False)
    await tuning(True)
    await tuning(True)
    assert len(q.frames) == 1


def test_intake_stt_has_fixed_keyterms_and_defaults(stt):
    s = stt._settings
    assert s.keyterm == ["EVM"]
    assert s.model == "nova-3-general"
    assert s.numerals is False and s.smart_format is False


def test_intake_stt_without_keyterms_sends_none():
    s = build_intake_stt("k", "nova-3-general", keyterms=[])._settings
    assert s.keyterm is None
    assert isinstance(build_intake_stt("k", "nova-3-general"), DeepgramSTTService)


def test_claims_pack_keyterms_include_member_id_prefix_and_plan_names():
    terms = load_pack("claims_status").keyterms
    assert "EVM" in terms
    assert "Evergreen HMO Silver" in terms
    assert "Summit PPO Gold" in terms
    # Fixed list: no member names (callers are not known up front).
    assert not {"Maria", "Alvarez", "Chen", "Patel"} & set(" ".join(terms).split())
