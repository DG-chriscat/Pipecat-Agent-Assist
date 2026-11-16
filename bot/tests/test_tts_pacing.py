"""Flux voice controls in the bot (P5-8, PRD AP-22): speed and expressivity at connect,
and slow read backs (speed switched with Configure around a read back line, then
restored, no reconnect). The fake Flux TTS is the real service with the websocket
replaced: it records every message it would send. No network."""

from __future__ import annotations

import asyncio

import pytest
from agent_assist_bot import call_settings as cs
from agent_assist_bot import intake, tts_pacing
from agent_assist_bot.intake import READBACK_LINE, speak_member_id
from pipecat.frames.frames import TTSSpeakFrame, TTSUpdateSettingsFrame
from pipecat.services.deepgram.flux.tts import DeepgramFluxTTSService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.tests.utils import run_test

FLUX = {
    "tts_model": "flux",
    "tts_voice": "flux-alexis-en",
    "tts_speed": 1.1,
    "tts_expressivity": 1,
    "slow_readback": True,
    "readback_speed": 0.9,
}

IDENTITY_LINE = READBACK_LINE.format(name="Maria Alvarez", member_id=speak_member_id("EVM100231"))


# ---- settings ---------------------------------------------------------------------------


def test_flux_control_defaults():
    d = cs.CallSettings.defaults({})
    assert (d.tts_speed, d.tts_expressivity, d.slow_readback, d.readback_speed) == (
        1.0,
        0,
        True,
        0.9,
    )


def test_flux_controls_come_from_the_servers_values():
    s = cs.CallSettings.from_mapping(FLUX, env={})
    assert (s.tts_speed, s.tts_expressivity, s.slow_readback, s.readback_speed) == (
        1.1,
        1,
        True,
        0.9,
    )


@pytest.mark.parametrize(
    "over",
    [
        {"tts_speed": 1.6},
        {"tts_speed": "fast"},
        {"tts_expressivity": 3},
        {"tts_expressivity": True},
        {"readback_speed": 1.1},
        {"slow_readback": "yes"},
    ],
)
def test_an_unusable_flux_control_falls_back_to_its_default(over):
    s = cs.CallSettings.from_mapping({**FLUX, **over}, env={})
    d = cs.CallSettings.defaults({})
    for key in over:
        assert getattr(s, key) == getattr(d, key)


def test_log_line_names_the_flux_controls():
    line = cs.CallSettings.from_mapping(FLUX, env={}).describe()
    assert "speed=1.1 expressivity=1 slow_readback=0.9" in line
    off = cs.CallSettings.from_mapping({**FLUX, "slow_readback": False}, env={}).describe()
    assert "slow_readback=off" in off


# ---- connect --------------------------------------------------------------------------


def test_flux_gets_speed_and_expressivity_at_connect():
    tts = cs.build_tts(cs.CallSettings.from_mapping(FLUX, env={}), api_key="k")
    assert isinstance(tts, DeepgramFluxTTSService)
    assert (tts._settings.speed, tts._settings.expressivity) == (1.1, 1)
    tts._sample_rate = 16000
    query = tts._build_query_string()
    assert "speed=1.1" in query and "expressivity=1" in query


def test_default_flux_connects_at_speed_1_and_expressivity_0():
    tts = cs.build_tts(cs.CallSettings.defaults({}), api_key="k")
    assert (tts._settings.speed, tts._settings.expressivity) == (1.0, 0)


def test_aura_ignores_the_flux_controls():
    s = cs.CallSettings.from_mapping(
        {**FLUX, "tts_model": "aura-2", "tts_voice": "aura-2-thalia-en"}, env={}
    )
    tts = cs.build_tts(s, api_key="k")
    assert type(tts) is DeepgramTTSService
    assert cs.build_readback_pacer(s) is None


def test_pacer_only_when_slow_read_backs_are_on_and_slower():
    on = cs.CallSettings.from_mapping(FLUX, env={})
    assert isinstance(cs.build_readback_pacer(on), tts_pacing.ReadbackPacer)
    off = cs.CallSettings.from_mapping({**FLUX, "slow_readback": False}, env={})
    assert cs.build_readback_pacer(off) is None
    same = cs.CallSettings.from_mapping({**FLUX, "tts_speed": 0.9}, env={})
    assert cs.build_readback_pacer(same) is None


