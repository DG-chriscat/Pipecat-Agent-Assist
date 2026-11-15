"""Intake bot: joins a Daily room as "Intake Bot", greets, listens, and replies.

Pipeline: Daily in -> Deepgram STT (Nova 3) -> user aggregator -> LLM -> Deepgram TTS
(Aura 2) -> Daily out -> assistant aggregator.

Two ways in, one bot:
- Spike S1 (`agent_assist_bot.spike_s1`): plain WebRTC room, greet the first participant.
- Daily pinless dial-in (`python -m agent_assist_bot`, started by the server webhook):
  pass a DialinConfig and the transport forwards the held PSTN call into the room
  (`/dialin/pinlessCallUpdate`); the caller is greeted when their leg joins.

The greeting is a fixed TTSSpeakFrame queued straight onto the pipeline, so the first
utterance needs only a TTS round trip (no LLM call) to hit the <2 s target (PRD IN-1).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import Frame, TranscriptionFrame, TTSSpeakFrame
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

from agent_assist_bot.llm import create_llm

BOT_NAME = "Intake Bot"
STT_MODEL = "nova-3-general"
TTS_VOICE = "aura-2-thalia-en"
DEFAULT_DAILY_API_URL = "https://api.daily.co/v1"
# PRD IN-2: tell the caller they are speaking with an AI assistant and a person will join.
GREETING = (
    "Hi, thanks for calling. You're speaking with an AI assistant. "
    "I'll ask a few quick questions, and then a person will join the call to help you."
)
SYSTEM_INSTRUCTION = (
    "You are the intake assistant for an insurance contact center. You are an AI, and a "
    "person will join the call shortly. You are speaking on a phone call, so reply in one "
    "or two short sentences of plain speech with no lists, markdown, or emoji. Ask one "
    "question at a time."
)
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


def greeting_frames() -> list[Frame]:
    return [TTSSpeakFrame(GREETING)]


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


def register_handlers(transport, worker) -> None:
    """Greet the first participant (the caller); end the session when they leave.

    The dial-in events only fire when the transport has dialin_settings.
    """

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant):
        name = participant.get("info", {}).get("userName", "")
        logger.info(f"[bot] first participant joined: {name!r} t={time.time():.3f}")
        await worker.queue_frames(greeting_frames())

    @transport.event_handler("on_participant_left")
    async def on_participant_left(transport, participant, reason):
        logger.info(f"[bot] participant left ({reason}); ending")
        await worker.cancel()

    @transport.event_handler("on_dialin_ready")
    async def on_dialin_ready(transport, sip_endpoint):
        # DailyTransport already POSTs /dialin/pinlessCallUpdate to forward the call.
        logger.info("[bot] dial-in ready; Daily is forwarding the held call")

    @transport.event_handler("on_dialin_error")
    async def on_dialin_error(transport, data):
        logger.error(f"[bot] dial-in error: {data}")
        await worker.cancel()


def build_worker(
    room_url: str, token: str | None = None, dialin: DialinConfig | None = None
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
    llm = create_llm(system_instruction=SYSTEM_INSTRUCTION)

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
    register_handlers(transport, worker)
    return worker, transport


async def run_bot(
    room_url: str, token: str | None = None, dialin: DialinConfig | None = None
) -> None:
    worker, _ = build_worker(room_url, token, dialin)
    # SIGTERM too: the server stops per-call bot processes with terminate().
    runner = WorkerRunner(handle_sigint=True, handle_sigterm=True)
    await runner.add_workers(worker)
    await runner.run()
