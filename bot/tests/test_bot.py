"""Bot wiring: greeting, dial-in option, handlers, CLI parsing. No network."""

import asyncio

import pytest
from agent_assist_bot import __main__ as cli
from agent_assist_bot import bot, hold
from agent_assist_bot.call_settings import CallSettings
from agent_assist_bot.calls import MemoryCallStore, PostgresCallStore
from agent_assist_bot.intake import IntakeSession
from agent_assist_bot.packs import load_pack, load_packs
from agent_assist_bot.stt_tuning import FluxIdentitySTTTuning, IdentitySTTTuning
from agent_assist_bot.transcript import CallRoles, TranscriptRelay
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import EndFrame
from pipecat.pipeline.parallel_pipeline import ParallelPipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frameworks.rtvi import RTVIObserver
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService as FluxSTT
from pipecat.services.stt_service import STTService
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies

LISTEN_STAGES = ("AudioRoute", "VADProcessor", "DeepgramSTTService", "TranscriptRelay")
FLUX_LISTEN_STAGES = ("AudioRoute", "ChunkedFluxSTTService", "TranscriptRelay")

KEYS = {
    "DAILY_API_KEY": "fake-daily",
    "DEEPGRAM_API_KEY": "fake-dg",
    "ANTHROPIC_API_KEY": "fake-anthropic",
}
ROOM = "https://dg-chriscat.daily.co/dialin-test"
DIALIN = bot.DialinConfig(call_id="call-123", call_domain="domain-456", api_key="fake-daily")


class FakeWorker:
    def __init__(self):
        self.queued = []
        self.cancelled = False

    async def queue_frames(self, frames):
        self.queued.extend(frames)

    async def cancel(self, **kwargs):
        self.cancelled = True


class FakeTransport:
    def __init__(self):
        self.handlers = {}

    def event_handler(self, name):
        def deco(fn):
            self.handlers[name] = fn
            return fn

        return deco


@pytest.fixture
def api_keys(monkeypatch):
    for k, v in KEYS.items():
        monkeypatch.setenv(k, v)


def test_greeting_identifies_ai_and_human_handoff():
    # PRD IN-2
    assert "AI assistant" in bot.GREETING
    assert "person will join" in bot.GREETING
    assert "Deepgram Vitals" in bot.GREETING
    assert "!" not in bot.GREETING  # DESIGN.md brand copy: no exclamation points


async def test_first_participant_joined_starts_intake_once():
    transport, worker = FakeTransport(), FakeWorker()
    started = []

    async def start():
        started.append(True)

    bot.register_handlers(transport, worker, on_caller_joined=start)
    await transport.handlers["on_first_participant_joined"](transport, {"id": "caller"})
    await transport.handlers["on_first_participant_joined"](transport, {"id": "caller"})
    await asyncio.sleep(0)  # intake starts in its own task
    assert started == [True]


class FakeFlowManager:
    def __init__(self):
        self.initialized_with = None
        self.nodes = []

    async def initialize(self, node):
        self.initialized_with = node
        self.nodes.append(node["name"])

    async def set_node_from_config(self, node):
        self.nodes.append(node["name"])


async def test_start_intake_creates_call_row_and_greets_with_tts_only():
    # IN-1/IN-2: the greeting is a fixed tts_say pre-action, no LLM round trip.
    store = MemoryCallStore()
    session = IntakeSession(pack=load_pack("claims_status"), store=store, session_id="s-9")
    fm = FakeFlowManager()
    await bot.start_intake(session, fm)
    assert [r.session_id for r in store.calls.values()] == ["s-9"]
    node = fm.initialized_with
    assert node["name"] == "greeting"
    assert {"type": "tts_say", "text": bot.GREETING} in node["pre_actions"]
    assert node["respond_immediately"] is False


async def test_start_intake_goes_straight_from_greeting_to_identity():
    # The caller's first answer must land in the identity node, which has verify_identity.
    session = IntakeSession(
        pack=load_pack("claims_status"), store=MemoryCallStore(), session_id="s"
    )
    fm = FakeFlowManager()
    await bot.start_intake(session, fm)
    assert fm.nodes == ["greeting", "identity"]


async def test_start_intake_queues_greeting_before_db_insert():
    # IN-1 tuning: the calls row insert (1.3 s on the live call) must not delay the
    # greeting.
    order = []

    class SlowStore(MemoryCallStore):
        async def create_call(self, session_id):
            order.append("create_call")
            return await super().create_call(session_id)

    class Recording(FakeFlowManager):
        async def initialize(self, node):
            order.append(node["name"])
            await super().initialize(node)

        async def set_node_from_config(self, node):
            order.append(node["name"])
            await super().set_node_from_config(node)

    session = IntakeSession(pack=load_pack("claims_status"), store=SlowStore(), session_id="s")
    await bot.start_intake(session, Recording())
    assert order == ["greeting", "create_call", "identity"]
    assert session.call_id is not None


async def test_start_intake_survives_db_failure():
    class Down(MemoryCallStore):
        async def create_call(self, session_id):
            raise OSError("db down")

    session = IntakeSession(pack=load_pack("claims_status"), store=Down(), session_id="s")
    fm = FakeFlowManager()
    await bot.start_intake(session, fm)
    assert fm.initialized_with["name"] == "greeting"


def test_call_store_from_env_picks_postgres_or_memory():
    assert isinstance(bot.call_store_from_env({}), MemoryCallStore)
    store = bot.call_store_from_env({"DATABASE_URL": "postgresql://u@h/db"})
    assert isinstance(store, PostgresCallStore)
    assert "postgresql" not in repr(store)


def test_call_store_from_env_honours_schema():
    store = bot.call_store_from_env(
        {"DATABASE_URL": "postgresql://u@h/db", "DATABASE_SCHEMA": "t1"}
    )
    assert store._schema == "t1"


async def test_participant_left_ends_session():
    transport, worker = FakeTransport(), FakeWorker()
    bot.register_handlers(transport, worker)
    await transport.handlers["on_participant_left"](transport, {"id": "caller"}, "hangup")
    assert worker.cancelled


