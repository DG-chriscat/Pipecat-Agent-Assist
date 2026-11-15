import json

import httpx
import pytest
from agent_assist_bot.daily_rooms import (
    MAX_TEST_ROOM_TTL_SECS,
    DailyRoom,
    DailyRoomsClient,
    DailyRoomsError,
)

NOW = 1_800_000_000


def make_client(handler, **kwargs):
    transport = httpx.MockTransport(handler)
    return DailyRoomsClient(api_key="test-key", transport=transport, clock=lambda: NOW, **kwargs)


async def test_create_room_posts_expiring_room_and_returns_url():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"name": "abc123", "url": "https://dg-chriscat.daily.co/abc123"},
        )

    async with make_client(handler) as client:
        room = await client.create_room(ttl_secs=300)

    assert room == DailyRoom(name="abc123", url="https://dg-chriscat.daily.co/abc123")
    assert seen["method"] == "POST"
    assert seen["url"] == "https://api.daily.co/v1/rooms"
    assert seen["auth"] == "Bearer test-key"
    props = seen["body"]["properties"]
    assert props["exp"] == NOW + 300
    assert props["eject_at_room_exp"] is True


async def test_create_room_default_ttl_is_within_ten_minutes():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"name": "r", "url": "https://x.daily.co/r"})

    async with make_client(handler) as client:
        await client.create_room()

    exp = bodies[0]["properties"]["exp"]
    assert NOW < exp <= NOW + MAX_TEST_ROOM_TTL_SECS
    assert MAX_TEST_ROOM_TTL_SECS <= 600


async def test_create_room_passes_optional_name():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"name": "s1-test", "url": "https://x.daily.co/s1-test"})

    async with make_client(handler) as client:
        room = await client.create_room(name="s1-test")

    assert bodies[0]["name"] == "s1-test"
    assert room.name == "s1-test"


async def test_create_room_rejects_non_positive_ttl():
    async with make_client(lambda r: httpx.Response(500)) as client:
        with pytest.raises(ValueError):
            await client.create_room(ttl_secs=0)


async def test_create_room_raises_on_api_error_without_leaking_key():
    def handler(request):
        return httpx.Response(401, json={"error": "authentication-error"})

    async with make_client(handler) as client:
        with pytest.raises(DailyRoomsError) as excinfo:
            await client.create_room()

    assert "401" in str(excinfo.value)
    assert "test-key" not in str(excinfo.value)


async def test_delete_room_sends_delete():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"deleted": True, "name": "abc123"})

    async with make_client(handler) as client:
        await client.delete_room("abc123")

    assert seen == {"method": "DELETE", "url": "https://api.daily.co/v1/rooms/abc123"}


async def test_delete_room_ignores_already_gone():
    async with make_client(lambda r: httpx.Response(404, json={"error": "not-found"})) as client:
        await client.delete_room("gone")


async def test_delete_room_raises_on_server_error():
    async with make_client(lambda r: httpx.Response(500, json={"error": "x"})) as client:
        with pytest.raises(DailyRoomsError):
            await client.delete_room("abc123")


async def test_temporary_room_context_deletes_on_exit():
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, json={"name": "tmp", "url": "https://x.daily.co/tmp"})
        return httpx.Response(200, json={"deleted": True})

    async with make_client(handler) as client:
        async with client.temporary_room(ttl_secs=120) as room:
            assert room.url == "https://x.daily.co/tmp"
            assert calls == ["POST"]

    assert calls == ["POST", "DELETE"]


async def test_temporary_room_context_deletes_on_error():
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, json={"name": "tmp", "url": "https://x.daily.co/tmp"})
        return httpx.Response(200, json={"deleted": True})

    async with make_client(handler) as client:
        with pytest.raises(RuntimeError):
            async with client.temporary_room():
                raise RuntimeError("boom")

    assert calls == ["POST", "DELETE"]


def test_room_name_from_url():
    room = DailyRoom.from_url("https://dg-chriscat.daily.co/my-room")
    assert room == DailyRoom(name="my-room", url="https://dg-chriscat.daily.co/my-room")


def test_client_requires_api_key(monkeypatch):
    monkeypatch.delenv("DAILY_API_KEY", raising=False)
    with pytest.raises(DailyRoomsError):
        DailyRoomsClient()
