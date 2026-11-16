"""QueueHandoff: the queue's implementation of the intake HandoffHook (PRD IN-7, QH-2).

The hook contract lives in `agent_assist_bot.intake`: `HandoffHook.on_intake_complete
(handoff: IntakeHandoff)`, with `NoopHandoff` as the default. The intake Flow calls it
once per call when intake ends (completed, request_person, or emergency).

QueueHandoff posts the handoff to the server's queue API
(server/agent_assist_server/queue_api.py). It also has `on_call_ended(session_id)`,
an extra method the bot calls when the caller leaves so a waiting or ringing call is
cleared, and `on_agent_left(session_id)` for when the agent leaves the room (QH-9).
`handoff_from_env()` picks QueueHandoff when AGENT_ASSIST_SERVER_URL is set,
otherwise NoopHandoff.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from agent_assist_bot.intake import HandoffHook, IntakeHandoff, NoopHandoff

logger = logging.getLogger(__name__)

SERVER_URL_ENV = "AGENT_ASSIST_SERVER_URL"
# Set by the server's bot launcher when login (APP_PASSWORD) is on; sent as a Bearer
# token so the queue endpoints accept the handoff.
BOT_KEY_ENV = "AGENT_ASSIST_BOT_KEY"


class HandoffError(RuntimeError):
    pass


def enqueue_payload(handoff: IntakeHandoff) -> dict[str, Any]:
    """Body for POST /queue/calls. The summary dict is what the agent sees on accept.

    `summary.identity_checks` lists which identity details matched (AP-18 badge); it is
    left out when identity was never checked."""
    summary: dict[str, Any] = {**handoff.intake, "summary": handoff.summary}
    if handoff.identity_checks is not None:
        summary["identity_checks"] = list(handoff.identity_checks)
    return {
        "session_id": handoff.session_id,
        "call_id": handoff.call_id,
        "outcome": handoff.outcome,
        "summary": summary,
        "identity_result": handoff.identity_result,
        "member_id": handoff.member_id,
        "flags": list(handoff.flags),
    }


class QueueHandoff:
    def __init__(
        self,
        server_url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
        bot_key: str | None = None,
    ):
        self.server_url = server_url.rstrip("/")
        self.bot_key = bot_key
        self._transport = transport
        self._timeout = timeout
        self._headers = {"authorization": f"Bearer {bot_key}"} if bot_key else {}
        self._enqueued: list[Callable[[str], Awaitable[None]]] = []
        self._closers: list[Callable[[], Awaitable[None]]] = []

    def add_enqueued_listener(self, fn: Callable[[str], Awaitable[None]]) -> None:
        """Called with the session id once the call is in the queue (QH-5b: the bot
        opens its socket to the server then)."""
        self._enqueued.append(fn)

    def add_closer(self, fn: Callable[[], Awaitable[None]]) -> None:
        self._closers.append(fn)

    async def close(self) -> None:
        for fn in self._closers:
            await fn()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.server_url,
            transport=self._transport,
            timeout=self._timeout,
            headers=self._headers,
        )

    async def on_intake_complete(self, handoff: IntakeHandoff) -> None:
        async with self._client() as c:
            try:
                r = await c.post("/queue/calls", json=enqueue_payload(handoff))
            except httpx.HTTPError as e:
                raise HandoffError(f"queue unreachable: {type(e).__name__}") from None
        if r.status_code != 200:
            raise HandoffError(f"enqueue returned {r.status_code}: {r.text[:200]}")
        logger.info(
            "handoff: %s (%s) queued at position %s",
            handoff.session_id,
            handoff.outcome,
            r.json().get("position"),
        )
        for fn in self._enqueued:
            await fn(handoff.session_id)

    async def on_call_ended(self, session_id: str) -> None:
        try:
            async with self._client() as c:
                await c.post(f"/queue/calls/{session_id}/end")
        except httpx.HTTPError as e:
            logger.warning("handoff: end call %s failed: %s", session_id, type(e).__name__)

    async def on_agent_left(self, session_id: str) -> None:
        """QH-9: the agent left the room. The server decides if it was a drop."""
        try:
            async with self._client() as c:
                await c.post(f"/queue/calls/{session_id}/agent-left")
        except httpx.HTTPError as e:
            logger.warning("handoff: agent left %s failed: %s", session_id, type(e).__name__)


async def end_call(handoff: HandoffHook, session_id: str) -> None:
    """Tell the queue the caller left, if the hook supports it (NoopHandoff does not)."""
    ended = getattr(handoff, "on_call_ended", None)
    if ended is not None:
        await ended(session_id)


async def agent_left(handoff: HandoffHook, session_id: str) -> None:
    """Tell the queue the agent left the room, if the hook supports it."""
    left = getattr(handoff, "on_agent_left", None)
    if left is not None:
        await left(session_id)


def handoff_from_env(env: dict[str, str] | None = None) -> HandoffHook:
    env = dict(os.environ) if env is None else env
    url = env.get(SERVER_URL_ENV, "").strip()
    if not url:
        return NoopHandoff()
    return QueueHandoff(url, bot_key=env.get(BOT_KEY_ENV, "").strip() or None)