async def test_caller_leaving_during_the_greeting_does_not_strand_intake_start():
    """Live bug (P5-2): start_intake waits in Flows for the greeting to finish playing.
    If the caller hangs up first, the worker is cancelled and the greeting never
    finishes, so the join handler never returned and Daily's cleanup (which waits for
    its event handlers) hung the bot process forever, even on SIGTERM."""
    transport, worker = FakeTransport(), FakeWorker()
    greeting_done = asyncio.Event()
    cancelled = []

    async def start():
        try:
            await greeting_done.wait()  # never set: the call ends mid greeting
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    bot.register_handlers(transport, worker, on_caller_joined=start)
    # The join handler hands intake off and returns at once.
    await asyncio.wait_for(
        transport.handlers["on_first_participant_joined"](transport, {"id": "caller"}), 1
    )
    await asyncio.sleep(0)
    await transport.handlers["on_participant_left"](transport, {"id": "caller"}, "leftCall")
    await asyncio.sleep(0)
    assert worker.cancelled
    assert cancelled == [True]


def _p(pid: str, name: str) -> dict:
    return {"id": pid, "info": {"userName": name}}


async def test_roles_follow_the_call_flow_and_accept_starts_listening():
    # P3-4: caller = first participant, agent = the accepting agent's user_name.
    transport, worker = FakeTransport(), FakeWorker()
    roles = CallRoles()
    bot.register_handlers(transport, worker, roles=roles)
    h = transport.handlers
    await h["on_first_participant_joined"](transport, _p("c1", "caller"))
    await h["on_participant_joined"](transport, _p("c1", "caller"))
    await h["on_participant_joined"](transport, _p("a1", "Agent Ana"))
    assert roles.caller_id == "c1"
    assert not roles.listening and roles.agent_id is None

    accepted = {"type": hold.MESSAGE_TYPE, "event": "agent_accepted", "agent_name": "Agent Ana"}
    await h["on_app_message"](transport, accepted, "api")
    assert roles.listening
    assert roles.agent_id == "a1"
    assert worker.queued[0].text == hold.CONNECTING_LINE

    await h["on_participant_left"](transport, _p("a1", "Agent Ana"), "left")
    assert roles.agent_id is None
    assert not worker.cancelled  # the agent leaving never ends the caller's call


async def test_accept_from_a_participant_does_not_start_listening():
    transport, worker = FakeTransport(), FakeWorker()
    roles = CallRoles()
    bot.register_handlers(transport, worker, roles=roles)
    accepted = {"type": hold.MESSAGE_TYPE, "event": "agent_accepted", "agent_name": "x"}
    await transport.handlers["on_app_message"](transport, accepted, "some-participant")
    assert not roles.listening


class FakeAssist:
    def __init__(self):
        self.calls = []

    async def on_accept(self):
        self.calls.append(("accept",))

    async def on_app_message(self, message, sender):
        self.calls.append(("message", sender))

    async def on_participant_joined(self, pid):
        self.calls.append(("joined", pid))


async def test_accept_starts_assist_once_and_forwards_messages_and_joins():
    # P4-3: the assist starts on accept; agent feedback and (re)joins reach it.
    transport, worker = FakeTransport(), FakeWorker()
    roles, assist = CallRoles(), FakeAssist()
    bot.register_handlers(transport, worker, roles=roles, assist=assist)
    h = transport.handlers
    await h["on_first_participant_joined"](transport, _p("c1", "caller"))
    await h["on_participant_joined"](transport, _p("c1", "caller"))
    accepted = {"type": hold.MESSAGE_TYPE, "event": "agent_accepted", "agent_name": "Agent Ana"}
    await h["on_app_message"](transport, accepted, "api")
    await h["on_app_message"](transport, accepted, "api")
    await h["on_participant_joined"](transport, _p("a1", "Agent Ana"))
    await h["on_app_message"](transport, {"label": "rtvi-ai"}, "a1")
    assert assist.calls.count(("accept",)) == 1
    assert ("joined", "a1") in assist.calls
    assert ("message", "a1") in assist.calls
    # The accept line is the last thing the bot says.
    assert [f.text for f in worker.queued] == [hold.CONNECTING_LINE]


# ---- P5-1: wrap up draft at call end (AP-9) -----------------------------------------


class Closable:
    def __init__(self, name, log):
        self.name, self.log = name, log

    async def close(self):
        self.log.append(self.name)


class EndAssist(Closable):
    def __init__(self, log, accepted):
        super().__init__("assist", log)
        self.accepted = accepted

    def wrap_up_inputs(self):
        self.log.append("inputs")
        return "INPUTS"


class FakeReporter:
    def __init__(self, log):
        self.log = log
        self.started = []

    def start(self, session_id, inputs):
        self.log.append("draft")
        self.started.append((session_id, inputs))

    async def wait(self):
        self.log.append("wait")


async def test_call_end_drafts_wrap_up_before_closing_and_waits_for_it():
    log: list[str] = []
    reporter = FakeReporter(log)
    on_ended = bot.end_of_call(
        session_id="s1",
        closers=[Closable("intake", log), EndAssist(log, True), Closable("store", log)],
        assist=EndAssist(log, True),
        reporter=reporter,
        handoff=Closable("handoff", log),
    )
    await on_ended()
    assert reporter.started == [("s1", "INPUTS")]
    # Inputs are read and the draft starts before anything closes; the draft is
    # posted before the handoff client (and the process) goes away.
    assert log == ["inputs", "draft", "intake", "assist", "store", "wait", "handoff"]


async def test_call_end_without_an_accepted_agent_drafts_nothing():
    log: list[str] = []
    reporter = FakeReporter(log)
    on_ended = bot.end_of_call(
        session_id="s1",
        closers=[],
        assist=EndAssist(log, False),
        reporter=reporter,
        handoff=object(),
    )
    await on_ended()
    assert reporter.started == []
    assert log == ["wait"]


def test_listen_branches_boost_pack_keyterms(api_keys):
    worker, _ = bot.build_worker(ROOM, "tok", store=MemoryCallStore())
    par = _parallel(worker)
    stts = [
        x
        for b in par._pipelines[1:]
        for x in b.processors
        if type(x).__name__ == "DeepgramSTTService"
    ]
    assert len(stts) == 2
    for stt in stts:
        assert stt._settings.model == "nova-3-general"  # IN-14 assist default
        terms = stt._settings.keyterm
        assert "CO-197" in terms and "charged twice" in terms and "EOB" in terms
        assert len(terms) == len(set(terms)) <= 100


def _parallel(worker) -> ParallelPipeline:
    inner = next(p for p in worker._pipeline.processors if type(p).__name__ == "Pipeline")
    return next(p for p in inner.processors if isinstance(p, ParallelPipeline))


def _user_aggregator(worker):
    inner = next(p for p in worker._pipeline.processors if type(p).__name__ == "Pipeline")
    return next(p for p in inner.processors if type(p).__name__ == "LLMUserAggregator")


