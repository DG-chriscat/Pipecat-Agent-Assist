"""Warm bot mode (P5-2): preload, then wait for one call assignment on stdin."""

import io
import json

import pytest
from agent_assist_bot import __main__ as cli
from agent_assist_bot import warm

ROOM = "https://x.daily.co/r1"


def line(**kw) -> io.StringIO:
    return io.StringIO(json.dumps(kw) + "\n")


def test_reads_a_dialin_assignment():
    args = warm.read_assignment(
        line(room_url=ROOM, token="t1", session_id="s1", call_id="c1", call_domain="d1")
    )
    assert args == cli.DialinArgs(
        room_url=ROOM, token="t1", call_id="c1", call_domain="d1", session_id="s1"
    )


def test_reads_a_browser_assignment():
    args = warm.read_assignment(
        line(room_url=ROOM, token="t1", session_id="s1", call_id=None, call_domain=None)
    )
    assert args.call_id is None and args.dialin_config("k", "u") is None


def test_eof_means_no_call():
    assert warm.read_assignment(io.StringIO("")) is None


@pytest.mark.parametrize(
    "raw",
    ["not json\n", json.dumps({"token": "t"}) + "\n", json.dumps({"room_url": ROOM}) + "\n"],
)
def test_bad_assignment_is_rejected(raw):
    with pytest.raises(warm.AssignmentError):
        warm.read_assignment(io.StringIO(raw))


def test_bad_assignment_error_does_not_echo_the_token():
    raw = json.dumps({"token": "secret-tok", "call_id": "c1"}) + "\n"
    with pytest.raises(warm.AssignmentError) as e:
        warm.read_assignment(io.StringIO(raw))
    assert "secret-tok" not in str(e.value)


def test_preload_loads_vad_tone_packs_and_daily():
    calls = []
    steps = warm.preload(
        {},
        loaders={
            "vad": lambda: calls.append("vad"),
            "tone": lambda: calls.append("tone"),
            "packs": lambda: calls.append("packs"),
            "daily": lambda: calls.append("daily"),
        },
    )
    assert calls == ["vad", "tone", "packs", "daily"]
    assert set(steps) == {"vad", "tone", "packs", "daily"}


def test_default_loaders_cover_every_slow_first_load():
    """Measured: the Anthropic SDK import alone is ~3 s of the first build_worker."""
    assert list(warm.LOADERS) == ["llm", "vad", "tone", "packs", "daily"]


def test_llm_loader_imports_the_anthropic_service():
    warm.LOADERS["llm"]()
    import sys

    assert "anthropic" in sys.modules
    assert "agent_assist_bot.llm_anthropic" in sys.modules


def test_preload_skips_the_tone_model_when_tone_is_off():
    calls = []
    loaders = {k: (lambda k=k: calls.append(k)) for k in ("vad", "tone", "packs", "daily")}
    warm.preload({"TONE_READER": "0"}, loaders=loaders)
    assert "tone" not in calls


def test_preloaded_tone_model_is_the_one_the_reader_shares():
    """The reader's setup looks the model up under this key; same key, no second load."""
    from agent_assist_bot.tone import TONE_PRECISION, TONE_REVISION, TONE_THREADS

    assert warm.TONE_MODEL_KEY == ("reader", TONE_REVISION, TONE_PRECISION, TONE_THREADS)


def test_warm_main_prints_ready_then_runs_the_assigned_call(monkeypatch, capsys):
    ran = {}

    async def fake_run_bot(room_url, token, dialin, session_id=""):
        ran.update(room_url=room_url, token=token, dialin=dialin, session_id=session_id)

    monkeypatch.setattr(cli, "run_bot", fake_run_bot)
    monkeypatch.setattr(warm, "preload", lambda env=None, loaders=None: {})
    for k in ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(k, "k")
    monkeypatch.setattr(
        "sys.stdin",
        line(room_url=ROOM, token="t1", session_id="s1", call_id="c1", call_domain="d1"),
    )
    assert cli.main(["--warm"]) == 0
    assert warm.READY_LINE in capsys.readouterr().out.splitlines()
    assert ran["room_url"] == ROOM and ran["token"] == "t1" and ran["session_id"] == "s1"
    assert ran["dialin"].call_id == "c1"


def test_warm_main_exits_quietly_when_the_server_closes_stdin(monkeypatch):
    monkeypatch.setattr(warm, "preload", lambda env=None, loaders=None: {})
    for k in ("DAILY_API_KEY", "DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(k, "k")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert cli.main(["--warm"]) == 0


def test_ready_line_matches_the_server():
    from agent_assist_server import bot_launcher

    assert warm.READY_LINE == bot_launcher.READY_LINE
    assert bot_launcher.WARM_FLAG == "--warm"
