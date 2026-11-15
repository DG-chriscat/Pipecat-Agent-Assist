"""Dial-in bot: joins a Daily room, accepts the forwarded PSTN call, greets the caller.

Pipeline follows Pipecat's Daily dial-in example:
  Daily in -> Deepgram STT -> user context -> Anthropic LLM -> Deepgram TTS -> Daily out

The greeting is a fixed TTSSpeakFrame queued straight onto the pipeline, so the
first utterance needs only a TTS round trip (no LLM call) to hit the <2 s target.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from loguru import logger

GREETING = (
    "Hi, thanks for calling. You're speaking with an AI assistant. "
    "I'll ask a few quick questions, and then a person will join the call to help you."
)

SYSTEM_PROMPT = (
    "You are a friendly intake assistant on a phone call for a healthcare contact center. "
    "You are an AI, and a human agent will join shortly. Keep replies to one or two short "
    "sentences, suitable for speech. Do not use lists, markdown, or emojis."
)

BOT_NAME = "Agent Assist Bot"
REQUIRED_KEYS = ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY")


@dataclass(frozen=True)
class BotConfig:
    room_url: str
    token: str
    call_id: str
    call_domain: str
    session_id: str = ""
    daily_api_url: str = "https://api.daily.co/v1"


class MissingKeyError(RuntimeError):
    pass


def require_keys(env: dict[str, str] | None = None) -> dict[str, str]:
    """Return the required API keys from env. Error names missing keys, never values."""
    env = dict(os.environ) if env is None else env
    missing = [k for k in REQUIRED_KEYS if not env.get(k)]
    if missing:
        raise MissingKeyError(f"missing environment variables: {', '.join(missing)}")
    return {k: env[k] for k in REQUIRED_KEYS}


def greeting_frames():
    from pipecat.frames.frames import TTSSpeakFrame

    return [TTSSpeakFrame(GREETING)]


def build_transport(cfg: BotConfig, daily_api_key: str):
    from pipecat.transports.daily.transport import (
        DailyDialinSettings,
        DailyParams,
        DailyTransport,
    )

    params = DailyParams(
        api_url=cfg.daily_api_url,
        api_key=daily_api_key,
        dialin_settings=DailyDialinSettings(call_id=cfg.call_id, call_domain=cfg.call_domain),
        audio_in_enabled=True,
        audio_out_enabled=True,
        camera_out_enabled=False,
        transcription_enabled=False,
    )
    return DailyTransport(cfg.room_url, cfg.token, BOT_NAME, params)


def register_handlers(transport, task) -> None:
    """Greet as soon as the caller's leg joins; end the session when they hang up."""

    @transport.event_handler("on_dialin_ready")
    async def on_dialin_ready(transport, sip_endpoint):
        # DailyTransport already POSTs /dialin/pinlessCallUpdate to forward the call.
        logger.info("dial-in ready; Daily is forwarding the held call")

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant):
        logger.info("caller joined; queueing greeting")
        await task.queue_frames(greeting_frames())

    @transport.event_handler("on_participant_left")
    async def on_participant_left(transport, participant, reason):
        logger.info("caller left ({}); ending session", reason)
        await task.cancel()

    @transport.event_handler("on_dialin_error")
    async def on_dialin_error(transport, data):
        logger.error("dial-in error: {}", data)
        await task.cancel()


def build_task(cfg: BotConfig, keys: dict[str, str]):
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
    )
    from pipecat.services.anthropic.llm import AnthropicLLMService
    from pipecat.services.deepgram.stt import DeepgramSTTService
    from pipecat.services.deepgram.tts import DeepgramTTSService

    transport = build_transport(cfg, keys["DAILY_API_KEY"])
    stt = DeepgramSTTService(api_key=keys["DEEPGRAM_API_KEY"])
    tts = DeepgramTTSService(
        api_key=keys["DEEPGRAM_API_KEY"],
        settings=DeepgramTTSService.Settings(voice=os.getenv("DEEPGRAM_VOICE", "aura-2-helena-en")),
    )
    llm = AnthropicLLMService(
        api_key=keys["ANTHROPIC_API_KEY"],
        settings=AnthropicLLMService.Settings(
            model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            system_instruction=SYSTEM_PROMPT,
        ),
    )

    context = LLMContext()
    aggregators = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregators.user(),
            llm,
            tts,
            transport.output(),
            aggregators.assistant(),
        ]
    )
    task = PipelineWorker(pipeline, params=PipelineParams(enable_metrics=True))
    register_handlers(transport, task)
    return task


async def run_bot(cfg: BotConfig, keys: dict[str, str] | None = None) -> None:
    from pipecat.pipeline.runner import PipelineRunner

    keys = keys or require_keys()
    task = build_task(cfg, keys)
    logger.info("bot starting session={} room={}", cfg.session_id, cfg.room_url)
    runner = PipelineRunner(handle_sigint=False, handle_sigterm=True)
    await runner.add_workers(task)
    await runner.run()
