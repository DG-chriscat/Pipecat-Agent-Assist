"""Intake bot: joins a Daily room as "Intake Bot" and runs the intake flow.

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

The greeting is the flow's first node: a fixed `tts_say` pre-action with no LLM call,
so the first utterance needs only a TTS round trip to hit the <2 s target (PRD IN-1).

Env: DATABASE_URL (calls row + identity check; in memory when unset),
INTAKE_PACK (pack id, default claims_status).
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.flows import FlowManager
from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.transports.daily.transport import (
    DailyDialinSettings,
    DailyParams,
    DailyTransport,
)
from pipecat.workers.runner import WorkerRunner

from agent_assist_bot.calls import CallStore, MemoryCallStore, PostgresCallStore
from agent_assist_bot.intake import GREETING as GREETING  # re-export (PRD IN-2)
from agent_assist_bot.intake import ROLE_MESSAGE, HandoffHook, IntakeSession
from agent_assist_bot.llm import create_llm
from agent_assist_bot.packs import DEFAULT_PACK, Pack, load_pack

BOT_NAME = "Intake Bot"
STT_MODEL = "nova-3-general"
TTS_VOICE = "aura-2-thalia-en"
DEFAULT_DAILY_API_URL = "https://api.daily.co/v1"
PACK_ENV = "INTAKE_PACK"
REQUIRED_KEYS = ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY")


@dataclass(frozen=True)
class DialinConfig:
    """Pinless dial-in call identifiers from Daily's webhook, plus the REST creds the
    transport needs to forward the call into the room."""

    call_id: str
    call_domain: str
    api_key: str
    api_url: str = DEFAULT_DAILY_API_URL


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
    room_url: str, token: str | None = None, dialin: DialinConfig | None = None
) -> DailyTransport:
    dialin_kwargs = {}
    if dialin is not None:
        dialin_kwargs = {
            "api_url": dialin.api_url,
            "api_key": dialin.api_key,
            "dialin_settings": DailyDialinSettings(
                call_id=dialin.call_id, call_domain=dialin.call_domain
            ),
        }
    params = DailyParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        camera_out_enabled=False,
        **dialin_kwargs,
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
    """Create the calls row, speak the greeting, then enter identity.

    The greeting node is TTS only (no LLM call). Identity follows at once, so its
    question is generated while the greeting plays and is spoken right after it, and
    the caller's first answer lands in a node that offers verify_identity.
    """
    try:
        await session.start()
    except Exception as e:  # noqa: BLE001 - the caller still gets intake
        logger.error(f"[bot] could not create call record: {type(e).__name__}: {e}")
        session.store = MemoryCallStore()
        await session.start()
    await flow_manager.initialize(session.greeting_node())
    await flow_manager.set_node_from_config(session.identity_node())


def register_handlers(transport, worker, on_caller_joined=None, on_ended=None) -> None:
    """Start intake for the first participant (the caller); end when they leave.

    The dial-in events only fire when the transport has dialin_settings.
    """

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant):
        name = participant.get("info", {}).get("userName", "")
        logger.info(f"[bot] first participant joined: {name!r} t={time.time():.3f}")
        if on_caller_joined is not None:
            await on_caller_joined()

    @transport.event_handler("on_participant_left")
    async def on_participant_left(transport, participant, reason):
        logger.info(f"[bot] participant left ({reason}); ending")
        await worker.cancel()
        if on_ended is not None:
            await on_ended()

    @transport.event_handler("on_dialin_ready")
    async def on_dialin_ready(transport, sip_endpoint):
        # DailyTransport already POSTs /dialin/pinlessCallUpdate to forward the call.
        logger.info("[bot] dial-in ready; Daily is forwarding the held call")

    @transport.event_handler("on_dialin_error")
    async def on_dialin_error(transport, data):
        logger.error(f"[bot] dial-in error: {data}")
        await worker.cancel()


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
    transport = build_transport(room_url, token, dialin)
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

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            TurnLogger("caller", TranscriptionFrame),
            aggregators.user(),
            llm,
            tts,
            transport.output(),
            aggregators.assistant(),
        ]
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True),
        idle_timeout_secs=120,
    )

    # Intake (PRD IN-3 to IN-9): a Pipecat Flow driven by the use case pack.
    session = IntakeSession(
        pack=pack or load_pack(os.environ.get(PACK_ENV) or DEFAULT_PACK),
        store=store or call_store_from_env(),
        session_id=session_id or str(uuid.uuid4()),
        handoff=handoff,
    )
    flow_manager = FlowManager(
        llm=llm, context_aggregator=aggregators, worker=worker, transport=transport
    )

    async def on_caller_joined() -> None:
        await start_intake(session, flow_manager)

    async def on_ended() -> None:
        await session.store.close()

    register_handlers(transport, worker, on_caller_joined=on_caller_joined, on_ended=on_ended)
    return worker, transport


async def run_bot(
    room_url: str,
    token: str | None = None,
    dialin: DialinConfig | None = None,
    session_id: str = "",
) -> None:
    worker, _ = build_worker(room_url, token, dialin, session_id=session_id)
    # SIGTERM too: the server stops per-call bot processes with terminate().
    runner = WorkerRunner(handle_sigint=True, handle_sigterm=True)
    await runner.add_workers(worker)
    await runner.run()