# ---- which lines are read backs --------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        IDENTITY_LINE,
        "Your member ID is EVM100231.",
        "So that's Maria, date of birth March 12, 1984. Is that right?",
        "You were born 03/12/1984, right?",
        "Born on the 12th of March, 1984?",
        "That's claim CLM-900001 for $1,240.",
        "Got it, claim number C L M, nine zero zero, zero zero one.",
        "Member ID E V M 1 0 0 2 3 1.",
    ],
)
def test_lines_with_a_member_id_dob_or_claim_number_are_read_backs(text):
    assert tts_pacing.is_readback_line(text)


@pytest.mark.parametrize(
    "text",
    [
        intake.GREETING,
        "Thanks for waiting. You're number 2 in line. A person will be with you shortly.",
        "What's the reason for your call today?",
        intake.HANDOFF_LINE,
        "Thanks, you are verified.",
        # Asking for a detail is not reading one back (seen live: these were slowed).
        "Thanks, Maria. What's your date of birth and member ID?",
        "Do you have the claim number? It's on your explanation of benefits.",
        "When were you born?",
    ],
)
def test_other_lines_are_not(text):
    assert not tts_pacing.is_readback_line(text)


def test_a_frame_marked_as_a_read_back_counts_whatever_its_text():
    frame = tts_pacing.mark_readback(TTSSpeakFrame("So you're calling about a denied bill."))
    assert tts_pacing.is_readback_frame(frame)
    assert not tts_pacing.is_readback_frame(TTSSpeakFrame("So you're calling about a bill."))


async def test_identity_read_back_is_marked():
    from agent_assist_bot.calls import MemoryCallStore
    from agent_assist_bot.packs import load_pack

    class Worker:
        def __init__(self):
            self.frames = []

        async def queue_frame(self, frame):
            self.frames.append(frame)

    class Actions:
        def _increment_ongoing_actions_count(self):
            pass

    class FM:
        worker = Worker()
        _action_manager = Actions()

    s = intake.IntakeSession(
        pack=load_pack("claims_status"), store=MemoryCallStore(), session_id="s"
    )
    s.intake.update(full_name="Maria Alvarez", member_id="EVM100231")
    action = next(a for a in s.identity_confirm_node()["pre_actions"] if a["type"] == "tts_say")
    await intake.uninterruptible_tts_say(action, FM)
    speak = FM.worker.frames[0]
    assert speak.text == IDENTITY_LINE and tts_pacing.is_readback_frame(speak)


async def test_say_to_caller_in_the_summary_read_back_is_marked():
    from agent_assist_bot.calls import MemoryCallStore
    from agent_assist_bot.packs import load_pack

    class Worker:
        def __init__(self):
            self.frames = []

        async def queue_frames(self, frames):
            self.frames.extend(frames)

    class FM:
        worker = Worker()

    s = intake.IntakeSession(
        pack=load_pack("claims_status"), store=MemoryCallStore(), session_id="s"
    )
    s.node = "readback"
    await s._on_say({"text": "So you're calling about a denied bill. Right?"}, FM)
    s.node = "reason"
    await s._on_say({"text": "What's the reason for your call?"}, FM)
    summary, other = FM.worker.frames
    assert tts_pacing.is_readback_frame(summary)
    assert not tts_pacing.is_readback_frame(other)


# ---- the pacer with a fake Flux TTS ---------------------------------------------------------


class FakeFluxTTS(tts_pacing.FluxTTSService):
    """The bot's Flux TTS with the websocket faked: records what it sends, and answers a
    Flush with a little audio and SpeechMetadata, as Flux does."""

    def __init__(self, **kw):
        super().__init__(api_key="k", **kw)
        self.sent: list[dict] = []
        self.connects = 0
        self._open = False

    async def _connect(self):
        self.connects += 1
        self._open = True

    async def _disconnect(self):
        self._open = False

    def _transport_is_active(self) -> bool:
        return self._open

    async def _transport_send_json(self, message: dict):
        self.sent.append(message)
        if message["type"] == "Flush":
            asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(self._finish()))

    async def _finish(self):
        await asyncio.sleep(0.01)
        await self._handle_audio(b"\x00\x00" * 160)
        await self._handle_message({"type": "SpeechMetadata", "speech_id": "x"})


