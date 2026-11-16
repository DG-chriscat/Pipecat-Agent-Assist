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
tracks, each line sent to the agent's portal only (P3-4, `transcript.py`).

The greeting is the flow's first node: a fixed `tts_say` pre-action with no LLM call,
so the first utterance needs only a TTS round trip to hit the <2 s target (PRD IN-1).

Env: DATABASE_URL (calls row + identity check; in memory when unset),
INTAKE_PACK (pack id, default claims_status).
"""

from __future__ import annotations

import os
import time
import uuid

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.flows import FlowManager
from pipecat.frames.frames import Frame, TranscriptionFrame
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

from agent_assist_bot.calls import CallStore, MemoryCallStore, PostgresCallStore
from agent_assist_bot.dialin import DEFAULT_DAILY_API_URL as DEFAULT_DAILY_API_URL
from agent_assist_bot.dialin import DialinConfig as DialinConfig
from agent_assist_bot.dialin import DialinForwarder, DialinForwardError, sip_uri_from_ready
from agent_assist_bot.handoff import end_call, handoff_from_env
from agent_assist_bot.hold import BotGate, HoldController, HoldState
from agent_assist_bot.intake import GREETING as GREETING  # re-export (PRD IN-2)
from agent_assist_bot.intake import ROLE_MESSAGE, HandoffHook, IntakeSession, NoopHandoff
from agent_assist_bot.llm import create_llm
from agent_assist_bot.packs import DEFAULT_PACK, Pack, load_pack
from agent_assist_bot.timing import FirstAudioTimer, log_timing
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


def build_transport(room_url: str, token: str | None = None) -> DailyTransport:
    """No `dialin_settings`: for dial-in calls the bot forwards the held call itself
    (see agent_assist_bot.dialin for why Pipecat's built-in forwarding is not used)."""
    params = DailyParams(
        audio_in_enabled=True,
        audio_in_user_tracks=True,  # one audio stream per participant (S2, P3-4)
        audio_out_enabled=True,
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
    """
    hold = hold or HoldController(worker.queue_frames)
    roles = roles or CallRoles()
    handoff = handoff or NoopHandoff()
    if dialin is not None and forwarder is None:
        forwarder = DialinForwarder(dialin)
    # The first participant is the caller. Others (the agent after accept) may come and
    # go; only the caller leaving ends the call.
    caller: dict[str, str | None] = {"id": None}

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

    @transport.event_handler("on_app_message")
    async def on_app_message(transport, message, sender):
        await hold.on_app_message(message, sender)
        if hold.state is HoldState.WITH_AGENT and not roles.listening:
            roles.agent_accepted(hold.agent_name)
            logger.info("[bot] agent accepted: listen only, live transcript to the agent")

    @transport.event_handler("on_participant_left")
    async def on_participant_left(transport, participant, reason):
        roles.left(participant.get("id"))
        if caller["id"] is not None and participant.get("id") != caller["id"]:
            name = participant.get("info", {}).get("userName", "")
            logger.info(f"[bot] {name!r} left ({reason}); caller still on the line")
            return
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


def listen_branch(role: str, roles: CallRoles, on_final) -> list[FrameProcessor]:
    """One role's live transcript (S2): its own audio, VAD, and Deepgram stream."""
    return [
        AudioRoute(role, roles),
        VADProcessor(vad_analyzer=SileroVADAnalyzer()),
        DeepgramSTTService(
            api_key=os.environ["DEEPGRAM_API_KEY"],
            settings=DeepgramSTTService.Settings(model=STT_MODEL),
        ),
        TranscriptRelay(role, roles, on_final=on_final),
    ]


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
    transport = build_transport(room_url, token)
    stt = DeepgramSTTService(
        api_key=os.environ["DEEPGRAM_API_KEY"],
        settings=DeepgramSTTService.Settings(model=STT_MODEL),
    )
    tts = DeepgramTTSService(
        api_key=os.environ["DEEPGRAM_API_KEY"],
        settings=DeepgramTTSService.Settings(voice=TTS_VOICE),
    )
    llm = create_llm(system_instruction=ROLE_MESSAGE)

    context = LLMContext()
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    store = store or call_store_from_env()
    session = IntakeSession(
        pack=pack or load_pack(os.environ.get(PACK_ENV) or DEFAULT_PACK),
        store=store,
        session_id=session_id,
        handoff=handoff,
    )

    # After an agent accepts, the gate stops caller turns reaching the LLM (QH-6),
    # intake hears nothing more, and the listen branches transcribe both people.
    gate = BotGate()
    roles = CallRoles()
    recorder = TranscriptRecorder(store, lambda: session.call_id)
    listen = [listen_branch(role, roles, recorder.add) for role in ROLES]
    pipeline = Pipeline(
        [
            transport.input(),
            ParallelPipeline(
                [AudioRoute(None, roles), stt, TurnLogger("caller", TranscriptionFrame)],
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
        # their lines to the agent only.
        rtvi_observer_params=RTVIObserverParams(
            ignored_sources=[p for branch in listen for p in branch]
        ),
    )

    # Intake (PRD IN-3 to IN-9): a Pipecat Flow driven by the use case pack. When it
    # ends (completed, request_person, or emergency) the hook enqueues the call.
    flow_manager = FlowManager(
        llm=llm, context_aggregator=aggregators, worker=worker, transport=transport
    )

    async def on_caller_joined() -> None:
        await start_intake(session, flow_manager)

    async def on_ended() -> None:
        await recorder.close()
        await session.store.close()

    register_handlers(
        transport,
        worker,
        on_caller_joined=on_caller_joined,
        on_ended=on_ended,
        hold=HoldController(worker.queue_frames, gate=gate),
        handoff=handoff,
        session_id=session_id,
        dialin=dialin,
        roles=roles,
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
