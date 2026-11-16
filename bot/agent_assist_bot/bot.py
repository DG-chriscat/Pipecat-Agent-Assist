"""Intake bot: joins a Daily room as "Vitals Assistant" and runs the intake flow.

Pipeline: Daily in -> Deepgram STT (Nova 3) -> user aggregator -> LLM -> Deepgram TTS
(Aura 2) -> Daily out -> assistant aggregator. A Pipecat Flow (`intake.py`) drives the
LLM through the intake steps from the use case pack (PRD IN-3, IN-4) and writes each
answer to the `calls` row (IN-5).

Two ways in, one bot:
- Spike S1 (`agent_assist_bot.spike_s1`): plain WebRTC room, greet the first participant.
- Daily pinless dial-in (`python -m agent_assist_bot`, started by the server webhook):
  pass a DialinConfig and the transport forwards the held PSTN call into the room
  (`/dialin/pinlessCallUpdate`); the caller is greeted when their leg joins.
- Browser caller (PRD IN-12, `POST /calls/browser`): same CLI without dial-in args, so
  no DialinConfig; the caller is greeted when their browser joins.

After an agent accepts (QH-6) the bot says "Connecting you now." and goes listen only:
intake hears nothing more and the caller and the agent are transcribed on their own
tracks, each line sent to the agent's portal only (P3-4, `transcript.py`). The assist
(P4-3, `assist.py`) turns those lines into ranked cards for the agent's portal only.

The greeting is the flow's first node: a fixed `tts_say` pre-action with no LLM call,
so the first utterance needs only a TTS round trip to hit the <2 s target (PRD IN-1).

Env: DATABASE_URL (calls row + identity check; in memory when unset),
INTAKE_PACK (pack id, default claims_status), HOLD_MUSIC / HOLD_MUSIC_VOLUME /
HOLD_MUSIC_PATH (hold music while queued, QH-5, `hold_music.py`).
"""

from __future__ import annotations

import os
import time
import uuid

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.flows import FlowManager
from pipecat.frames.frames import EndFrame, Frame, TranscriptionFrame
from pipecat.pipeline.parallel_pipeline import ParallelPipeline
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIObserverParams
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.transports.daily.transport import DailyParams, DailyTransport
from pipecat.workers.runner import WorkerRunner

from agent_assist_bot.assist import LLM_DEBOUNCE_SECS
from agent_assist_bot.assist_llm import LLMProposer
from agent_assist_bot.assist_runtime import AssistSession
from agent_assist_bot.calls import CallStore, MemoryCallStore, PostgresCallStore
from agent_assist_bot.dialin import DEFAULT_DAILY_API_URL as DEFAULT_DAILY_API_URL
from agent_assist_bot.dialin import DialinConfig as DialinConfig
from agent_assist_bot.dialin import DialinForwarder, DialinForwardError, sip_uri_from_ready
from agent_assist_bot.handoff import agent_left, end_call, handoff_from_env
from agent_assist_bot.hold import BotGate, HoldController, HoldState, hold_interval_from_env
from agent_assist_bot.hold_music import HoldMusicMixer, hold_music_from_env
from agent_assist_bot.intake import GREETING as GREETING  # re-export (PRD IN-2)
from agent_assist_bot.intake import ROLE_MESSAGE, HandoffHook, IntakeSession, NoopHandoff
from agent_assist_bot.llm import create_llm
from agent_assist_bot.packs import DEFAULT_PACK, MAX_KEYTERMS, Pack, load_pack, load_packs
from agent_assist_bot.stt_tuning import IdentitySTTTuning, build_intake_stt
from agent_assist_bot.timing import FirstAudioTimer, log_timing
from agent_assist_bot.tone import IntakeToneTap, MoodTrend, tone_reader_from_env
from agent_assist_bot.transcript import (
    ROLES,
    AudioRoute,
    CallRoles,
    TranscriptRecorder,
    TranscriptRelay,
)