def _settings(**over) -> CallSettings:
    return CallSettings.from_mapping(over, env={})


def _spy_session(monkeypatch) -> dict:
    seen = {}
    real_session = bot.IntakeSession

    def spy(**kwargs):
        seen["session"] = real_session(**kwargs)
        return seen["session"]

    monkeypatch.setattr(bot, "IntakeSession", spy)
    return seen


def test_flux_intake_owns_turns_without_vad_or_smart_turn(api_keys, monkeypatch):
    seen = _spy_session(monkeypatch)
    settings = _settings(stt_intake_model="flux-general-en")
    worker, _ = bot.build_worker(ROOM, "tok", store=MemoryCallStore(), settings=settings)
    intake = [type(x).__name__ for x in _parallel(worker)._pipelines[0].processors]
    assert "ChunkedFluxSTTService" in intake
    assert "VADProcessor" not in intake and "DeepgramSTTService" not in intake
    params = _user_aggregator(worker)._params
    assert params.vad_analyzer is None
    assert isinstance(params.user_turn_strategies, ExternalUserTurnStrategies)
    assert params.user_turn_strategies.enable_interruptions is True
    stt = next(x for x in _parallel(worker)._pipelines[0].processors if isinstance(x, FluxSTT))
    assert stt._should_interrupt is True
    assert isinstance(seen["session"].identity_tuning, FluxIdentitySTTTuning)


def test_nova_intake_by_default_keeps_silero_vad_and_smart_turn(api_keys, monkeypatch):
    seen = _spy_session(monkeypatch)
    worker, _ = bot.build_worker(ROOM, "tok", store=MemoryCallStore())
    intake = [type(x).__name__ for x in _parallel(worker)._pipelines[0].processors]
    assert "DeepgramSTTService" in intake and "ChunkedFluxSTTService" not in intake
    params = _user_aggregator(worker)._params
    assert isinstance(params.vad_analyzer, SileroVADAnalyzer)
    assert params.user_turn_strategies is None  # Pipecat default: VAD + smart turn
    assert isinstance(seen["session"].identity_tuning, IdentitySTTTuning)


def test_flux_assist_branches_never_interrupt_and_need_no_vad(api_keys):
    settings = _settings(stt_assist_model="flux-general-en")
    worker, _ = bot.build_worker(ROOM, "tok", store=MemoryCallStore(), settings=settings)
    par = _parallel(worker)
    for b in par._pipelines[1:]:
        names = [type(x).__name__ for x in b.processors]
        assert [n for n in names if n in FLUX_LISTEN_STAGES] == list(FLUX_LISTEN_STAGES)
        stt = next(x for x in b.processors if isinstance(x, FluxSTT))
        assert stt._should_interrupt is False
    relays = [x for b in par._pipelines for x in b.processors if isinstance(x, TranscriptRelay)]
    assert sorted(r._role for r in relays) == ["agent", "caller"]
    # The listen branches stay invisible to RTVI, Flux or not.
    observer = next(o for o in worker._observer._observers if isinstance(o, RTVIObserver))
    listen = [
        x
        for b in par._pipelines[1:]
        for x in b.processors
        if type(x).__name__ in FLUX_LISTEN_STAGES
    ]
    assert len(listen) == 6
    assert set(listen) <= observer._ignored_sources


def test_medical_assist_branches_run_nova_3_medical(api_keys):
    settings = _settings(stt_assist_model="nova-3-medical")
    worker, _ = bot.build_worker(ROOM, "tok", store=MemoryCallStore(), settings=settings)
    stts = [
        x
        for b in _parallel(worker)._pipelines[1:]
        for x in b.processors
        if type(x).__name__ == "DeepgramSTTService"
    ]
    assert [s._settings.model for s in stts] == ["nova-3-medical", "nova-3-medical"]


def test_keyterms_are_deduped_across_packs():
    terms = bot.listen_keyterms(load_packs())
    assert len(terms) == len(set(t.lower() for t in terms))
    assert "EVM" in terms


def test_listen_branch_finals_reach_transcript_store_and_assist(api_keys):
    worker, _ = bot.build_worker(ROOM, "tok", store=MemoryCallStore())
    inner = next(p for p in worker._pipeline.processors if type(p).__name__ == "Pipeline")
    par = next(p for p in inner.processors if isinstance(p, ParallelPipeline))
    relays = [x for b in par._pipelines for x in b.processors if isinstance(x, TranscriptRelay)]
    for r in relays:
        targets = {type(getattr(f, "__self__", f)).__name__ for f in r._on_final.targets}
        assert targets == {"TranscriptRecorder", "AssistSession", "PreviousLine"}


def test_pipeline_has_intake_and_two_listen_branches(api_keys):
    worker, transport = bot.build_worker(ROOM, "tok")
    assert transport._params.audio_in_user_tracks is True
    inner = next(p for p in worker._pipeline.processors if type(p).__name__ == "Pipeline")
    par = next(p for p in inner.processors if isinstance(p, ParallelPipeline))
    branches = [[type(x).__name__ for x in b.processors] for b in par._pipelines]
    assert [b[1] for b in branches] == ["AudioRoute"] * 3
    assert "DeepgramSTTService" in branches[0] and "TranscriptRelay" not in branches[0]
    for b in branches[1:]:
        core = [n for n in b if n in LISTEN_STAGES]
        assert core == list(LISTEN_STAGES)
    relays = [x for b in par._pipelines for x in b.processors if isinstance(x, TranscriptRelay)]
    assert sorted(r._role for r in relays) == ["agent", "caller"]
    # RTVI's own user transcription messages go to everyone in the room, so the
    # listen branches are ignored by the RTVI observer: only TranscriptRelay speaks.
    observer = next(o for o in worker._observer._observers if isinstance(o, RTVIObserver))
    listen = [
        x for b in par._pipelines[1:] for x in b.processors if type(x).__name__ in LISTEN_STAGES
    ]
    assert len(listen) == 8
    assert set(listen) <= observer._ignored_sources


@pytest.mark.parametrize("model", ["flux-general-en", "nova-3-general"])
def test_intake_stt_uses_the_active_pack_keyterms(api_keys, model):
    # Identity card: fixed keyterms from the active pack (P4-2 `keyterms`) on the
    # intake STT; the identity step's tuning goes to this STT only.
    pack = load_pack("claims_status")
    worker, _ = bot.build_worker(
        ROOM, "tok", pack=pack, store=MemoryCallStore(), settings=_settings(stt_intake_model=model)
    )
    intake_stt = next(
        x for x in _parallel(worker)._pipelines[0].processors if isinstance(x, STTService)
    )
    assert intake_stt._settings.model == model
    assert intake_stt._settings.keyterm == pack.keyterms
    assert "EVM" in intake_stt._settings.keyterm


