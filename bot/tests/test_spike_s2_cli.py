import pytest
from agent_assist_bot import spike_s2


async def test_runs_listen_bot_on_given_room(monkeypatch, capsys):
    ran = []

    async def fake_run(url, token=None, *, model=None):
        ran.append((url, model))

    monkeypatch.setattr(spike_s2, "run_listen_bot", fake_run)

    rc = await spike_s2.amain(["--room-url", "https://dg-chriscat.daily.co/s2"])

    assert rc == 0
    assert ran == [("https://dg-chriscat.daily.co/s2", "nova-3-general")]
    assert "https://dg-chriscat.daily.co/s2" in capsys.readouterr().out


async def test_stt_model_flag(monkeypatch):
    ran = []

    async def fake_run(url, token=None, *, model=None):
        ran.append(model)

    monkeypatch.setattr(spike_s2, "run_listen_bot", fake_run)
    await spike_s2.amain(["--room-url", "u", "--stt-model", "flux-general-en"])
    assert ran == ["flux-general-en"]
    with pytest.raises(SystemExit):
        spike_s2.parse_args(["--room-url", "u", "--stt-model", "nova-2"])


def test_room_url_is_required():
    with pytest.raises(SystemExit):
        spike_s2.parse_args([])
