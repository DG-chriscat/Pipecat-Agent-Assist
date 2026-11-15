"""Bot wiring: greeting, dial-in option, handlers, CLI parsing. No network."""

import pytest
from agent_assist_bot import __main__ as cli
from agent_assist_bot import bot
from agent_assist_bot.calls import MemoryCallStore, PostgresCallStore
from agent_assist_bot.intake import IntakeSession
from agent_assist_bot.packs import load_pack
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


async def test_first_participant_joined_starts_intake_once():
    transport, worker = FakeTransport(), FakeWorker()
    started = []

    async def start():
        started.append(True)

    bot.register_handlers(transport, worker, on_caller_joined=start)
    await transport.handlers["on_first_participant_joined"](transport, {"id": "caller"})
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
    assert node["pre_actions"][0] == {"type": "tts_say", "text": bot.GREETING}
    assert node["respond_immediately"] is False


async def test_start_intake_goes_straight_from_greeting_to_identity():
    # The caller's first answer must land in the identity node, which has verify_identity.
    session = IntakeSession(
        pack=load_pack("claims_status"), store=MemoryCallStore(), session_id="s"
    )
    fm = FakeFlowManager()
    await bot.start_intake(session, fm)
    assert fm.nodes == ["greeting", "identity"]


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

    def fake_build_worker(room_url, token=None, dialin=None, session_id=""):
        seen.update(room_url=room_url, token=token, dialin=dialin, session_id=session_id)
        return sentinel, None

    monkeypatch.setattr(bot, "WorkerRunner", FakeRunner)
    monkeypatch.setattr(bot, "build_worker", fake_build_worker)
    await bot.run_bot(ROOM, "tok", DIALIN, session_id="sess-7")
    assert seen == {"room_url": ROOM, "token": "tok", "dialin": DIALIN, "session_id": "sess-7"}
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
        seen.update(room_url=room_url, token=token, dialin=dialin, session_id=session_id)

    monkeypatch.setenv("DAILY_ROOM_TOKEN", "tok")
    monkeypatch.setattr(cli, "run_bot", fake_run_bot)
    assert cli.main(["--room-url", ROOM, "--session-id", "s-1"]) == 0
    assert seen == {"room_url": ROOM, "token": "tok", "dialin": None, "session_id": "s-1"}


def test_cli_main_without_session_id_makes_one(monkeypatch, api_keys):
    seen = {}

    async def fake_run_bot(room_url, token=None, dialin=None, session_id=""):
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
