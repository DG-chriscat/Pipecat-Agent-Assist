from agent_assist_bot.bot import BOT_NAME, build_worker
from pipecat.pipeline.worker import PipelineWorker
from pipecat.transports.daily.transport import DailyTransport


def test_build_worker_wires_pipeline_without_network(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "an-test")

    worker, transport = build_worker("https://dg-chriscat.daily.co/unit-test")

    assert isinstance(worker, PipelineWorker)
    assert isinstance(transport, DailyTransport)
    assert BOT_NAME == "Vitals Assistant"
