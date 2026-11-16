"""P4-5: tone readings (IN-10), the mood trend, and the two step drop rule (AP-14)."""

from __future__ import annotations

import pytest
from agent_assist_bot.tone import (
    CALM,
    MOODS,
    MoodTrend,
    mood_step,
    read_tone,
    tone_reader_from_env,
    tone_tip,
)
from pipecat.expressive.perception import PerceptionSignal


def signals(label: str, conf: float, second=("neutral", 0.05), sarcasm: float = 0.01):
    return [
        PerceptionSignal("emotion", labels=[(label, conf), second]),
        PerceptionSignal("sarcasm", value=sarcasm),
    ]


def reading(label: str, conf: float = 0.9, sarcasm: float = 0.01, line_id: str = "caller-1"):
    r = read_tone(signals(label, conf, sarcasm=sarcasm))
    return r | {"line_id": line_id, "ts": 1.0}


# ---- one reading ----------------------------------------------------------------------


def test_mood_steps_from_the_readers_emotions():
    assert MOODS == ("Pleased", "Calm", "Uneasy", "Worried", "Frustrated")
    assert mood_step("happy", 0.9, 0.0) == 0
    assert mood_step("amused", 0.9, 0.0) == 0
    assert mood_step("neutral", 0.9, 0.0) == CALM == 1
    assert mood_step("surprised", 0.9, 0.0) == 1
    assert mood_step("sad", 0.9, 0.0) == 3
    assert mood_step("afraid", 0.9, 0.0) == 3
    assert mood_step("angry", 0.9, 0.0) == 4
    assert mood_step("disgusted", 0.9, 0.0) == 4
    assert mood_step("contemptuous", 0.9, 0.0) == 4


def test_unsure_readings_stay_near_calm():
    # Under 0.5 confidence a negative label is only Uneasy, a positive one only Calm.
    assert mood_step("angry", 0.45, 0.0) == 2
    assert mood_step("afraid", 0.3, 0.0) == 2
    assert mood_step("happy", 0.4, 0.0) == 1
    assert mood_step("unknown_label", 0.9, 0.0) == 1


def test_sarcasm_reads_at_least_uneasy():
    # The reader's own note: a sarcastic turn can read happy.
    assert mood_step("happy", 0.9, 0.8) == 2
    assert mood_step("angry", 0.9, 0.8) == 4
    assert mood_step("happy", 0.9, 0.49) == 0


def test_read_tone_keeps_the_readers_labels_and_adds_the_mood():
    r = read_tone(signals("angry", 0.8968, ("disgusted", 0.0508), sarcasm=0.0066))
    assert r == {
        "emotion": "angry",
        "confidence": 0.8968,
        "labels": [["angry", 0.8968], ["disgusted", 0.0508]],
        "sarcasm": 0.0066,
        "step": 4,
        "mood": "Frustrated",
        "score": 1.0,
    }


def test_read_tone_without_an_emotion_signal_is_none():
    assert read_tone(None) is None
    assert read_tone([]) is None
    assert read_tone([PerceptionSignal("sarcasm", value=0.2)]) is None


# ---- trend ----------------------------------------------------------------------------


def test_trend_label_needs_readings():
    t = MoodTrend()
    assert t.label() is None
    assert t.current() is None


def test_trend_label_is_mood_plus_direction():
    t = MoodTrend()
    t.add(reading("neutral"))
    assert t.label() == "Calm"
    t.add(reading("neutral"))
    assert t.label() == "Calm, steady"
    t.add(reading("afraid"))
    assert t.label() == "Worried, worsening"
    t.add(reading("afraid"))
    assert t.label() == "Worried, worsening"  # still above the two before (1, 3)
    t.add(reading("afraid"))
    assert t.label() == "Worried, steady"
    t.add(reading("afraid", 0.4))
    assert t.label() == "Uneasy, easing"