BOT_NAME = "Vitals Assistant"
STT_MODEL = "nova-3-general"
TTS_VOICE = "aura-2-thalia-en"
PACK_ENV = "INTAKE_PACK"
REQUIRED_KEYS = ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY")


class MissingKeyError(RuntimeError):
    pass


def require_keys(env: dict[str, str] | None = None) -> dict[str, str]:
    """Return the required API keys from env. Error names missing keys, never values."""
    env = dict(os.environ) if env is None else env
    missing = [k for k in REQUIRED_KEYS if not env.get(k)]
    if missing:
        raise MissingKeyError(f"missing environment variables: {', '.join(missing)}")
    return {k: env[k] for k in REQUIRED_KEYS}


class TurnLogger(FrameProcessor):
    """Logs final user transcripts with a timestamp, passing every frame through."""

    def __init__(self, label: str, frame_type: type[Frame]):
        super().__init__()
        self._label = label
        self._frame_type = frame_type

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, self._frame_type) and getattr(frame, "text", "").strip():
            logger.info(f"[{self._label}] t={time.time():.3f} {frame.text!r}")
        await self.push_frame(frame, direction)


def build_transport(
    room_url: str, token: str | None = None, *, mixer: HoldMusicMixer | None = None
) -> DailyTransport:
    """No `dialin_settings`: for dial-in calls the bot forwards the held call itself
    (see agent_assist_bot.dialin for why Pipecat's built-in forwarding is not used).

    `mixer` is the hold music (QH-5); it is silent until the call is queued."""
    params = DailyParams(
        audio_in_enabled=True,
        audio_in_user_tracks=True,  # one audio stream per participant (S2, P3-4)
        audio_out_enabled=True,
        audio_out_mixer=mixer,
        camera_out_enabled=False,
    )
    return DailyTransport(room_url, token, BOT_NAME, params)


def call_store_from_env(env: dict[str, str] | None = None) -> CallStore:
    """Postgres when DATABASE_URL is set (ADR 0002), otherwise in memory (logged)."""
    store = PostgresCallStore.from_env(env)
    if store is None:
        logger.warning("[bot] DATABASE_URL not set: call record kept in memory only")
        return MemoryCallStore()
    return store


async def start_intake(session: IntakeSession, flow_manager) -> None:
    """Speak the greeting, create the calls row, then enter identity.

    The greeting node is TTS only (no LLM call) and is queued before the calls row is
    written: the insert is a DB round trip (1.3 s on the Nov 15 live dial-in call) that
    the caller should not wait through in silence (PRD IN-1). Identity follows at once,
    so its question is generated while the greeting plays and is spoken right after it,
    and the caller's first answer lands in a node that offers verify_identity. Function
    handlers that write to the calls row only run after the caller speaks, by which
    time the row exists.
    """
    await flow_manager.initialize(session.greeting_node())
    try:
        await session.start()
    except Exception as e:  # noqa: BLE001 - the caller still gets intake
        logger.error(f"[bot] could not create call record: {type(e).__name__}: {e}")
        session.store = MemoryCallStore()
        await session.start()
    # Stall guards (intake.py): the 3 minute intake budget starts with the greeting.
    session.start_watchdog(flow_manager)
    await flow_manager.set_node_from_config(session.identity_node())


