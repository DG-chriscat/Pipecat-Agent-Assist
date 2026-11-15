"""Bot wiring: greeting, dial-in option, handlers, CLI parsing. No network."""

import pytest
from agent_assist_bot import __main__ as cli
from agent_assist_bot import bot
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.worker import PipelineWorker

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


async def test_greeting_queued_on_first_participant_joined():
    transport, worker = FakeTransport(), FakeWorker()
    bot.register_handlers(transport, worker)
    await transport.handlers["on_first_participant_joined"](transport, {"id": "caller"})
    assert len(worker.queued) == 1
    frame = worker.queued[0]
    assert isinstance(frame, TTSSpeakFrame)
    assert frame.text == bot.GREETING


def test_greeting_is_tts_only_no_llm_frames():
    assert [type(f) for f in bot.greeting_frames()] == [TTSSpeakFrame]


async def test_participant_left_ends_session():
    transport, worker = FakeTransport(), FakeWorker()
    bot.register_handlers(transport, worker)
    await transport.handlers["on_participant_left"](transport, {"id": "caller"}, "hangup")
    assert worker.cancelled


async def test_dialin_ready_does_not_end_session():
    transport, worker = FakeTransport(), FakeWorker()
    bot.register_handlers(transport, worker)
    await transport.handlers["on_dialin_ready"](transport, "sip:x@daily")
    assert not worker.cancelled and not worker.queued


async def test_dialin_error_ends_session():
    transport, worker = FakeTransport(), FakeWorker()
    bot.register_handlers(transport, worker)
    await transport.handlers["on_dialin_error"](transport, {"error": "x"})
    assert worker.cancelled


def test_transport_without_dialin_has_no_dialin_settings():
    params = bot.build_transport(ROOM)._params
    assert params.dialin_settings is None
    assert params.audio_in_enabled and params.audio_out_enabled
    assert params.camera_out_enabled is False


def test_transport_carries_dialin_settings():
    params = bot.build_transport(ROOM, "tok", DIALIN)._params
    assert params.dialin_settings.call_id == "call-123"
    assert params.dialin_settings.call_domain == "domain-456"
    assert params.api_key == "fake-daily"
    assert params.api_url == bot.DEFAULT_DAILY_API_URL
    assert params.audio_in_enabled and params.audio_out_enabled
    assert params.camera_out_enabled is False


def test_build_worker_with_dialin(api_keys):
    worker, transport = bot.build_worker(ROOM, "tok", DIALIN)
    assert isinstance(worker, PipelineWorker)
    assert transport._params.dialin_settings.call_id == "call-123"


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

    def fake_build_worker(room_url, token=None, dialin=None, **kwargs):
        seen.update(room_url=room_url, token=token, dialin=dialin)
        return sentinel, None

    monkeypatch.setattr(bot, "WorkerRunner", FakeRunner)
    monkeypatch.setattr(bot, "build_worker", fake_build_worker)
    await bot.run_bot(ROOM, "tok", DIALIN)
    assert seen == {"room_url": ROOM, "token": "tok", "dialin": DIALIN}
    assert calls[0][1]["handle_sigterm"] is True
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

    async def fake_run_bot(room_url, token=None, dialin=None, session_id=""):
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

    async def fake_run_bot(room_url, token=None, dialin=None, session_id=""):
        seen.update(room_url=room_url, token=token, dialin=dialin)

    monkeypatch.setenv("DAILY_ROOM_TOKEN", "tok")
    monkeypatch.setattr(cli, "run_bot", fake_run_bot)
    assert cli.main(["--room-url", ROOM]) == 0
    assert seen == {"room_url": ROOM, "token": "tok", "dialin": None}


def test_cli_main_missing_keys_exits_2(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DAILY_ROOM_TOKEN", "tok")
    assert cli.main(["--room-url", ROOM, "--call-id", "c", "--call-domain", "d"]) == 2


# ---- queue hold and handoff wiring (QH-5, QH-6) -------------------------------------


class RecordingHandoff:
    def __init__(self):
        self.ended = []

    async def on_intake_complete(self, session_id, summary):
        pass

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

    worker, _ = bot.build_worker(ROOM, "tok")
    inner = next(p for p in worker._pipeline.processors if type(p).__name__ == "Pipeline")
    names = [type(p).__name__ for p in inner.processors]
    assert names.index(BotGate.__name__) == names.index("AnthropicLLMService") - 1


def test_cli_passes_session_id(monkeypatch, api_keys):
    seen = {}

    async def fake_run_bot(room_url, token=None, dialin=None, session_id=""):
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
