"""Handoff hook: how the bot hands a finished intake to the queue (PRD IN-7, QH-2).

P2-3's intake Flow calls `on_intake_complete(session_id, summary)` when intake ends
(or the caller asks for a person) and `on_call_ended(session_id)` when the caller
leaves. The default is a no-op; with AGENT_ASSIST_SERVER_URL set, QueueHandoff posts
to the server's queue API (server/agent_assist_server/queue_api.py).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

SERVER_URL_ENV = "AGENT_ASSIST_SERVER_URL"


class HandoffError(RuntimeError):
    pass


class HandoffHook(Protocol):
    async def on_intake_complete(self, session_id: str, summary: dict[str, Any]) -> None: ...

    async def on_call_ended(self, session_id: str) -> None: ...


class NoopHandoff:
    async def on_intake_complete(self, session_id: str, summary: dict[str, Any]) -> None:
        logger.info("handoff (noop): intake complete for %s", session_id)

    async def on_call_ended(self, session_id: str) -> None:
        pass


class QueueHandoff:
    def __init__(
        self,
        server_url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ):
        self.server_url = server_url.rstrip("/")
        self._transport = transport
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.server_url, transport=self._transport, timeout=self._timeout
        )

    async def on_intake_complete(self, session_id: str, summary: dict[str, Any]) -> None:
        async with self._client() as c:
            try:
                r = await c.post(
                    "/queue/calls", json={"session_id": session_id, "summary": summary}
                )
            except httpx.HTTPError as e:
                raise HandoffError(f"queue unreachable: {type(e).__name__}") from None
        if r.status_code != 200:
            raise HandoffError(f"enqueue returned {r.status_code}: {r.text[:200]}")
        logger.info("handoff: %s queued at position %s", session_id, r.json().get("position"))

    async def on_call_ended(self, session_id: str) -> None:
        try:
            async with self._client() as c:
                await c.post(f"/queue/calls/{session_id}/end")
        except httpx.HTTPError as e:
            logger.warning("handoff: end call %s failed: %s", session_id, type(e).__name__)


def handoff_from_env(env: dict[str, str] | None = None) -> HandoffHook:
    env = dict(os.environ) if env is None else env
    url = env.get(SERVER_URL_ENV, "").strip()
    return QueueHandoff(url) if url else NoopHandoff()