class FakeForwarder:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls = []

    async def warm(self):
        self.calls.append("warm")

    async def forward(self, sip_uri):
        self.calls.append(("forward", sip_uri))
        if self.error:
            raise self.error

    async def close(self):
        self.calls.append("close")


async def test_dialin_ready_without_dialin_config_does_nothing():
    transport, worker = FakeTransport(), FakeWorker()
    bot.register_handlers(transport, worker)
    await transport.handlers["on_joined"](transport, {})
    await transport.handlers["on_dialin_ready"](transport, "sip:x@daily")
    assert not worker.cancelled and not worker.queued


async def test_dialin_config_builds_a_real_forwarder(monkeypatch):
    made = []
    monkeypatch.setattr(bot, "DialinForwarder", lambda cfg: made.append(cfg) or FakeForwarder())
    bot.register_handlers(FakeTransport(), FakeWorker(), dialin=DIALIN)
    assert made == [DIALIN]


@pytest.mark.parametrize(
    "payload",
    [
        {"provider": "signalwire", "sipEndpoint": "sip:room.0@daily.signalwire.com"},
        "sip:room.0@daily.signalwire.com",
    ],
    ids=["dict-payload", "string-payload"],
)
async def test_dialin_ready_forwards_held_call_with_string_sip_uri(payload):
    # Live call on Nov 15: daily-python sends a dict here, and Pipecat's built-in
    # forwarding posted it as sipUri, so Daily answered 400 and the caller was stuck.
    transport, worker, fwd = FakeTransport(), FakeWorker(), FakeForwarder()
    bot.register_handlers(transport, worker, dialin=DIALIN, forwarder=fwd)
    await transport.handlers["on_joined"](transport, {})
    await transport.handlers["on_dialin_ready"](transport, payload)
    assert fwd.calls == ["warm", ("forward", "sip:room.0@daily.signalwire.com"), "close"]
    assert not worker.cancelled


async def test_dialin_forward_failure_ends_session():
    from agent_assist_bot.dialin import DialinForwardError

    transport, worker = FakeTransport(), FakeWorker()
    fwd = FakeForwarder(DialinForwardError("pinlessCallUpdate returned 400"))
    bot.register_handlers(transport, worker, dialin=DIALIN, forwarder=fwd)
    await transport.handlers["on_dialin_ready"](transport, {"sipEndpoint": "sip:x@d"})
    assert worker.cancelled
    assert fwd.calls[-1] == "close"


async def test_dialin_ready_without_sip_endpoint_ends_session():
    transport, worker, fwd = FakeTransport(), FakeWorker(), FakeForwarder()
    bot.register_handlers(transport, worker, dialin=DIALIN, forwarder=fwd)
    await transport.handlers["on_dialin_ready"](transport, {"provider": "signalwire"})
    assert worker.cancelled
    assert not any(isinstance(c, tuple) for c in fwd.calls)


async def test_dialin_error_ends_session():
    transport, worker = FakeTransport(), FakeWorker()
    bot.register_handlers(transport, worker)
    await transport.handlers["on_dialin_error"](transport, {"error": "x"})
    assert worker.cancelled


def test_transport_has_no_dialin_settings():
    # The bot forwards dial-in calls itself, so Pipecat's forwarding stays off.
    params = bot.build_transport(ROOM, "tok")._params
    assert params.dialin_settings is None
    assert params.audio_in_enabled and params.audio_out_enabled
    assert params.camera_out_enabled is False


def test_build_worker_with_dialin(api_keys):
    worker, transport = bot.build_worker(ROOM, "tok", DIALIN)
    assert isinstance(worker, PipelineWorker)
    assert transport._params.dialin_settings is None


def test_transport_carries_the_hold_music_mixer(api_keys, monkeypatch):
    # QH-5: dial-in and browser calls build the same transport, so both get music.
    from agent_assist_bot.hold_music import DEFAULT_TRACK, HoldMusicMixer

    monkeypatch.delenv("HOLD_MUSIC", raising=False)
    for dialin in (DIALIN, None):
        _, transport = bot.build_worker(ROOM, "tok", dialin)
        mixer = transport._params.audio_out_mixer
        assert isinstance(mixer, HoldMusicMixer)
        assert mixer.path == DEFAULT_TRACK
        assert not mixer.enabled  # silent through intake


def test_hold_music_off_builds_no_mixer(api_keys, monkeypatch):
    monkeypatch.setenv("HOLD_MUSIC", "off")
    _, transport = bot.build_worker(ROOM, "tok")
    assert transport._params.audio_out_mixer is None


@pytest.mark.parametrize(("raw", "secs"), [(None, 60.0), ("8", 8.0), ("x", 60.0), ("0", 60.0)])
def test_hold_update_interval_from_env(raw, secs):
    env = {} if raw is None else {"HOLD_UPDATE_SECS": raw}
    assert hold.hold_interval_from_env(env) == secs


async def test_queued_message_turns_on_the_transport_mixer(api_keys, monkeypatch):
    # The HoldController built by build_worker drives the transport's own mixer.
    monkeypatch.delenv("HOLD_MUSIC", raising=False)
    _, transport = bot.build_worker(ROOM, "tok")
    mixer = transport._params.audio_out_mixer
    handlers = transport._event_handlers["on_app_message"].handlers
    queued = {"type": hold.MESSAGE_TYPE, "event": "queued", "position": 1}
    for h in handlers:
        await h(transport, queued, "api")
    assert mixer.enabled
    accepted = {"type": hold.MESSAGE_TYPE, "event": "agent_accepted", "agent_name": "Ana"}
    for h in handlers:
        await h(transport, accepted, "api")
    assert not mixer.enabled


def test_pipeline_times_first_audio_just_before_output(api_keys):
    worker, _ = bot.build_worker(ROOM, "tok")
    inner = next(p for p in worker._pipeline.processors if type(p).__name__ == "Pipeline")
    names = [type(p).__name__ for p in inner.processors]
    assert names.index("FirstAudioTimer") == names.index("DailyOutputTransport") - 1


