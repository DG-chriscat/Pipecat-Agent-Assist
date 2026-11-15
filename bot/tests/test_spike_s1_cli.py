import httpx
from agent_assist_bot import spike_s1
from agent_assist_bot.daily_rooms import DailyRoomsClient


async def test_given_room_url_is_printed_and_used_without_creating(monkeypatch, capsys):
    ran = []

    async def fake_run_bot(url, token=None):
        ran.append(url)

    monkeypatch.setattr(spike_s1, "run_bot", fake_run_bot)

    def no_http(request):
        raise AssertionError("must not call the Daily API when --room-url is given")

    rc = await spike_s1.amain(
        ["--room-url", "https://dg-chriscat.daily.co/existing"],
        rooms_factory=lambda: DailyRoomsClient(api_key="k", transport=httpx.MockTransport(no_http)),
    )

    assert rc == 0
    assert ran == ["https://dg-chriscat.daily.co/existing"]
    assert "https://dg-chriscat.daily.co/existing" in capsys.readouterr().out


async def test_without_room_url_creates_prints_and_deletes(monkeypatch, capsys):
    calls = []
    ran = []

    def handler(request):
        calls.append(request.method)
        if request.method == "POST":
            return httpx.Response(
                200, json={"name": "new", "url": "https://dg-chriscat.daily.co/new"}
            )
        return httpx.Response(200, json={"deleted": True})

    async def fake_run_bot(url, token=None):
        ran.append(url)

    monkeypatch.setattr(spike_s1, "run_bot", fake_run_bot)

    rc = await spike_s1.amain(
        [],
        rooms_factory=lambda: DailyRoomsClient(api_key="k", transport=httpx.MockTransport(handler)),
    )

    assert rc == 0
    assert ran == ["https://dg-chriscat.daily.co/new"]
    assert calls == ["POST", "DELETE"]
    assert "https://dg-chriscat.daily.co/new" in capsys.readouterr().out
