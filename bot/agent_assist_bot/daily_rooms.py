"""Minimal Daily REST API client for creating and deleting rooms."""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

DAILY_API_URL = "https://api.daily.co/v1"
# Rooms created for tests and the spike must expire within 10 minutes.
MAX_TEST_ROOM_TTL_SECS = 600
DEFAULT_ROOM_TTL_SECS = 540


class DailyRoomsError(RuntimeError):
    """Raised when the Daily REST API call fails."""


@dataclass(frozen=True)
class DailyRoom:
    name: str
    url: str

    @classmethod
    def from_url(cls, url: str) -> DailyRoom:
        return cls(name=urlparse(url).path.strip("/"), url=url)


class DailyRoomsClient:
    """Create and delete Daily rooms. Use as an async context manager."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        api_url: str = DAILY_API_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ):
        api_key = api_key or os.getenv("DAILY_API_KEY")
        if not api_key:
            raise DailyRoomsError("DAILY_API_KEY is not set")
        self._clock = clock
        self._http = httpx.AsyncClient(
            base_url=api_url,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
            timeout=10.0,
        )

    async def __aenter__(self) -> DailyRoomsClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def create_room(
        self, *, ttl_secs: int = DEFAULT_ROOM_TTL_SECS, name: str | None = None
    ) -> DailyRoom:
        if ttl_secs <= 0:
            raise ValueError("ttl_secs must be positive")
        body: dict = {
            "properties": {
                "exp": int(self._clock()) + ttl_secs,
                "eject_at_room_exp": True,
            }
        }
        if name:
            body["name"] = name
        resp = await self._http.post("/rooms", json=body)
        if resp.status_code >= 400:
            raise DailyRoomsError(f"create room failed: HTTP {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        return DailyRoom(name=data["name"], url=data["url"])

    async def delete_room(self, name: str) -> None:
        resp = await self._http.delete(f"/rooms/{name}")
        if resp.status_code == 404:
            return
        if resp.status_code >= 400:
            raise DailyRoomsError(f"delete room failed: HTTP {resp.status_code} {resp.text[:200]}")

    @asynccontextmanager
    async def temporary_room(
        self, *, ttl_secs: int = DEFAULT_ROOM_TTL_SECS, name: str | None = None
    ) -> AsyncIterator[DailyRoom]:
        room = await self.create_room(ttl_secs=ttl_secs, name=name)
        try:
            yield room
        finally:
            await self.delete_room(room.name)