def register_handlers(
    transport,
    worker,
    on_caller_joined=None,
    on_ended=None,
    *,
    hold: HoldController | None = None,
    handoff: HandoffHook | None = None,
    session_id: str = "",
    dialin: DialinConfig | None = None,
    forwarder: DialinForwarder | None = None,
    roles: CallRoles | None = None,
    assist=None,
) -> None:
    """Start intake for the first participant (the caller); end when they leave.

    Queue app messages from the server go to `hold` (QH-5, QH-6). When the caller
    leaves, `handoff.on_call_ended` (QueueHandoff only) tells the queue so a waiting
    or ringing call is cleared. For a dial-in call (`dialin` set) the bot warms a
    Daily API connection on join and forwards the held call into the room when Daily
    fires dialin-ready. `[timing]` lines mark the call timeline for the IN-1 test.

    `roles` (P3-4) learns who the caller and the agent are: the caller is the first
    participant; on the server's agent_accepted message the agent is whoever joined
    with the accepting agent's name, and the bot switches to listen only.

    `assist` (P4-3, AssistSession) starts on accept, gets every app message (it
    keeps only the agent's feedback), and resends its state when the agent joins.
    """
    hold = hold or HoldController(worker.queue_frames)
    roles = roles or CallRoles()
    handoff = handoff or NoopHandoff()
    if dialin is not None and forwarder is None:
        forwarder = DialinForwarder(dialin)
    # The first participant is the caller. Others (the agent after accept) may come and
    # go; only the caller leaving ends the call.
    caller: dict[str, str | None] = {"id": None}
    # Set once the call is over (caller left, or the agent pressed End), so the end
    # path runs once.
    done = {"ended": False}

    @transport.event_handler("on_joined")
    async def on_joined(transport, data):
        log_timing("bot_joined", session=session_id)
        if forwarder is not None:
            await forwarder.warm()

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant):
        name = participant.get("info", {}).get("userName", "")
        caller["id"] = participant.get("id")
        roles.set_caller(participant["id"])
        log_timing("caller_joined", session=session_id)
        logger.info(f"[bot] first participant joined: {name!r}")
        if on_caller_joined is not None:
            await on_caller_joined()

    @transport.event_handler("on_participant_joined")
    async def on_participant_joined(transport, participant):
        roles.joined(participant["id"], participant.get("info", {}).get("userName"))
        if assist is not None:
            await assist.on_participant_joined(participant["id"])

    @transport.event_handler("on_app_message")
    async def on_app_message(transport, message, sender):
        await hold.on_app_message(message, sender)
        if hold.state is HoldState.WITH_AGENT and not roles.listening:
            roles.agent_accepted(hold.agent_name)
            logger.info("[bot] agent accepted: listen only, live transcript to the agent")
            if assist is not None:
                # Once per call; after a QH-9 drop the next agent's join resyncs it.
                await assist.on_accept()
        elif hold.state is HoldState.QUEUED and roles.listening:
            # QH-9: the agent dropped off; intake hears the caller again.
            roles.agent_dropped()
            logger.info("[bot] agent dropped: back in the queue, the bot speaks again")
        elif hold.state is HoldState.ENDED and not done["ended"]:
            # P3-5: the agent pressed End. Goodbye is queued; end once it has played.
            done["ended"] = True
            logger.info("[bot] the agent ended the call; leaving after goodbye")
            await worker.queue_frames([EndFrame()])
            if on_ended is not None:
                await on_ended()
        elif assist is not None:
            await assist.on_app_message(message, sender)

    @transport.event_handler("on_participant_left")
    async def on_participant_left(transport, participant, reason):
        pid = participant.get("id")
        was_agent = pid is not None and pid == roles.agent_id
        roles.left(pid)
        if caller["id"] is not None and pid != caller["id"]:
            name = participant.get("info", {}).get("userName", "")
            logger.info(f"[bot] {name!r} left ({reason}); caller still on the line")
            if was_agent and hold.state is HoldState.WITH_AGENT and session_id:
                await agent_left(handoff, session_id)
            return
        if done["ended"]:
            # The agent already ended the call and the queue knows. Make sure the bot
            # stops even if the goodbye EndFrame has not finished yet.
            await worker.cancel()
            return
        done["ended"] = True
        logger.info(f"[bot] caller left ({reason}); ending")
        hold.stop()
        if session_id:
            await end_call(handoff, session_id)
        await worker.cancel()
        if on_ended is not None:
            await on_ended()

    @transport.event_handler("on_dialin_ready")
    async def on_dialin_ready(transport, data):
        log_timing("dialin_ready", session=session_id)
        if forwarder is None:
            return
        sip_uri = sip_uri_from_ready(data)
        if sip_uri is None:
            logger.error(f"[bot] dial-in ready without a SIP endpoint: {data!r}; ending")
            await worker.cancel()
            return
        try:
            await forwarder.forward(sip_uri)
        except DialinForwardError as e:
            logger.error(f"[bot] could not forward the held call: {e}; ending")
            await worker.cancel()
            return
        finally:
            await forwarder.close()
        log_timing("call_forwarded", session=session_id)

    @transport.event_handler("on_dialin_connected")
    async def on_dialin_connected(transport, data):
        log_timing("dialin_connected", session=session_id)

    @transport.event_handler("on_dialin_error")
    async def on_dialin_error(transport, data):
        logger.error(f"[bot] dial-in error: {data}")
        await worker.cancel()