def _trace(tts: FakeFluxTTS) -> list[str]:
    out = []
    for m in tts.sent:
        if m["type"] == "Configure":
            out.append(f"speed {m['speed']}")
        elif m["type"] == "Speak":
            out.append(f"say {m['text'].strip()}")
    return out


async def _run(frames, settings=FLUX):
    s = cs.CallSettings.from_mapping(settings, env={})
    tts = FakeFluxTTS(
        settings=DeepgramFluxTTSService.Settings(
            voice=s.tts_voice, speed=s.tts_speed, expressivity=s.tts_expressivity
        ),
        session_id="sess",
    )
    pacer = cs.build_readback_pacer(s)
    from pipecat.pipeline.pipeline import Pipeline

    stages = [p for p in (pacer, tts) if p is not None]
    await run_test(Pipeline(stages), frames_to_send=frames)
    return tts


async def test_read_back_is_slowed_then_the_next_line_is_back_to_normal():
    tts = await _run(
        [
            TTSSpeakFrame("Hi there."),
            TTSSpeakFrame(IDENTITY_LINE),
            TTSSpeakFrame("What's the reason for your call?"),
        ]
    )
    assert _trace(tts) == [
        "say Hi there.",
        "speed 0.9",
        f"say {IDENTITY_LINE}",
        "speed 1.1",
        "say What's the reason for your call?",
    ]
    assert tts.connects == 1  # Configure only, no reconnect


async def test_marked_summary_read_back_is_slowed():
    line = "So you're calling about a denied bill. Is that right?"
    tts = await _run([tts_pacing.mark_readback(TTSSpeakFrame(line)), TTSSpeakFrame("Thanks.")])
    assert _trace(tts) == ["speed 0.9", f"say {line}", "speed 1.1", "say Thanks."]


async def test_slow_read_backs_off_never_changes_speed():
    tts = await _run([TTSSpeakFrame(IDENTITY_LINE)], settings={**FLUX, "slow_readback": False})
    assert _trace(tts) == [f"say {IDENTITY_LINE}"]


async def test_streamed_llm_read_back_slows_once_the_id_shows_and_restores_after():
    from pipecat.frames.frames import (
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
    )

    tts = await _run(
        [
            LLMFullResponseStartFrame(),
            LLMTextFrame("Thanks. "),
            LLMTextFrame("Your claim number "),
            LLMTextFrame("is CLM-900001."),
            LLMFullResponseEndFrame(),
            TTSSpeakFrame("Anything else?"),
        ]
    )
    trace = _trace(tts)
    slow, restore = trace.index("speed 0.9"), trace.index("speed 1.1")
    said_slow = " ".join(t for t in trace[slow:restore] if t.startswith("say"))
    assert "CLM-900001" in said_slow
    assert trace[restore + 1 :] == ["say Anything else?"]
    assert tts.connects == 1


async def test_speed_frames_survive_a_barge_in():
    frames = tts_pacing.ReadbackPacer(tts_speed=1.1, readback_speed=0.9).frames_for(
        TTSSpeakFrame(IDENTITY_LINE)
    )
    slow, line, restore = frames
    assert isinstance(slow, TTSUpdateSettingsFrame) and not slow.interruptible
    assert isinstance(restore, TTSUpdateSettingsFrame) and not restore.interruptible
    assert (slow.delta.speed, restore.delta.speed) == (0.9, 1.1)
    assert line.text == IDENTITY_LINE


async def test_speed_changes_are_logged_on_the_timing_log():
    from loguru import logger

    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), format="{message}")
    try:
        await _run([TTSSpeakFrame(IDENTITY_LINE)])
    finally:
        logger.remove(sink)
    speed = [line for line in lines if "event=tts_speed" in line]
    assert len(speed) == 2
    assert "speed=0.9" in speed[0] and "reason=readback" in speed[0]
    assert "speed=1.1" in speed[1] and "reason=restore" in speed[1]
    assert all("session=sess" in line for line in speed)
