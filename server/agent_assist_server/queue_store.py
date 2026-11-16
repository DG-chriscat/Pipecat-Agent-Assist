"""Postgres write-through for the queue service (docs/queue.md).

Subscribes to queue events and upserts the `agents` and `calls` rows. Writes run one
at a time in event order on a background task, so the queue never waits on the
database, and a database error is logged and dropped (the in-memory queue stays the
source of truth, QH-4). Enabled in the server with QUEUE_PERSIST=1.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any

from psycopg.types.json import Jsonb

from agent_assist_server.db import connect
from agent_assist_server.queue_service import Event

logger = logging.getLogger(__name__)


def _ts(epoch: float | None) -> dt.datetime | None:
    return None if epoch is None else dt.datetime.fromtimestamp(epoch, tz=dt.UTC)


UPSERT_AGENT = """
INSERT INTO agents (agent_id, name, state, idle_since, current_call, updated_at)
VALUES (%(agent_id)s, %(name)s, %(state)s, %(idle_since)s, %(call_id)s, now())
ON CONFLICT (agent_id) DO UPDATE SET
    name = EXCLUDED.name, state = EXCLUDED.state, idle_since = EXCLUDED.idle_since,
    current_call = EXCLUDED.current_call, updated_at = now()
"""

# The bot (P2-3) may have created the row during intake: merge the summary into its
# intake jsonb instead of replacing it. Flags are merged (the bot's own add_flag writes
# stay), identity_result/member_id only fill in or overwrite with a non-null value.
# member_id is a foreign key, so an unknown id is dropped rather than failing the write.
UPSERT_CALL = """
INSERT INTO calls (session_id, status, intake, queued_at, answered_at, ended_at, agent_id,
                   ended_by, wrap_up_reason, wrap_up_notes, wrapped_up_at,
                   identity_result, member_id, flags, phi_reveals, identity_decision)
VALUES (%(session_id)s, %(status)s, %(summary)s, %(queued_at)s, %(answered_at)s,
        %(ended_at)s, %(agent_id)s, %(ended_by)s, %(wrap_up_reason)s, %(wrap_up_notes)s,
        %(wrapped_up_at)s, %(identity_result)s,
        (SELECT member_id FROM members WHERE member_id = %(member_id)s), %(flags)s,
        %(phi_reveals)s, %(identity_decision)s)
ON CONFLICT (session_id) DO UPDATE SET
    status = EXCLUDED.status,
    intake = calls.intake || EXCLUDED.intake,
    queued_at = EXCLUDED.queued_at,
    answered_at = EXCLUDED.answered_at,
    ended_at = EXCLUDED.ended_at,
    agent_id = EXCLUDED.agent_id,
    ended_by = EXCLUDED.ended_by,
    wrap_up_reason = EXCLUDED.wrap_up_reason,
    wrap_up_notes = EXCLUDED.wrap_up_notes,
    wrapped_up_at = EXCLUDED.wrapped_up_at,
    phi_reveals = EXCLUDED.phi_reveals,
    identity_decision = COALESCE(EXCLUDED.identity_decision, calls.identity_decision),
    identity_result = COALESCE(EXCLUDED.identity_result, calls.identity_result),
    member_id = COALESCE(EXCLUDED.member_id, calls.member_id),
    flags = calls.flags || ARRAY(
        SELECT f FROM unnest(EXCLUDED.flags) AS f WHERE NOT (f = ANY(calls.flags))
    ),
    updated_at = now()
"""


class QueueStore:
    def __init__(self, *, schema: str | None = None, url: str | None = None):
        self._schema = schema
        self._url = url
        self._pending: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def on_event(self, event: Event) -> None:
        if "agent" in event:
            a = event["agent"]
            self._put(UPSERT_AGENT, {**a, "idle_since": _ts(a["idle_since"])})
        if "call" in event:
            c = event["call"]
            wrap = c.get("wrap_up") or {}
            # Agent rows land first: the service emits agent_updated before the call
            # event that points at the agent (calls.agent_id is a foreign key).
            self._put(
                UPSERT_CALL,
                {
                    "session_id": c["session_id"],
                    "status": c["status"],
                    "summary": Jsonb(c["summary"]),
                    "queued_at": _ts(c["queued_at"]),
                    "answered_at": _ts(c["answered_at"]),
                    "ended_at": _ts(c["ended_at"]),
                    "agent_id": c["agent_id"],
                    "ended_by": c.get("ended_by"),
                    "wrap_up_reason": wrap.get("reason_code"),
                    "wrap_up_notes": wrap.get("notes"),
                    "wrapped_up_at": _ts(wrap.get("saved_at")),
                    "identity_result": c.get("identity_result"),
                    "member_id": c.get("member_id"),
                    "flags": list(c.get("flags") or []),
                    "phi_reveals": Jsonb(list(c.get("phi_reveals") or [])),
                    "identity_decision": (
                        Jsonb(c["identity_decision"]) if c.get("identity_decision") else None
                    ),
                },
            )

    def _put(self, query: str, params: dict[str, Any]) -> None:
        self._pending.put_nowait((query, params))
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self) -> None:
        conn = None
        try:
            while not self._pending.empty():
                query, params = self._pending.get_nowait()
                try:
                    if conn is None or conn.closed:
                        conn = await connect(schema=self._schema, url=self._url)
                    await conn.execute(query, params)
                except Exception as e:
                    # Never log params or the URL; the error class is enough to debug.
                    logger.error("queue write-through failed: %s", type(e).__name__)
                    if conn is not None:
                        await conn.close()
                        conn = None
        finally:
            if conn is not None:
                await conn.close()

    async def drain(self) -> None:
        """Wait until every event so far is written (tests)."""
        while self._task is not None and not self._task.done():
            await asyncio.shield(self._task)
            if not self._pending.empty():
                self._task = asyncio.get_running_loop().create_task(self._run())
