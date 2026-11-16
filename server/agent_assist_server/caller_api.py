"""Caller panel data (P3-3, PRD AP-5, AP-13, DC-3, DC-8).

    GET  /calls/{session_id}/caller?agent_id=...
    POST /calls/{session_id}/phi-reveal  {agent_id, field: member_id|dob}  (AP-17)

Returns what the agent's caller panel shows: the intake answers and identity result
from the queue (in memory, QH-4), the wait time, and, for verified callers only, the
member (name, ID, plan, DOB), calls in the last 30 days with a repeat flag (2 or more),
and the last 3 events across past interactions, claims, and referrals from the mock DB.

Access: the whole app sits behind the shared login (P2-6). On top of that only the
agent the call was accepted by can read it (agent_id must match call.agent_id); a
queued or ringing call is not readable yet.

Never guess a member: when identity is not verified (or verified without a member id,
or the id is gone from the DB) the reply has intake data only and not_verified true.
The claimed member id is not looked up at all in that case.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from agent_assist_server.db import connect
from agent_assist_server.queue_api import get_queue
from agent_assist_server.queue_service import QueueError

logger = logging.getLogger(__name__)

SCHEMA_ENV = "DATABASE_SCHEMA"
REPEAT_CALLS = 2
RECENT_DAYS = 30
RECENT_EVENTS = 3
# PRD DC-4: a connector call times out after 2 seconds.
LOOKUP_TIMEOUT_SECS = 2.0

router = APIRouter()


@dataclass(frozen=True)
class MemberHistory:
    member: dict[str, Any]
    calls_30d: int
    recent_events: list[dict[str, Any]] = field(default_factory=list)


class HistoryRepo(Protocol):
    async def lookup(self, member_id: str) -> MemberHistory | None: ...


MEMBER_SQL = """
SELECT m.member_id, m.first_name, m.last_name, m.dob, m.plan_id, p.name, p.plan_type
FROM members m JOIN plans p ON p.plan_id = m.plan_id
WHERE m.member_id = %(member_id)s
"""

CALLS_30D_SQL = """
SELECT count(*) FROM interactions
WHERE member_id = %(member_id)s AND started_at >= %(since)s AND started_at <= %(now)s
"""

# One row per event, newest first. Every branch filters on the same member id.
EVENTS_SQL = """
SELECT kind, ref, occurred_at, status, a, b, c FROM (
    SELECT 'call' AS kind, interaction_id AS ref, started_at AS occurred_at,
           outcome AS status, queue AS a, duration_seconds::text AS b, NULL::text AS c
    FROM interactions WHERE member_id = %(member_id)s AND started_at <= %(now)s
    UNION ALL
    SELECT 'claim', claim_id, COALESCE(processed_on, received_on)::timestamptz,
           status, service_description, denial_code, provider_name
    FROM claims WHERE member_id = %(member_id)s
    UNION ALL
    SELECT 'referral', referral_id, created_on::timestamptz,
           status, to_provider, specialty, NULL
    FROM referrals WHERE member_id = %(member_id)s
) e
ORDER BY occurred_at DESC, ref DESC
LIMIT %(limit)s
"""

_CLAIM_STATUS = {"in_review": "in review", "pending_info": "pending info"}


def _event(row: tuple) -> dict[str, Any]:
    kind, ref, occurred_at, status, a, b, _c = row
    if kind == "call":
        minutes = max(1, round(int(b) / 60))
        summary = f"called {a}, {minutes} min, {status}"
    elif kind == "claim":
        summary = f"{a} claim {_CLAIM_STATUS.get(status, status)}"
        if b:
            summary += f", {b}"
    else:
        summary = f"referral to {a}, {status}"
    return {
        "kind": kind,
        "ref": ref,
        "occurred_on": occurred_at.date().isoformat(),
        "status": status,
        "summary": summary,
    }


class CallerHistory:
    """Reads one member's caller history from the mock DB (DATABASE_URL)."""

    def __init__(
        self,
        *,
        schema: str | None = None,
        url: str | None = None,
        clock: Callable[[], dt.datetime] | None = None,
    ):
        self.schema = schema
        self._url = url
        self._clock = clock or (lambda: dt.datetime.now(dt.UTC))

    async def lookup(self, member_id: str) -> MemberHistory | None:
        now = self._clock()
        params = {
            "member_id": member_id,
            "now": now,
            "since": now - dt.timedelta(days=RECENT_DAYS),
            "limit": RECENT_EVENTS,
        }
        async with await connect(schema=self.schema, url=self._url) as conn:
            row = await (await conn.execute(MEMBER_SQL, params)).fetchone()
            if row is None:
                return None
            mid, first, last, dob, plan_id, plan_name, plan_type = row
            (calls,) = await (await conn.execute(CALLS_30D_SQL, params)).fetchone()
            events = await (await conn.execute(EVENTS_SQL, params)).fetchall()
        return MemberHistory(
            member={
                "member_id": mid,
                "first_name": first,
                "last_name": last,
                "name": f"{first} {last}",
                "dob": dob.isoformat(),
                "plan_id": plan_id,
                "plan_name": plan_name,
                "plan_type": plan_type,
            },
            calls_30d=int(calls),
            recent_events=[_event(r) for r in events],
        )