def listen_keyterms(packs) -> list[str]:
    """Every pack's fixed STT keyterms for the listen branches, deduped, max 100.

    The listen branches run before anyone knows which pack the conversation will
    land on, so they boost all packs' terms (never per member).
    """
    seen: set[str] = set()
    out: list[str] = []
    for p in packs.values():
        for t in p.keyterms:
            if t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
    return out[:MAX_KEYTERMS]


class FanOut:
    """One `on_final` callback that calls each target in order."""

    def __init__(self, *targets):
        self.targets = targets

    def __call__(self, data) -> None:
        for t in self.targets:
            t(data)


def listen_branch(
    role: str,
    roles: CallRoles,
    on_final,
    keyterms: list[str] | None = None,
    reader: FrameProcessor | None = None,
) -> list[FrameProcessor]:
    """One role's live transcript (S2): its own audio, VAD, and Deepgram stream.
    `reader` (the caller's tone reader, P4-5) goes right after the STT."""
    return [
        AudioRoute(role, roles),
        VADProcessor(vad_analyzer=SileroVADAnalyzer()),
        DeepgramSTTService(
            api_key=os.environ["DEEPGRAM_API_KEY"],
            settings=DeepgramSTTService.Settings(model=STT_MODEL, keyterm=keyterms or None),
        ),
        *([reader] if reader is not None else []),
        TranscriptRelay(role, roles, on_final=on_final),
    ]


class PreviousLine:
    """Gives the caller's listen reader the agent's last line as the turn before."""

    def __init__(self, reader) -> None:
        self._reader = reader

    def __call__(self, data) -> None:
        if data.get("role") == "agent":
            self._reader.set_previous(data["text"])


