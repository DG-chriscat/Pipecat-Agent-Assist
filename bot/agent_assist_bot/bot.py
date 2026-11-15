"""Spike S1 bot: joins a Daily room as "Intake Bot", greets, listens, and replies.

Pipeline: Daily in -> Deepgram STT (Nova 3) -> user aggregator -> LLM -> Deepgram TTS
(Aura 2) -> Daily out -> assistant aggregator.
"""

from __future__ import annotations

import os
import time

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
from pipecat.transports.daily.transport import DailyParams, DailyTransport
from pipecat.workers.runner import WorkerRunner

from agent_assist_bot.llm import create_llm

BOT_NAME = "Intake Bot"
STT_MODEL = "nova-3-general"
TTS_VOICE = "aura-2-thalia-en"
GREETING = "Hi, thanks for calling. This is the intake assistant. How can I help you today?"
SYSTEM_INSTRUCTION = (
    "You are the intake assistant for an insurance contact center. You are speaking on a "
    "phone call, so reply in one or two short sentences of plain speech with no lists, "
    "markdown, or emoji. Ask one question at a time."
)


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


def build_worker(room_url: str, token: str | None = None) -> tuple[PipelineWorker, DailyTransport]:
    transport = DailyTransport(
        room_url,
        token,
        BOT_NAME,
        DailyParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            camera_out_enabled=False,
        ),
    )
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

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant):
        name = participant.get("info", {}).get("userName", "")
        logger.info(f"[bot] first participant joined: {name!r} t={time.time():.3f}")
        await worker.queue_frames([TTSSpeakFrame(GREETING)])

    @transport.event_handler("on_participant_left")
    async def on_participant_left(transport, participant, reason):
        logger.info(f"[bot] participant left ({reason}); ending")
        await worker.cancel()

    return worker, transport


async def run_bot(room_url: str, token: str | None = None) -> None:
    worker, _ = build_worker(room_url, token)
    runner = WorkerRunner(handle_sigint=True)
    await runner.add_workers(worker)
    await runner.run()