def test_trend_tone_for_the_chip():
    t = MoodTrend()
    t.add(reading("happy"))
    assert t.tone() == "ok"
    t.add(reading("neutral"))
    assert t.tone() == "ok"
    t.add(reading("sad"))
    assert t.tone() == "warn"
    t.add(reading("angry"))
    assert t.tone() == "bad"


def test_trend_message_fields_carry_every_reading_in_order():
    t = MoodTrend()
    t.add(reading("neutral", line_id="intake-caller-1"))
    t.add(reading("angry", line_id="caller-1"))
    f = t.fields()
    assert [r["line_id"] for r in f["readings"]] == ["intake-caller-1", "caller-1"]
    assert [r["score"] for r in f["readings"]] == [0.25, 1.0]
    assert f["label"] == "Frustrated, worsening"
    assert f["tone"] == "bad"
    assert f["step"] == 4
    assert set(f["readings"][0]) == {
        "line_id",
        "ts",
        "emotion",
        "confidence",
        "sarcasm",
        "step",
        "mood",
        "score",
    }


# ---- two step drop --------------------------------------------------------------------


def test_one_step_is_not_a_drop():
    t = MoodTrend()
    assert t.add(reading("neutral")) is False
    assert t.add(reading("angry", 0.4)) is False  # Calm -> Uneasy


def test_two_steps_from_the_calmest_reading_is_a_drop():
    t = MoodTrend()
    assert t.add(reading("neutral")) is False
    assert t.add(reading("afraid")) is True  # Calm -> Worried


def test_drop_is_measured_from_the_calmest_reading_not_the_last():
    t = MoodTrend()
    assert t.add(reading("happy")) is False  # Pleased (0)
    assert t.add(reading("neutral")) is False  # 1
    assert t.add(reading("happy", sarcasm=0.9)) is True  # Uneasy (2): two below Pleased


def test_first_reading_is_measured_from_calm():
    t = MoodTrend()
    assert t.add(reading("angry")) is True  # Calm -> Frustrated
    t = MoodTrend()
    assert t.add(reading("sad", 0.45)) is False  # Calm -> Uneasy


def test_after_a_drop_the_rule_rearms_from_the_new_calmest_reading():
    t = MoodTrend()
    assert t.add(reading("neutral")) is False
    assert t.add(reading("angry")) is True
    assert t.add(reading("angry")) is False  # no new drop while it stays there
    assert t.add(reading("sad")) is False
    assert t.add(reading("neutral")) is False  # recovered to Calm
    assert t.add(reading("angry")) is True  # dropped again


def test_intake_readings_set_the_baseline_but_never_tip():
    t = MoodTrend()
    assert t.add(reading("neutral"), can_tip=False) is False
    assert t.add(reading("angry"), can_tip=False) is False
    # After accept, the caller is still Frustrated: the calmest since the baseline is
    # Calm (intake), so this is a two step drop the agent has not been told about.
    assert t.add(reading("angry")) is True


# ---- tone tip -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "step,headline",
    [(2, "Check in"), (3, "Reassure first"), (4, "Acknowledge, then act")],
)
def test_tone_tip_per_mood(step, headline):
    tip = tone_tip(step)
    assert tip["headline"] == headline
    assert tip["why"]
    assert " she " not in f" {tip['why'].lower()} " and " he " not in f" {tip['why'].lower()} "


# ---- the assist engine: mood messages and the tone tip card ---------------------------


class Sent:
    def __init__(self):
        self.data: list[dict] = []
        self.events: list[tuple] = []

    async def send(self, d):
        self.data.append(d)

    def record(self, kind, card_id, data):
        self.events.append((kind, card_id, data))

    def of(self, event):
        return [d for d in self.data if d["event"] == event]