def get_history(app: FastAPI) -> HistoryRepo:
    repo = getattr(app.state, "caller_history", None)
    if repo is None:
        repo = CallerHistory(schema=os.environ.get(SCHEMA_ENV, "").strip() or None)
        app.state.caller_history = repo
    return repo


@router.get("/calls/{session_id}/caller")
async def caller_panel(
    session_id: str, request: Request, agent_id: str = Query(min_length=1, max_length=64)
) -> dict[str, Any]:
    svc = get_queue(request.app)
    try:
        call = svc.get_call(session_id)
    except QueueError:
        raise HTTPException(status_code=404, detail="unknown call") from None
    if call.agent_id is None or call.agent_id != agent_id:
        raise HTTPException(status_code=403, detail="only the agent on this call can read it")

    answered = call.answered_at if call.answered_at is not None else time.time()
    body: dict[str, Any] = {
        "session_id": call.session_id,
        "identity_result": call.identity_result,
        "not_verified": True,
        "outcome": call.outcome,
        "flags": list(call.flags),
        "intake": dict(call.summary),
        "wait_secs": round(max(0.0, answered - call.queued_at), 1),
        "member": None,
        "calls_30d": None,
        "repeat_caller": None,
        "recent_events": [],
    }
    if call.identity_result != "verified" or not call.member_id:
        return body

    try:
        history = await asyncio.wait_for(
            get_history(request.app).lookup(call.member_id), LOOKUP_TIMEOUT_SECS
        )
    except Exception as e:
        # Never echo the error: it can carry the database URL.
        logger.error("caller history lookup failed for %s: %s", session_id, type(e).__name__)
        raise HTTPException(status_code=503, detail="caller history is unavailable") from None
    if history is None:
        logger.warning("verified call %s points at a member not in the DB", session_id)
        return body

    body.update(
        not_verified=False,
        member=history.member,
        calls_30d=history.calls_30d,
        repeat_caller=history.calls_30d >= REPEAT_CALLS,
        recent_events=history.recent_events,
    )
    return body


class PhiRevealRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    field: Literal["member_id", "dob"]


@router.post("/calls/{session_id}/phi-reveal")
async def phi_reveal(session_id: str, body: PhiRevealRequest, request: Request) -> dict[str, Any]:
    """AP-17: the agent clicked Reveal on a masked field. Logged on the call record
    (calls.phi_reveals via the queue write-through); the portal re-masks after 30 s."""
    svc = get_queue(request.app)
    try:
        svc.get_call(session_id)
    except QueueError:
        raise HTTPException(status_code=404, detail="unknown call") from None
    try:
        return svc.log_phi_reveal(body.agent_id, session_id, body.field)
    except QueueError as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