async def test_run_bot_passes_dialin_and_runs(monkeypatch):
    calls = []

    class FakeRunner:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        async def add_workers(self, *workers):
            calls.append(("add", workers))

        async def run(self):
            calls.append(("run",))

    sentinel = object()
    seen = {}

    def fake_build_worker(room_url, token=None, dialin=None, session_id="", **kwargs):
        seen.update(room_url=room_url, token=token, dialin=dialin, session_id=session_id)
        return sentinel, None

    monkeypatch.setattr(bot, "WorkerRunner", FakeRunner)
    monkeypatch.setattr(bot, "build_worker", fake_build_worker)
    await bot.run_bot(ROOM, "tok", DIALIN, session_id="sess-7")
    assert seen == {"room_url": ROOM, "token": "tok", "dialin": DIALIN, "session_id": "sess-7"}
    assert calls[0][1]["handle_sigterm"] is True
    assert calls[1] == ("add", (sentinel,))
    assert calls[2] == ("run",)


async def test_run_bot_waits_for_a_pending_wrap_up_draft(monkeypatch):
    # P5-1: the agent's End can finish the pipeline while the draft is in flight;
    # run_bot must not return (and exit the process) before it is posted.
    import asyncio

    from agent_assist_bot import wrap_up

    posted = []

    async def slow_post(session_id, body):
        await asyncio.sleep(0.05)
        posted.append(session_id)

    class FakeRunner:
        def __init__(self, **kwargs):
            pass

        async def add_workers(self, *workers):
            pass

        async def run(self):
            r = wrap_up.WrapUpReporter(lambda: wrap_up.WrapUpDrafter(None), slow_post)
            r.start("s1", _wrap_inputs())

    monkeypatch.setattr(bot, "WorkerRunner", FakeRunner)
    monkeypatch.setattr(bot, "build_worker", lambda *a, **k: (object(), None))
    await bot.run_bot(ROOM, "tok", None, session_id="s1")
    assert posted == ["s1"]


def _wrap_inputs():
    from agent_assist_bot.wrap_up import WrapUpInputs

    return WrapUpInputs(pack=load_pack("claims_status"), intake={"full_name": "Maria"})


def test_require_keys_reports_names_not_values():
    with pytest.raises(bot.MissingKeyError) as e:
        bot.require_keys({"DAILY_API_KEY": "secret-value"})
    msg = str(e.value)
    assert "DEEPGRAM_API_KEY" in msg and "ANTHROPIC_API_KEY" in msg
    assert "secret-value" not in msg
    assert bot.require_keys(KEYS) == KEYS


def test_cli_parse_args_reads_token_from_env():
    args = cli.parse_args(
        ["--room-url", "https://x.daily.co/r", "--call-id", "c", "--call-domain", "d"],
        env={"DAILY_ROOM_TOKEN": "tok"},
    )
    assert args.token == "tok"
    assert args.call_id == "c" and args.call_domain == "d"
    cfg = args.dialin_config("daily-key", "https://api.example/v1")
    assert cfg == bot.DialinConfig("c", "d", "daily-key", "https://api.example/v1")


def test_cli_requires_token():
    with pytest.raises(SystemExit):
        cli.parse_args(
            ["--room-url", "https://x.daily.co/r", "--call-id", "c", "--call-domain", "d"],
            env={},
        )


def test_cli_main_runs_bot_with_dialin(monkeypatch, api_keys):
    seen = {}

    async def fake_run_bot(room_url, token=None, dialin=None, session_id="", settings=None):
        seen.update(room_url=room_url, token=token, dialin=dialin)

    monkeypatch.setenv("DAILY_ROOM_TOKEN", "tok")
    monkeypatch.delenv("DAILY_API_URL", raising=False)
    monkeypatch.setattr(cli, "run_bot", fake_run_bot)
    rc = cli.main(["--room-url", ROOM, "--call-id", "c", "--call-domain", "d"])
    assert rc == 0
    assert seen["room_url"] == ROOM and seen["token"] == "tok"
    assert seen["dialin"] == bot.DialinConfig("c", "d", "fake-daily", bot.DEFAULT_DAILY_API_URL)


def test_cli_parse_args_without_dialin(monkeypatch):
    # IN-12 browser caller: no call_id/call_domain
    args = cli.parse_args(["--room-url", ROOM, "--session-id", "s1"], env={"DAILY_ROOM_TOKEN": "t"})
    assert args.call_id is None and args.call_domain is None
    assert args.session_id == "s1"
    assert args.dialin_config("daily-key", "https://api.example/v1") is None


@pytest.mark.parametrize(
    "extra", [["--call-id", "c"], ["--call-domain", "d"]], ids=["only-id", "only-domain"]
)
def test_cli_rejects_half_dialin(extra):
    with pytest.raises(SystemExit):
        cli.parse_args(["--room-url", ROOM, *extra], env={"DAILY_ROOM_TOKEN": "t"})


def test_cli_main_runs_bot_without_dialin(monkeypatch, api_keys):
    seen = {}

    async def fake_run_bot(room_url, token=None, dialin=None, session_id="", settings=None):
        seen.update(room_url=room_url, token=token, dialin=dialin, session_id=session_id)

    monkeypatch.setenv("DAILY_ROOM_TOKEN", "tok")
    monkeypatch.setattr(cli, "run_bot", fake_run_bot)
    assert cli.main(["--room-url", ROOM, "--session-id", "s-1"]) == 0
    assert seen == {"room_url": ROOM, "token": "tok", "dialin": None, "session_id": "s-1"}


def test_cli_main_without_session_id_makes_one(monkeypatch, api_keys):
    seen = {}

    async def fake_run_bot(room_url, token=None, dialin=None, session_id="", settings=None):
        seen["session_id"] = session_id

    monkeypatch.setenv("DAILY_ROOM_TOKEN", "tok")
    monkeypatch.setattr(cli, "run_bot", fake_run_bot)
    assert cli.main(["--room-url", ROOM]) == 0
    assert seen["session_id"]


