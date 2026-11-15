import pytest
from agent_assist_bot import spike_s2


async def test_runs_listen_bot_on_given_room(monkeypatch, capsys):
    ran = []

    async def fake_run(url, token=None):
        ran.append(url)

    monkeypatch.setattr(spike_s2, "run_listen_bot", fake_run)

    rc = await spike_s2.amain(["--room-url", "https://dg-chriscat.daily.co/s2"])

    assert rc == 0
    assert ran == ["https://dg-chriscat.daily.co/s2"]
    assert "https://dg-chriscat.daily.co/s2" in capsys.readouterr().out


def test_room_url_is_required():
    with pytest.raises(SystemExit):
        spike_s2.parse_args([])
