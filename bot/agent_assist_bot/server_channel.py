"""The bot's own websocket to the server (QH-5b).

After handoff the bot opens /ws/bot/{session_id} on the server (AGENT_ASSIST_SERVER_URL,
http -> ws) with the bot key (AGENT_ASSIST_BOT_KEY) as a Bearer token when the server
has login on. The server pushes agent_accepted, requeued and call_ended on it, the
same messages (same "id") it also sends as Daily app messages. The socket skips the
REST hop through Daily, so the bot stops hold music sooner after the agent accepts.

The Daily app message stays the fallback: if this socket is down, refused, or slow,
the Daily copy still arrives and the bot acts on it. HoldController drops whichever
copy comes second (by id). A dropped socket reconnects with a short backoff; a
refusal (4401 bad key, 4404 unknown session) stops trying.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from urllib.parse import quote

import websockets
from loguru import logger
from websockets.exceptions import ConnectionClosed, InvalidStatus

OnMessage = Callable[[dict[str, Any]], Awaitable[None]]
# Close codes from the server that mean "do not retry" (auth.WS_UNAUTHORIZED,
# queue_api.WS_UNKNOWN_SESSION).
FATAL_CLOSE_CODES = frozenset({4401, 4404})
RETRY_DELAYS = (0.2, 0.5, 1.0, 2.0)


def channel_url(server_url: str, session_id: str) -> str:
    base = server_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    return f"{base}/ws/bot/{quote(session_id, safe='')}"


class ServerChannel:
    def __init__(
        self,
        server_url: str,
        session_id: str,
        on_message: OnMessage,
        *,
        bot_key: str | None = None,
        retry_delays: Sequence[float] = RETRY_DELAYS,
    ):
        self.url = channel_url(server_url, session_id)
        self.session_id = session_id
        self._on_message = on_message
        self._headers = {"authorization": f"Bearer {bot_key}"} if bot_key else {}
        self._retry_delays = tuple(retry_delays) or RETRY_DELAYS
        self._task: asyncio.Task | None = None

    @property
    def done(self) -> bool:
        return self._task is not None and self._task.done()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        failures = 0
        while True:
            try:
                async with websockets.connect(
                    self.url, additional_headers=self._headers, open_timeout=5
                ) as ws:
                    logger.info(f"[channel] connected to the server for {self.session_id}")
                    failures = 0
                    async for raw in ws:
                        await self._deliver(raw)
                code = ws.close_code
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as e:
                code = e.rcvd.code if e.rcvd else None
            except (OSError, InvalidStatus, TimeoutError) as e:
                code = None
                logger.warning(f"[channel] connect failed ({type(e).__name__}); Daily fallback")
            if code in FATAL_CLOSE_CODES:
                logger.warning(f"[channel] server refused the socket ({code}); Daily only")
                return
            delay = self._retry_delays[min(failures, len(self._retry_delays) - 1)]
            failures += 1
            await asyncio.sleep(delay)

    async def _deliver(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("[channel] ignoring a message that is not JSON")
            return
        if not isinstance(msg, dict):
            return
        try:
            await self._on_message(msg)
        except Exception as e:  # noqa: BLE001 - one bad message must not end the socket
            logger.error(f"[channel] handling {msg.get('event')!r} failed: {e}")