def test_cli_main_missing_keys_exits_2(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DAILY_ROOM_TOKEN", "tok")
    assert cli.main(["--room-url", ROOM, "--call-id", "c", "--call-domain", "d"]) == 2


# ---- queue hold and handoff wiring (QH-5, QH-6) -------------------------------------


class RecordingHandoff:
    def __init__(self):
        self.ended = []
        self.completed = []

    async def on_intake_complete(self, handoff):
        self.completed.append(handoff)

    async def on_call_ended(self, session_id):
        self.ended.append(session_id)


async def test_app_message_from_server_accept_speaks_connecting_line():
    from agent_assist_bot import hold

    transport, worker = FakeTransport(), FakeWorker()
    ctl = hold.HoldController(worker.queue_frames)
    bot.register_handlers(transport, worker, hold=ctl)
    msg = {"type": hold.MESSAGE_TYPE, "event": "agent_accepted", "agent_name": "Ana"}
    await transport.handlers["on_app_message"](transport, msg, "api")
    assert [f.text for f in worker.queued] == [hold.CONNECTING_LINE]
    assert not ctl.gate.open


async def test_participant_left_tells_queue_call_ended():
    transport, worker, h = FakeTransport(), FakeWorker(), RecordingHandoff()
    bot.register_handlers(transport, worker, handoff=h, session_id="s1")
    await transport.handlers["on_participant_left"](transport, {"id": "caller"}, "hangup")
    assert h.ended == ["s1"]
    assert worker.cancelled


def test_pipeline_has_gate_before_llm(api_keys):
    from agent_assist_bot.hold import BotGate
    from pipecat.services.llm_service import LLMService

    worker, _ = bot.build_worker(ROOM, "tok")
    inner = next(p for p in worker._pipeline.processors if type(p).__name__ == "Pipeline")
    procs = inner.processors
    llm_at = next(i for i, p in enumerate(procs) if isinstance(p, LLMService))
    assert isinstance(procs[llm_at - 1], BotGate)


def test_cli_passes_session_id(monkeypatch, api_keys):
    seen = {}

    async def fake_run_bot(room_url, token=None, dialin=None, session_id="", settings=None):
        seen.update(session_id=session_id)

    monkeypatch.setenv("DAILY_ROOM_TOKEN", "tok")
    monkeypatch.setattr(cli, "run_bot", fake_run_bot)
    assert cli.main(["--room-url", ROOM, "--session-id", "s9"]) == 0
    assert seen == {"session_id": "s9"}


async def test_agent_leaving_does_not_end_the_call():
    transport, worker, h = FakeTransport(), FakeWorker(), RecordingHandoff()
    bot.register_handlers(transport, worker, handoff=h, session_id="s1")
    caller = {"id": "caller-id", "info": {"userName": "caller"}}
    agent = {"id": "agent-id", "info": {"userName": "Agent Ana"}}
    await transport.handlers["on_first_participant_joined"](transport, caller)
    await transport.handlers["on_participant_left"](transport, agent, "leftCall")
    assert not worker.cancelled
    assert h.ended == []
    await transport.handlers["on_participant_left"](transport, caller, "leftCall")
    assert worker.cancelled
    assert h.ended == ["s1"]


def test_build_worker_gives_one_hook_to_intake_and_handlers(monkeypatch, api_keys):
    """The intake Flow enqueues via the hook; the caller leaving ends via the same hook."""
    seen = {}
    real_session = bot.IntakeSession

    def spy_session(**kwargs):
        seen["intake"] = kwargs["handoff"]
        seen["session_id"] = kwargs["session_id"]
        return real_session(**kwargs)

    def spy_handlers(transport, worker, on_caller_joined=None, on_ended=None, **kwargs):
        seen.update(handlers=kwargs["handoff"], handlers_session=kwargs["session_id"])

    monkeypatch.setattr(bot, "IntakeSession", spy_session)
    monkeypatch.setattr(bot, "register_handlers", spy_handlers)
    h = RecordingHandoff()
    bot.build_worker(ROOM, "tok", session_id="s-5", store=MemoryCallStore(), handoff=h)
    assert seen["intake"] is h and seen["handlers"] is h
    assert seen["session_id"] == seen["handlers_session"] == "s-5"


def test_build_worker_defaults_to_queue_handoff_from_env(monkeypatch, api_keys):
    from agent_assist_bot.handoff import SERVER_URL_ENV, QueueHandoff
    from agent_assist_bot.intake import NoopHandoff

    seen = []
    real_session = bot.IntakeSession

    def spy_session(**kwargs):
        seen.append(kwargs["handoff"])
        return real_session(**kwargs)

    monkeypatch.setattr(bot, "IntakeSession", spy_session)
    monkeypatch.delenv(SERVER_URL_ENV, raising=False)
    bot.build_worker(ROOM, "tok", store=MemoryCallStore())
    monkeypatch.setenv(SERVER_URL_ENV, "http://127.0.0.1:8000")
    bot.build_worker(ROOM, "tok", store=MemoryCallStore())
    assert isinstance(seen[0], NoopHandoff)
    assert isinstance(seen[1], QueueHandoff)
    assert seen[1].server_url == "http://127.0.0.1:8000"


async def test_run_bot_passes_queue_handoff_from_env(monkeypatch):
    from agent_assist_bot.handoff import SERVER_URL_ENV, QueueHandoff

    seen = {}

    class FakeRunner:
        def __init__(self, **kwargs):
            pass

        async def add_workers(self, *workers):
            pass

        async def run(self):
            pass

    def fake_build_worker(room_url, token=None, dialin=None, session_id="", **kwargs):
        seen.update(kwargs)
        return object(), None

    monkeypatch.setenv(SERVER_URL_ENV, "http://srv")
    monkeypatch.setattr(bot, "WorkerRunner", FakeRunner)
    monkeypatch.setattr(bot, "build_worker", fake_build_worker)
    await bot.run_bot(ROOM, "tok", None, session_id="s")
    assert isinstance(seen["handoff"], QueueHandoff)


async def test_intake_end_enqueues_through_queue_handoff():
    """request_person mid intake posts the handoff (with flags) to the queue API."""
    import json

    import httpx
    from agent_assist_bot.handoff import QueueHandoff

    posted = []

    def handler(req):
        posted.append((req.url.path, json.loads(req.content or b"{}")))
        return httpx.Response(200, json={"position": 1})

    h = QueueHandoff("http://srv", transport=httpx.MockTransport(handler))
    session = IntakeSession(
        pack=load_pack("claims_status"), store=MemoryCallStore(), session_id="s-q", handoff=h
    )
    await session.start()
    _, node = await session._on_request_person({}, None)
    assert node["name"] == "handoff"
    assert [p for p, _ in posted] == ["/queue/calls"]
    body = posted[0][1]
    assert body["session_id"] == "s-q"
    assert body["outcome"] == "asked_for_person"
    assert body["flags"] == ["asked_for_person"]
    assert body["summary"]["summary"]


# ---- QH-9 agent drop and agent End (P3-5) -------------------------------------------


class AgentLeftHandoff(RecordingHandoff):
    def __init__(self):
        super().__init__()
        self.agent_left = []

    async def on_agent_left(self, session_id):
        self.agent_left.append(session_id)


async def _with_agent(transport, worker, roles):
    h = transport.handlers
    await h["on_first_participant_joined"](transport, _p("c1", "caller"))
    await h["on_participant_joined"](transport, _p("c1", "caller"))
    await h["on_participant_joined"](transport, _p("a1", "Agent Ana"))
    accepted = {"type": hold.MESSAGE_TYPE, "event": "agent_accepted", "agent_name": "Agent Ana"}
    await h["on_app_message"](transport, accepted, "api")
    assert roles.agent_id == "a1"


async def test_agent_dropping_is_reported_to_the_queue():
    transport, worker, h = FakeTransport(), FakeWorker(), AgentLeftHandoff()
    roles = CallRoles()
    bot.register_handlers(transport, worker, handoff=h, session_id="s1", roles=roles)
    await _with_agent(transport, worker, roles)
    await transport.handlers["on_participant_left"](transport, _p("a1", "Agent Ana"), "left")
    assert h.agent_left == ["s1"]
    assert not worker.cancelled


async def test_other_participant_leaving_is_not_an_agent_drop():
    transport, worker, h = FakeTransport(), FakeWorker(), AgentLeftHandoff()
    roles = CallRoles()
    bot.register_handlers(transport, worker, handoff=h, session_id="s1", roles=roles)
    await _with_agent(transport, worker, roles)
    await transport.handlers["on_participant_left"](transport, _p("x9", "Someone"), "left")
    assert h.agent_left == []


async def test_requeued_stops_listening_and_a_new_agent_can_take_over():
    transport, worker, h = FakeTransport(), FakeWorker(), AgentLeftHandoff()
    roles = CallRoles()
    bot.register_handlers(transport, worker, handoff=h, session_id="s1", roles=roles)
    await _with_agent(transport, worker, roles)
    hd = transport.handlers
    await hd["on_participant_left"](transport, _p("a1", "Agent Ana"), "left")
    requeued = {"type": hold.MESSAGE_TYPE, "event": "requeued", "position": 1}
    await hd["on_app_message"](transport, requeued, "api")
    assert not roles.listening  # intake hears the caller again
    assert worker.queued[-1].text == hold.REQUEUED_LINE
    await hd["on_participant_joined"](transport, _p("b1", "Agent Ben"))
    accepted = {"type": hold.MESSAGE_TYPE, "event": "agent_accepted", "agent_name": "Agent Ben"}
    await hd["on_app_message"](transport, accepted, "api")
    assert roles.listening and roles.agent_id == "b1"


async def test_call_ended_by_agent_ends_the_bot():
    transport, worker, h = FakeTransport(), FakeWorker(), AgentLeftHandoff()
    roles = CallRoles()
    ended = []

    async def on_ended():
        ended.append(True)

    bot.register_handlers(
        transport, worker, on_ended=on_ended, handoff=h, session_id="s1", roles=roles
    )
    await _with_agent(transport, worker, roles)
    msg = {"type": hold.MESSAGE_TYPE, "event": "call_ended"}
    await transport.handlers["on_app_message"](transport, msg, "api")
    # Goodbye, then a graceful end so the line plays out before the bot leaves.
    assert worker.queued[-2].text == hold.GOODBYE_LINE
    assert isinstance(worker.queued[-1], EndFrame)
    assert not worker.cancelled
    assert ended == [True]
    # The queue already knows; the bot does not report it again.
    assert h.ended == []
    # The room going away afterwards (caller ejected) stops the bot, but does not run
    # the end path twice.
    await transport.handlers["on_participant_left"](transport, _p("c1", "caller"), "ejected")
    assert worker.cancelled
    assert ended == [True]
    assert h.ended == []


# ---- P5-5: the call's settings shape the pipeline --------------------------------------


def _tts(worker):
    from pipecat.services.tts_service import TTSService

    inner = next(p for p in worker._pipeline.processors if type(p).__name__ == "Pipeline")
    return next(p for p in inner.processors if isinstance(p, TTSService))


def test_default_call_uses_flux_alexis(api_keys):
    """IN-11: Flux is the default voice (docs/spikes/tts-flux-vs-aura.md)."""
    from pipecat.services.deepgram.flux.tts import DeepgramFluxTTSService

    worker, _ = bot.build_worker(ROOM, "tok", store=MemoryCallStore())
    tts = _tts(worker)
    assert isinstance(tts, DeepgramFluxTTSService) and tts._settings.voice == "flux-alexis-en"


def test_aura_settings_put_the_aura_service_in_the_pipeline(api_keys):
    from agent_assist_bot.call_settings import CallSettings
    from pipecat.services.deepgram.tts import DeepgramTTSService

    s = CallSettings.from_mapping({"tts_model": "aura-2", "tts_voice": "aura-2-thalia-en"}, env={})
    worker, _ = bot.build_worker(ROOM, "tok", store=MemoryCallStore(), settings=s)
    tts = _tts(worker)
    assert type(tts) is DeepgramTTSService and tts._settings.voice == "aura-2-thalia-en"


def test_flux_settings_put_the_flux_service_in_the_pipeline(api_keys):
    from agent_assist_bot.call_settings import CallSettings
    from pipecat.services.deepgram.flux.tts import DeepgramFluxTTSService

    s = CallSettings.from_mapping({"tts_model": "flux", "tts_voice": "flux-alexis-en"}, env={})
    worker, _ = bot.build_worker(ROOM, "tok", store=MemoryCallStore(), settings=s)
    tts = _tts(worker)
    assert isinstance(tts, DeepgramFluxTTSService) and tts._settings.voice == "flux-alexis-en"
    # The greeting timer still sits right after the TTS, before the output (IN-1 log).
    # With slow read backs on (default) the pacer sits right before the TTS (P5-8).
    inner = next(p for p in worker._pipeline.processors if type(p).__name__ == "Pipeline")
    names = [type(p).__name__ for p in inner.processors]
    assert names[names.index("FluxTTSService") + 1] == "FirstAudioTimer"
    assert names[names.index("FluxTTSService") - 1] == "ReadbackPacer"


def test_aura_settings_have_no_read_back_pacer(api_keys):
    from agent_assist_bot.call_settings import CallSettings

    s = CallSettings.from_mapping({"tts_model": "aura-2", "tts_voice": "aura-2-thalia-en"}, env={})
    worker, _ = bot.build_worker(ROOM, "tok", store=MemoryCallStore(), settings=s)
    inner = next(p for p in worker._pipeline.processors if type(p).__name__ == "Pipeline")
    assert "ReadbackPacer" not in [type(p).__name__ for p in inner.processors]


async def test_settings_pick_the_pack_greeting_and_read_back(api_keys, monkeypatch):
    from agent_assist_bot.call_settings import CallSettings

    seen = {}
    real = bot.IntakeSession

    def spy(**kw):
        s = real(**kw)
        seen["session"] = s
        return s

    monkeypatch.setattr(bot, "IntakeSession", spy)
    greeting = "Hello. You're talking to an AI assistant, and a person will join after."
    s = CallSettings.from_mapping(
        {"intake_pack": "account_payments", "greeting_text": greeting, "identity_read_back": False},
        env={},
    )
    bot.build_worker(ROOM, "tok", store=MemoryCallStore(), settings=s)
    session = seen["session"]
    assert session.pack.id == "account_payments"
    assert session.greeting == greeting and session.identity_read_back is False


async def test_settings_set_the_hold_interval_and_line(api_keys, monkeypatch):
    from agent_assist_bot.call_settings import CallSettings

    made = {}
    real = bot.HoldController

    def spy(*a, **kw):
        made.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(bot, "HoldController", spy)
    s = CallSettings.from_mapping(
        {"hold_update_secs": 25, "hold_update_text": "Still here. {place}"}, env={}
    )
    bot.build_worker(ROOM, "tok", store=MemoryCallStore(), settings=s)
    assert made["interval"] == 25
    assert made["line"](2) == "Still here. You're number 2 in line."


async def test_run_bot_passes_the_settings_to_build_worker(monkeypatch):
    from agent_assist_bot.call_settings import CallSettings

    class FakeRunner:
        def __init__(self, **kwargs):
            pass

        async def add_workers(self, *workers):
            pass

        async def run(self):
            pass

    seen = {}

    def fake_build_worker(room_url, token=None, dialin=None, session_id="", **kwargs):
        seen.update(kwargs)
        return object(), None

    monkeypatch.setattr(bot, "WorkerRunner", FakeRunner)
    monkeypatch.setattr(bot, "build_worker", fake_build_worker)
    s = CallSettings.defaults({})
    await bot.run_bot(ROOM, "tok", None, session_id="s", settings=s)
    assert seen["settings"] is s


def test_flow_manager_uses_the_uninterruptible_tts_say(api_keys, monkeypatch):
    from agent_assist_bot import intake as intake_mod

    seen = {}
    real = bot.FlowManager

    def spy(**kw):
        fm = real(**kw)
        seen["fm"] = fm
        return fm

    monkeypatch.setattr(bot, "FlowManager", spy)
    bot.build_worker(ROOM, "tok", store=MemoryCallStore())
    handlers = seen["fm"]._action_manager._action_handlers
    assert handlers["tts_say"] is intake_mod.uninterruptible_tts_say


# ---- P5-9 (DC-11): the LLM for each job comes from the call's settings ------------------


def test_default_intake_llm_is_phonellm_on_deepgram(api_keys):
    """P5-9: phoneLLM mini is the intake default (docs/spikes/llm-deepgram.md)."""
    from agent_assist_bot.llm_deepgram import DeepgramLLMService

    worker, _ = bot.build_worker(ROOM, "tok", store=MemoryCallStore())
    inner = next(p for p in worker._pipeline.processors if type(p).__name__ == "Pipeline")
    llm = next(p for p in inner.processors if isinstance(p, DeepgramLLMService))
    assert llm._settings.model == "phonellm-mini-alpha-2-fp8"


def test_intake_llm_follows_the_settings(api_keys, monkeypatch):
    from agent_assist_bot.call_settings import CallSettings
    from agent_assist_bot.llm_deepgram import DeepgramLLMService

    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg")
    s = CallSettings.from_mapping({"llm_intake": "phonellm-mini-alpha-2-fp8"}, env={})
    worker, _ = bot.build_worker(ROOM, "tok", store=MemoryCallStore(), settings=s)
    inner = next(p for p in worker._pipeline.processors if type(p).__name__ == "Pipeline")
    llm = next(p for p in inner.processors if isinstance(p, DeepgramLLMService))
    assert llm._settings.model == "phonellm-mini-alpha-2-fp8"


def test_intake_llm_claude(api_keys):
    from agent_assist_bot.call_settings import CallSettings
    from pipecat.services.anthropic.llm import AnthropicLLMService

    s = CallSettings.from_mapping({"llm_intake": "claude-haiku-5-5"}, env={})
    worker, _ = bot.build_worker(ROOM, "tok", store=MemoryCallStore(), settings=s)
    inner = next(p for p in worker._pipeline.processors if type(p).__name__ == "Pipeline")
    llm = next(p for p in inner.processors if isinstance(p, AnthropicLLMService))
    assert llm._settings.model == "claude-haiku-5-5"


def test_assist_and_wrap_up_llms_follow_the_settings(api_keys, monkeypatch):
    """The assist proposer (and so the Ask bar, which uses it) and the wrap up drafter
    are built with the call's llm_assist and llm_wrapup."""
    from agent_assist_bot import assist_llm, wrap_up
    from agent_assist_bot.call_settings import CallSettings

    made = []

    def fake_create_llm(**kw):
        made.append(kw.get("model"))
        return object()

    monkeypatch.setattr(assist_llm, "create_llm", fake_create_llm)
    monkeypatch.setattr("agent_assist_bot.llm.create_llm", fake_create_llm)
    seen = {}
    real_assist = bot.AssistSession
    real_reporter = bot.WrapUpReporter

    def spy_assist(**kw):
        seen["assist"] = kw["llm_factory"]
        return real_assist(**kw)

    def spy_reporter(factory, post):
        seen["wrap_up"] = factory
        return real_reporter(factory, post)

    monkeypatch.setattr(bot, "AssistSession", spy_assist)
    monkeypatch.setattr(bot, "WrapUpReporter", spy_reporter)
    s = CallSettings.from_mapping(
        {"llm_assist": "think-fast", "llm_wrapup": "phonellm-mini-alpha-2-fp8"}, env={}
    )
    bot.build_worker(ROOM, "tok", store=MemoryCallStore(), settings=s)
    assert isinstance(seen["assist"](), assist_llm.LLMProposer)
    assert isinstance(seen["wrap_up"](), wrap_up.WrapUpDrafter)
    assert made == ["think-fast", "phonellm-mini-alpha-2-fp8"]