async def engine(trend=None, max_cards=4):
    from agent_assist_bot.assist import AssistEngine
    from agent_assist_bot.packs import load_packs
    from assist_fakes import Clock, FakeConnectors

    out = Sent()
    e = AssistEngine(
        packs=load_packs(),
        connectors=FakeConnectors(),
        send=out.send,
        record=out.record,
        clock=Clock(),
        max_cards_per_min=max_cards,
        llm_debounce_secs=None,
        trend=trend if trend is not None else MoodTrend(),
    )
    await e.start({"reason": "MRI claim denied"}, default_pack="claims_status")
    return e, out


def line(text, n, r):
    return {
        "type": "transcript",
        "role": "caller",
        "text": text,
        "is_final": True,
        "ts": 1000.0 + n,
        "line_id": f"caller-{n}",
        "tone": r,
    }


async def test_engine_sends_the_mood_with_every_caller_reading():
    e, out = await engine()
    await e.on_tone(line("Hi.", 1, read_tone(signals("neutral", 0.9))))
    mood = out.of("mood")
    assert len(mood) == 1
    assert mood[0]["label"] == "Calm" and mood[0]["tone"] == "ok"
    assert [r["line_id"] for r in mood[0]["readings"]] == ["caller-1"]
    assert out.of("card_added") == []


async def test_intake_readings_are_sent_on_start_and_resync():
    trend = MoodTrend()
    trend.add(reading("neutral", line_id="intake-caller-1"), can_tip=False)
    trend.add(reading("sad", 0.45, line_id="intake-caller-2"), can_tip=False)
    e, out = await engine(trend)
    assert [d["event"] for d in out.data] == ["assist_status", "plan_update", "identity", "mood"]
    assert out.of("mood")[0]["label"] == "Uneasy, worsening"
    out.data.clear()
    await e.resync()
    assert "mood" in [d["event"] for d in out.data]


async def test_two_step_drop_sends_a_tone_tip_card_for_the_agent():
    e, out = await engine()
    await e.on_tone(line("Okay.", 1, read_tone(signals("neutral", 0.9))))
    await e.on_tone(line("This is ridiculous.", 2, read_tone(signals("angry", 0.9))))
    cards = [d["card"] for d in out.of("card_added")]
    assert len(cards) == 1
    tip = cards[0]
    assert tip["kind"] == "tone_tip"
    assert tip["id"] == "tone:caller-2"
    assert tip["title"] == "Tone tip"
    assert tip["body"]["headline"] == "Acknowledge, then act"
    assert tip["body"]["mood"] == "Frustrated"
    assert tip["body"]["emotion"] == "angry"
    assert tip["trigger"]["line_id"] == "caller-2" and tip["trigger"]["phrase"] is None
    assert tip["source"] == {"label": "Pipecat Tone reader, words only", "records": []}
    assert out.of("hero") == []  # never the hero
    assert ("card_sent", "tone:caller-2") in [(k, c) for k, c, _ in out.events]


async def test_tone_tip_waits_for_the_rate_limit():
    e, out = await engine(max_cards=1)
    await e.on_line(line("The MRI claim was denied, no prior authorization.", 1, None))
    n = len(out.of("hero")) + len(out.of("card_added"))
    await e.on_tone(line("I'm so angry about this.", 2, read_tone(signals("angry", 0.9))))
    assert len(out.of("hero")) + len(out.of("card_added")) == n
    assert out.of("assist_status")[-1]["state"] == "rate_limited"
    e._clock.advance(61)
    await e.flush()
    kinds = [d["card"]["kind"] for d in out.of("card_added")]
    assert "tone_tip" in kinds


async def test_a_new_tip_replaces_the_old_one():
    e, out = await engine()
    await e.on_tone(line("This is ridiculous.", 1, read_tone(signals("angry", 0.9))))
    await e.on_tone(line("Okay, thanks.", 2, read_tone(signals("happy", 0.9))))
    await e.on_tone(line("I'm worried.", 3, read_tone(signals("afraid", 0.9))))
    assert [d["card"]["id"] for d in out.of("card_added")] == ["tone:caller-1", "tone:caller-3"]
    removed = out.of("card_removed")
    assert [(r["card_id"], r["reason"]) for r in removed] == [("tone:caller-1", "replaced")]
    assert set(e.supporting) == {"tone:caller-3"}