def build_worker(
    room_url: str,
    token: str | None = None,
    dialin: DialinConfig | None = None,
    session_id: str = "",
    *,
    pack: Pack | None = None,
    store: CallStore | None = None,
    handoff: HandoffHook | None = None,
) -> tuple[PipelineWorker, DailyTransport]:
    """`handoff` defaults to handoff_from_env(): QueueHandoff when
    AGENT_ASSIST_SERVER_URL is set, else NoopHandoff."""
    session_id = session_id or str(uuid.uuid4())
    handoff = handoff or handoff_from_env()
    music = hold_music_from_env()
    transport = build_transport(room_url, token, mixer=music)
    pack = pack or load_pack(os.environ.get(PACK_ENV) or DEFAULT_PACK)
    # Intake STT: the pack's fixed keyterms; identity step tuning in stt_tuning.py.
    stt = build_intake_stt(os.environ["DEEPGRAM_API_KEY"], STT_MODEL, pack.keyterms)
    tts = DeepgramTTSService(
        api_key=os.environ["DEEPGRAM_API_KEY"],
        settings=DeepgramTTSService.Settings(voice=TTS_VOICE),
    )
    llm = create_llm(system_instruction=ROLE_MESSAGE)

    context = LLMContext()
    vad = SileroVADAnalyzer()
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=vad),
    )

    store = store or call_store_from_env()
    session = IntakeSession(
        pack=pack,
        store=store,
        session_id=session_id,
        handoff=handoff,
    )

    # After an agent accepts, the gate stops caller turns reaching the LLM (QH-6),
    # intake hears nothing more, and the listen branches transcribe both people.
    # Each final line is saved for wrap up and fed to the assist (P4-3).
    gate = BotGate()
    roles = CallRoles()
    recorder = TranscriptRecorder(store, lambda: session.call_id)
    packs = load_packs()
    # Caller tone (P4-5, IN-10, AP-14): one reader on the intake STT and one on the
    # caller's listen STT (they share one loaded model); none on the agent. Off with
    # TONE_READER=0. Intake readings go into the trend the assist starts from.
    trend = MoodTrend()
    intake_reader = tone_reader_from_env(name="CallerToneReader[intake]")
    caller_reader = tone_reader_from_env(name="CallerToneReader[caller]")
    assist = AssistSession(
        packs=packs,
        roles=roles,
        output=transport.output(),
        store=store,
        intake=session,
        llm_factory=LLMProposer.from_env,
        llm_debounce_secs=LLM_DEBOUNCE_SECS,
        trend=trend,
    )
    targets = [recorder.add, assist.on_final]
    if caller_reader is not None:
        targets.append(PreviousLine(caller_reader))
    on_final = FanOut(*targets)
    keyterms = listen_keyterms(packs)
    listen = [
        listen_branch(role, roles, on_final, keyterms, caller_reader if role == "caller" else None)
        for role in ROLES
    ]
    intake_tone = (
        [intake_reader, IntakeToneTap(trend, on_final=recorder.add)]
        if intake_reader is not None
        else []
    )
    pipeline = Pipeline(
        [
            transport.input(),
            ParallelPipeline(
                [
                    AudioRoute(None, roles),
                    stt,
                    *intake_tone,
                    TurnLogger("caller", TranscriptionFrame),
                ],
                *listen,
            ),
            aggregators.user(),
            gate,
            llm,
            tts,
            FirstAudioTimer(session_id),
            transport.output(),
            aggregators.assistant(),
        ]
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True),
        idle_timeout_secs=120,
        # RTVI's own transcription messages go to everyone in the room, the caller
        # included. The listen branches are invisible to it; TranscriptRelay sends
        # their lines to the agent only. user-input messages are off: they would carry
        # the intake tone reading (perception) to the caller (P4-5).
        rtvi_observer_params=RTVIObserverParams(
            ignored_sources=[p for branch in listen for p in branch],
            user_input_enabled=False,
        ),
    )

    # Intake (PRD IN-3 to IN-9): a Pipecat Flow driven by the use case pack. When it
    # ends (completed, request_person, or emergency) the hook enqueues the call.
    session.identity_tuning = IdentitySTTTuning(stt, vad, worker.queue_frames)
    flow_manager = FlowManager(
        llm=llm, context_aggregator=aggregators, worker=worker, transport=transport
    )

    async def on_caller_joined() -> None:
        await start_intake(session, flow_manager)

    # Every caller turn feeds the intake stall guards (affirmative fallback, watchdog).
    @aggregators.user().event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(aggregator, strategy, message) -> None:
        if not roles.listening:
            await session.on_caller_turn(message.content or "")

    @aggregators.assistant().event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(aggregator, message) -> None:
        if message.content:
            session.on_bot_turn()

    async def on_ended() -> None:
        await session.close()
        await assist.close()
        await recorder.close()
        await session.store.close()

    register_handlers(
        transport,
        worker,
        on_caller_joined=on_caller_joined,
        on_ended=on_ended,
        hold=HoldController(
            worker.queue_frames, gate=gate, interval=hold_interval_from_env(), music=music
        ),
        handoff=handoff,
        session_id=session_id,
        dialin=dialin,
        roles=roles,
        assist=assist,
    )
    return worker, transport


async def run_bot(
    room_url: str,
    token: str | None = None,
    dialin: DialinConfig | None = None,
    session_id: str = "",
) -> None:
    worker, _ = build_worker(
        room_url, token, dialin, session_id=session_id, handoff=handoff_from_env()
    )
    # SIGTERM too: the server stops per-call bot processes with terminate().
    runner = WorkerRunner(handle_sigint=True, handle_sigterm=True)
    await runner.add_workers(worker)
    await runner.run()
