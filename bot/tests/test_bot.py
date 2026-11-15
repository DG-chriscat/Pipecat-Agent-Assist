"""Bot wiring: dial-in settings, greeting on join, CLI parsing. No network."""

import pytest
from agent_assist_bot import __main__ as cli
from agent_assist_bot import bot
from pipecat.frames.frames import TTSSpeakFrame

KEYS = {
    "DAILY_API_KEY": "fake-daily",
    "DEEPGRAM_API_KEY": "fake-dg",
    "ANTHROPIC_API_KEY": "fake-anthropic",
}
CFG = bot.BotConfig(
    room_url="https://dg-chriscat.daily.co/dialin-test",
    token="tok",
    call_id="call-123",
    call_domain="domain-456",
    session_id="sid",
)


class FakeTask:
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


def test_greeting_identifies_ai_and_human_handoff():
    # PRD IN-2
    assert "AI assistant" in bot.GREETING
    assert "person will join" in bot.GREETING


async def test_greeting_queued_on_first_participant_joined():
    transport, task = FakeTransport(), FakeTask()
    bot.register_handlers(transport, task)
    await transport.handlers["on_first_participant_joined"](transport, {"id": "caller"})
    assert len(task.queued) == 1
    frame = task.queued[0]
    assert isinstance(frame, TTSSpeakFrame)
    assert frame.text == bot.GREETING


async def test_greeting_is_tts_only_no_llm_frames():
    frames = bot.greeting_frames()
    assert [type(f) for f in frames] == [TTSSpeakFrame]


async def test_participant_left_ends_session():
    transport, task = FakeTransport(), FakeTask()
    bot.register_handlers(transport, task)
    await transport.handlers["on_participant_left"](transport, {"id": "caller"}, "hangup")
    assert task.cancelled


async def test_dialin_error_ends_session():
    transport, task = FakeTransport(), FakeTask()
    bot.register_handlers(transport, task)
    await transport.handlers["on_dialin_error"](transport, {"error": "x"})
    assert task.cancelled


def test_transport_carries_dialin_settings():
    transport = bot.build_transport(CFG, KEYS["DAILY_API_KEY"])
    params = transport._params
    assert params.dialin_settings.call_id == "call-123"
    assert params.dialin_settings.call_domain == "domain-456"
    assert params.api_key == "fake-daily"
    assert params.audio_in_enabled and params.audio_out_enabled
    assert params.camera_out_enabled is False


def test_build_task_registers_handlers():
    task = bot.build_task(CFG, KEYS)
    assert task is not None


async def test_run_bot_adds_worker_and_runs(monkeypatch):
    import pipecat.pipeline.runner as runner_mod

    calls = []

    class FakeRunner:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        async def add_workers(self, *workers):
            calls.append(("add", workers))

        async def run(self):
            calls.append(("run",))

    sentinel = object()
    monkeypatch.setattr(runner_mod, "PipelineRunner", FakeRunner)
    monkeypatch.setattr(bot, "build_task", lambda cfg, keys: sentinel)
    await bot.run_bot(CFG, KEYS)
    assert calls[1] == ("add", (sentinel,))
    assert calls[2] == ("run",)


def test_require_keys_reports_names_not_values():
    with pytest.raises(bot.MissingKeyError) as e:
        bot.require_keys({"DAILY_API_KEY": "secret-value"})
    msg = str(e.value)
    assert "DEEPGRAM_API_KEY" in msg and "ANTHROPIC_API_KEY" in msg
    assert "secret-value" not in msg
    assert bot.require_keys(KEYS) == KEYS


def test_cli_parse_args_reads_token_from_env():
    cfg = cli.parse_args(
        ["--room-url", "https://x.daily.co/r", "--call-id", "c", "--call-domain", "d"],
        env={"DAILY_ROOM_TOKEN": "tok"},
    )
    assert cfg.token == "tok"
    assert cfg.call_id == "c" and cfg.call_domain == "d"


def test_cli_requires_token():
    with pytest.raises(SystemExit):
        cli.parse_args(
            ["--room-url", "https://x.daily.co/r", "--call-id", "c", "--call-domain", "d"],
            env={},
        )