async def test_lines_without_a_reading_change_nothing():
    e, out = await engine()
    n = len(out.data)
    await e.on_tone(line("Hi.", 1, None))
    assert len(out.data) == n


# ---- switch ---------------------------------------------------------------------------


# ---- pipeline wiring ------------------------------------------------------------------


async def test_relay_attaches_the_caller_reading_to_the_final_line():
    from agent_assist_bot.transcript import CALLER, CallRoles, TranscriptRelay
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.tests.utils import run_test
    from pipecat.transports.daily.transport import DailyOutputTransportMessageUrgentFrame

    roles = CallRoles()
    roles.set_caller("c1")
    roles.agent_accepted("Ana")
    roles.joined("a1", "Ana")
    finals: list[dict] = []
    frame = TranscriptionFrame("This is ridiculous.", "c1", "t")
    frame.perception = signals("angry", 0.9)
    plain = TranscriptionFrame("Okay.", "c1", "t")
    await run_test(
        TranscriptRelay(CALLER, roles, on_final=finals.append, clock=lambda: 5.0),
        frames_to_send=[frame, plain],
        expected_down_frames=[DailyOutputTransportMessageUrgentFrame] * 2,
    )
    assert finals[0]["tone"]["mood"] == "Frustrated"
    assert finals[0]["tone"]["emotion"] == "angry"
    assert "tone" not in finals[1]


async def test_recorder_saves_the_reading_with_the_line():
    from agent_assist_bot.calls import MemoryCallStore
    from agent_assist_bot.transcript import TranscriptRecorder, transcript_message

    store = MemoryCallStore()
    call_id = await store.create_call("s1")
    rec = TranscriptRecorder(store, lambda: call_id)
    r = read_tone(signals("afraid", 0.9))
    rec.add(transcript_message("caller", "I'm worried.", True, 10.0, "caller-1") | {"tone": r})
    rec.add(transcript_message("agent", "Let me check.", True, 11.0, "agent-1"))
    await rec.close()
    assert store.calls[call_id].transcript == [
        ("caller", "I'm worried.", 10.0),
        ("agent", "Let me check.", 11.0),
    ]
    assert store.calls[call_id].tones == [r, None]


async def test_intake_tap_saves_caller_lines_with_tone_and_feeds_the_trend():
    from agent_assist_bot.tone import IntakeToneTap
    from pipecat.frames.frames import InterimTranscriptionFrame, TextFrame, TranscriptionFrame
    from pipecat.tests.utils import run_test

    saved: list[dict] = []
    trend = MoodTrend()
    a = TranscriptionFrame("My name is Maria.", "c1", "t")
    a.perception = signals("neutral", 0.9)
    b = TranscriptionFrame("I already called twice!", "c1", "t")
    b.perception = signals("angry", 0.9)
    unread = TranscriptionFrame("Okay.", "c1", "t")
    await run_test(
        IntakeToneTap(trend, on_final=saved.append, clock=lambda: 7.0),
        frames_to_send=[a, InterimTranscriptionFrame("I al", "c1", "t"), b, unread, TextFrame("x")],
        expected_down_frames=[
            TranscriptionFrame,
            InterimTranscriptionFrame,
            TranscriptionFrame,
            TranscriptionFrame,
            TextFrame,
        ],
    )
    assert [(d["line_id"], d["role"], d["text"]) for d in saved] == [
        ("intake-caller-1", "caller", "My name is Maria."),
        ("intake-caller-2", "caller", "I already called twice!"),
        ("intake-caller-3", "caller", "Okay."),
    ]
    assert [d.get("tone", {}).get("mood") for d in saved] == ["Calm", "Frustrated", None]
    # Intake readings set the trend but never tip (no agent yet).
    assert [r["line_id"] for r in trend.readings] == ["intake-caller-1", "intake-caller-2"]
    assert trend.add(reading("angry")) is True


