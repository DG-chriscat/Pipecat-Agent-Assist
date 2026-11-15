"""Minimal async client for the Daily REST API (rooms and meeting tokens).

Only what the dial-in path needs. The API key is held in memory and sent as a
bearer header; it is never logged or included in exception messages.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass

import httpx

DEFAULT_DAILY_API_URL = "https://api.daily.co/v1"
ROOM_EXP_SECONDS = 60 * 60
TOKEN_EXP_SECONDS = 60 * 60
# Must match agent_assist_bot.bot.BOT_NAME: the token's user_name overrides the bot's own.
BOT_USER_NAME = "Intake Bot"


class DailyAPIError(Exception):
    """Daily REST call failed. Message carries status and Daily's error body only."""


class DailyConfigError(Exception):
    """DAILY_API_KEY is not configured."""


@dataclass(frozen=True)
class DailyRoom:
    name: str
    url: str
    sip_endpoint: str | None


def build_dialin_room_body(
    *, caller: str | None, now: float | None = None, room_name: str | None = None
) -> dict:
    """Body for POST /rooms: a short-lived room that accepts one SIP dial-in leg.

    Mirrors pipecat.runner.daily.configure(sip_caller_phone=...), which is what
    Pipecat's Daily dial-in example uses.
    """
    now = time.time() if now is None else now
    return {
        "name": room_name or f"dialin-{uuid.uuid4().hex[:12]}",
        "properties": {
            "exp": int(now + ROOM_EXP_SECONDS),
            "eject_at_room_exp": True,
            "start_video_off": True,
            "sip": {
                "display_name": caller or "dial-in caller",
                "video": False,
                "sip_mode": "dial-in",
                "num_endpoints": 1,
            },
        },
    }


def build_token_body(*, room_name: str, now: float | None = None) -> dict:
    """Body for POST /meeting-tokens: owner token for the bot, scoped to one room."""
    now = time.time() if now is None else now
    return {
        "properties": {
            "room_name": room_name,
            "is_owner": True,
            "user_name": BOT_USER_NAME,
            "exp": int(now + TOKEN_EXP_SECONDS),
            "eject_at_token_exp": True,
        }
    }


def build_agent_token_body(*, room_name: str, user_name: str, exp: int) -> dict:
    """Body for POST /meeting-tokens: non-owner token for an agent's browser (spike S4)."""
    return {
        "properties": {
            "room_name": room_name,
            "user_name": user_name,
            "is_owner": False,
            "exp": exp,
        }
    }


class DailyRestClient:
    def __init__(
        self,
        api_key: str,
        api_url: str = DEFAULT_DAILY_API_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ):
        if not api_key:
            raise DailyConfigError("DAILY_API_KEY is not set")
        self._api_key = api_key
        self.api_url = api_url.rstrip("/")
        self._transport = transport
        self._timeout = timeout

    @classmethod
    def from_env(cls) -> DailyRestClient:
        return cls(
            api_key=os.getenv("DAILY_API_KEY", ""),
            api_url=os.getenv("DAILY_API_URL", DEFAULT_DAILY_API_URL),
        )

    @property
    def api_key(self) -> str:
        return self._api_key

    def __repr__(self) -> str:  # never show the key
        return f"DailyRestClient(api_url={self.api_url!r})"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.api_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            transport=self._transport,
            timeout=self._timeout,
        )

    async def _post(self, path: str, body: dict) -> dict:
        async with self._client() as client:
            try:
                r = await client.post(path, json=body)
            except httpx.HTTPError as e:
                raise DailyAPIError(f"POST {path} failed: {type(e).__name__}") from None
        if r.status_code != 200:
            raise DailyAPIError(f"POST {path} returned {r.status_code}: {r.text[:300]}")
        return r.json()

    async def create_dialin_room(self, caller: str | None) -> DailyRoom:
        data = await self._post("/rooms", build_dialin_room_body(caller=caller))
        return DailyRoom(
            name=data["name"],
            url=data["url"],
            sip_endpoint=(data.get("config") or {}).get("sip_endpoint"),
        )

    async def create_meeting_token(self, room_name: str) -> str:
        data = await self._post("/meeting-tokens", build_token_body(room_name=room_name))
        return data["token"]

    async def create_agent_token(self, room_name: str, user_name: str, exp: int) -> str:
        """Mint a non-owner meeting token for a human agent joining `room_name`."""
        body = build_agent_token_body(room_name=room_name, user_name=user_name, exp=exp)
        data = await self._post("/meeting-tokens", body)
        token = data.get("token")
        if not token:
            raise DailyAPIError("POST /meeting-tokens returned no token")
        return token

    async def delete_room(self, room_name: str) -> None:
        async with self._client() as client:
            r = await client.delete(f"/rooms/{room_name}")
        if r.status_code not in (200, 404):
            raise DailyAPIError(f"DELETE /rooms returned {r.status_code}: {r.text[:300]}")

    async def get_presence(self, room_name: str) -> dict:
        async with self._client() as client:
            r = await client.get(f"/rooms/{room_name}/presence")
        if r.status_code != 200:
            raise DailyAPIError(f"GET presence returned {r.status_code}: {r.text[:300]}")
        return r.json()