def _branches(worker):
    from pipecat.pipeline.parallel_pipeline import ParallelPipeline

    inner = next(p for p in worker._pipeline.processors if type(p).__name__ == "Pipeline")
    par = next(p for p in inner.processors if isinstance(p, ParallelPipeline))
    return par._pipelines


def _keys(monkeypatch):
    for k in ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(k, "fake")


def test_reader_reads_the_caller_only_after_each_stt(monkeypatch):
    from agent_assist_bot import bot
    from agent_assist_bot.calls import MemoryCallStore
    from pipecat.processors.frameworks.rtvi import RTVIObserver
    from pipecat.processors.tone.reader import PipecatToneReader

    _keys(monkeypatch)
    monkeypatch.delenv("TONE_READER", raising=False)
    worker, _ = bot.build_worker("https://x.daily.co/r", "tok", store=MemoryCallStore())
    names = [[type(x).__name__ for x in b.processors] for b in _branches(worker)]
    intake, caller, agent = names
    # P5-7: the reader follows whichever intake STT runs (Nova-3 by default; Flux
    # also pushes one final TranscriptionFrame per caller turn).
    assert intake[intake.index("DeepgramSTTService") + 1] == "CallerToneReader"
    assert intake[intake.index("CallerToneReader") + 1] == "IntakeToneTap"
    assert caller[caller.index("DeepgramSTTService") + 1] == "CallerToneReader"
    assert caller[caller.index("CallerToneReader") + 1] == "TranscriptRelay"
    assert "CallerToneReader" not in agent
    readers = [
        x for b in _branches(worker) for x in b.processors if isinstance(x, PipecatToneReader)
    ]
    assert all(r._threads == 1 and r._precision == "fp16" for r in readers)
    observer = next(o for o in worker._observer._observers if isinstance(o, RTVIObserver))
    assert readers[1] in observer._ignored_sources  # the listen reader is agent-only


def test_switch_off_builds_the_pipeline_without_readers(monkeypatch):
    from agent_assist_bot import bot
    from agent_assist_bot.calls import MemoryCallStore

    _keys(monkeypatch)
    monkeypatch.setenv("TONE_READER", "0")
    worker, _ = bot.build_worker("https://x.daily.co/r", "tok", store=MemoryCallStore())
    names = [type(x).__name__ for b in _branches(worker) for x in b.processors]
    assert "CallerToneReader" not in names and "IntakeToneTap" not in names


def test_caller_never_gets_rtvi_user_input_with_tone(monkeypatch):
    from agent_assist_bot import bot
    from agent_assist_bot.calls import MemoryCallStore
    from pipecat.processors.frameworks.rtvi import RTVIObserver

    _keys(monkeypatch)
    worker, _ = bot.build_worker("https://x.daily.co/r", "tok", store=MemoryCallStore())
    observer = next(o for o in worker._observer._observers if isinstance(o, RTVIObserver))
    assert observer._params.user_input_enabled is False


def test_agent_lines_become_the_turn_before_for_the_caller_reader():
    from agent_assist_bot.bot import PreviousLine
    from agent_assist_bot.tone import tone_reader_from_env

    reader = tone_reader_from_env({})
    prev = PreviousLine(reader)
    prev({"role": "caller", "text": "Hi."})
    assert reader._previous is None
    prev({"role": "agent", "text": "I can see the claim."})
    assert reader._previous == "I can see the claim."


# ---- switch ---------------------------------------------------------------------------


def test_switch_off_means_no_reader():
    assert tone_reader_from_env({"TONE_READER": "0"}) is None
    assert tone_reader_from_env({"TONE_READER": "off"}) is None


def test_switch_on_by_default_with_one_thread_fp16():
    reader = tone_reader_from_env({})
    assert reader is not None
    assert reader._threads == 1
    assert reader._precision == "fp16"
    assert reader._revision == "reader-rc2"
