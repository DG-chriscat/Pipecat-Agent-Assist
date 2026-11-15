"""Queue service: the single source of truth for calls and agent states (PRD QH-1 to QH-4).

In memory, one process. Every change is published as an event dict to subscribers:
the websocket hub (portal, QH-4), the bot notifier (QH-5/QH-6), and the optional
Postgres write-through (docs/queue.md). State is owned here; the database is a copy.

- One FIFO queue (QH-2). A missed call keeps its place at the front.
- Agents: offline, available, ringing, on_call, wrap_up (QH-1). Only available agents
  get offers.
- An offer rings the longest idle available agent for OFFER_TIMEOUT_SECS (QH-3). A miss
  or a decline sets that agent offline (the only "not available" state) and rings the
  next one.
- Accept mints a Daily meeting token for the agent and returns it with the intake
  summary (QH-6, QH-7). Tokens go only to the accepting agent, never into events.

Single threaded by design: call it from the event loop only. Timers come from a
clock object so tests can use a fake one.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

OFFER_TIMEOUT_SECS = 20.0

Event = dict[str, Any]
Listener = Callable[[Event], None]
TokenMinter = Callable[[str, str], Awaitable[tuple[str, int]]]


class QueueError(Exception):
    """A command is not valid in the current state. Message is safe to show the client."""


class TokenError(QueueError):
    """Daily refused or failed to mint the agent's meeting token. The call was re-queued."""


class AgentState(StrEnum):
    OFFLINE = "offline"
    AVAILABLE = "available"
    RINGING = "ringing"
    ON_CALL = "on_call"
    WRAP_UP = "wrap_up"


class CallStatus(StrEnum):
    # Same values as calls.status in db/migrations/0001_init.sql.
    QUEUED = "queued"
    WITH_AGENT = "with_agent"
    ENDED = "ended"
    ABANDONED = "abandoned"


# States an agent may set by hand, from each state. ringing and on_call are set by the
# service only (offer and accept). ringing -> offline counts as a decline; on_call ->
# wrap_up ends the call.
MANUAL_TRANSITIONS: dict[AgentState, set[AgentState]] = {
    AgentState.OFFLINE: {AgentState.AVAILABLE},
    AgentState.AVAILABLE: {AgentState.OFFLINE},
    AgentState.RINGING: {AgentState.OFFLINE},
    AgentState.ON_CALL: {AgentState.WRAP_UP},
    AgentState.WRAP_UP: {AgentState.AVAILABLE, AgentState.OFFLINE},
}


class TimerHandle(Protocol):
    def cancel(self) -> None: ...


class Clock(Protocol):
    def now(self) -> float: ...

    def call_later(self, delay: float, fn: Callable[[], None]) -> TimerHandle: ...


class LoopClock:
    """Wall clock plus asyncio timers on the running loop."""

    def now(self) -> float:
        return time.time()

    def call_later(self, delay: float, fn: Callable[[], None]) -> TimerHandle:
        return asyncio.get_running_loop().call_later(delay, fn)


@dataclass
class Agent:
    agent_id: str
    name: str
    seq: int
    state: AgentState = AgentState.OFFLINE
    idle_since: float | None = None
    offer_call_id: str | None = None
    offer_expires_at: float | None = None
    call_id: str | None = None
    _timer: TimerHandle | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "state": self.state.value,
            "idle_since": self.idle_since,
            "offer_call_id": self.offer_call_id,
            "offer_expires_at": self.offer_expires_at,
            "call_id": self.call_id,
        }


@dataclass
class Call:
    session_id: str
    room_url: str
    room_name: str
    summary: dict[str, Any]
    seq: int
    queued_at: float
    status: CallStatus = CallStatus.QUEUED
    offered_to: str | None = None
    agent_id: str | None = None
    answered_at: float | None = None
    ended_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "room_url": self.room_url,
            "room_name": self.room_name,
            "summary": self.summary,
            "status": self.status.value,
            "queued_at": self.queued_at,
            "offered_to": self.offered_to,
            "agent_id": self.agent_id,
            "answered_at": self.answered_at,
            "ended_at": self.ended_at,
        }


@dataclass(frozen=True)
class AcceptResult:
    session_id: str
    room_url: str
    token: str
    expires_at: int
    summary: dict[str, Any]


def room_name_from_url(room_url: str) -> str:
    parsed = urlparse(room_url)
    name = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    if parsed.scheme not in ("http", "https") or not parsed.netloc or not name:
        raise QueueError("room_url must be an http(s) Daily room URL")
    return name


class QueueService:
    def __init__(
        self,
        *,
        mint_token: TokenMinter,
        clock: Clock | None = None,
        offer_timeout: float = OFFER_TIMEOUT_SECS,
    ):
        self._mint_token = mint_token
        self._clock = clock or LoopClock()
        self._offer_timeout = offer_timeout
        self._agents: dict[str, Agent] = {}
        self._calls: dict[str, Call] = {}
        self._rooms: dict[str, str] = {}
        self._listeners: list[Listener] = []
        self._seq = 0

    # ---- events -----------------------------------------------------------

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _emit(self, event: Event) -> None:
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:
                logger.exception("queue listener failed on %s", event.get("type"))

    def _agent_updated(self, agent: Agent) -> None:
        self._emit({"type": "agent_updated", "agent": agent.to_dict()})

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    # ---- reads -------------------------------------------------------------

    def get_agent(self, agent_id: str) -> Agent:
        try:
            return self._agents[agent_id]
        except KeyError:
            raise QueueError(f"unknown agent {agent_id!r}") from None

    def get_call(self, session_id: str) -> Call:
        try:
            return self._calls[session_id]
        except KeyError:
            raise QueueError(f"unknown call {session_id!r}") from None

    def agents(self) -> list[Agent]:
        return sorted(self._agents.values(), key=lambda a: a.seq)

    def waiting_calls(self) -> list[Call]:
        """Queued calls in arrival order, including the ones currently ringing."""
        waiting = (c for c in self._calls.values() if c.status is CallStatus.QUEUED)
        return sorted(waiting, key=lambda c: c.seq)

    def active_calls(self) -> list[Call]:
        return [c for c in self._calls.values() if c.status is CallStatus.WITH_AGENT]

    def snapshot(self) -> dict[str, Any]:
        return {
            "queue": [c.to_dict() for c in self.waiting_calls()],
            "active": [c.to_dict() for c in self.active_calls()],
            "agents": [a.to_dict() for a in self.agents()],
            "offer_timeout_secs": self._offer_timeout,
        }

    # ---- sessions started by this server ------------------------------------

    def register_session(self, session_id: str, room_url: str) -> None:
        """Remember the room of a bot this server launched, so only those can be queued."""
        room_name_from_url(room_url)
        self._rooms[session_id] = room_url

    def room_for(self, session_id: str) -> str | None:
        return self._rooms.get(session_id)

    # ---- agents ------------------------------------------------------------

    def sign_in(self, name: str) -> Agent:
        """Demo sign in (AP-1): a display name is the identity. Same name, same agent."""
        name = name.strip()
        if not name or len(name) > 100:
            raise QueueError("name must be 1 to 100 characters")
        for agent in self._agents.values():
            if agent.name == name:
                return agent
        agent = Agent(agent_id=f"agent-{uuid.uuid4().hex[:8]}", name=name, seq=self._next_seq())
        self._agents[agent.agent_id] = agent
        self._agent_updated(agent)
        return agent

    def set_status(self, agent_id: str, state: AgentState | str) -> Agent:
        agent = self.get_agent(agent_id)
        try:
            target = AgentState(state)
        except ValueError:
            raise QueueError(f"unknown state {state!r}") from None
        if target is agent.state:
            return agent
        if target not in MANUAL_TRANSITIONS[agent.state]:
            raise QueueError(f"cannot go from {agent.state.value} to {target.value}")

        if agent.state is AgentState.RINGING:
            self._miss_offer(agent, "declined")
            return agent
        if agent.state is AgentState.ON_CALL:
            self._finish_call(agent.call_id, CallStatus.ENDED)
            return agent

        # Remaining manual moves: offline <-> available, wrap_up -> available/offline.
        agent.state = target
        agent.call_id = None
        agent.idle_since = self._clock.now() if target is AgentState.AVAILABLE else None
        self._agent_updated(agent)
        if target is AgentState.AVAILABLE:
            self._dispatch()
        return agent

    # ---- calls -------------------------------------------------------------

    def enqueue_call(self, session_id: str, room_url: str, summary: dict[str, Any]) -> Call:
        """Put a call at the back of the queue (PRD QH-2). Re-sending updates the summary."""
        room_name = room_name_from_url(room_url)
        existing = self._calls.get(session_id)
        if existing is not None:
            if existing.status is not CallStatus.QUEUED:
                raise QueueError(f"call {session_id!r} is already {existing.status.value}")
            existing.summary = dict(summary)
            self._emit({"type": "call_updated", "call": existing.to_dict()})
            return existing
        call = Call(
            session_id=session_id,
            room_url=room_url,
            room_name=room_name,
            summary=dict(summary),
            seq=self._next_seq(),
            queued_at=self._clock.now(),
        )
        self._calls[session_id] = call
        self._emit(
            {"type": "call_queued", "call": call.to_dict(), "position": self._position(call)}
        )
        self._dispatch()
        return call

    def _position(self, call: Call) -> int:
        return [c.session_id for c in self.waiting_calls()].index(call.session_id) + 1

    def end_call(self, session_id: str) -> None:
        """The caller left (or the bot ended). Frees a ringing agent; on_call -> wrap_up."""
        call = self._calls.get(session_id)
        if call is None:
            return
        if call.status is CallStatus.QUEUED:
            self._finish_call(session_id, CallStatus.ABANDONED)
        elif call.status is CallStatus.WITH_AGENT:
            self._finish_call(session_id, CallStatus.ENDED)

    def _finish_call(self, session_id: str | None, status: CallStatus) -> None:
        call = self._calls.get(session_id or "")
        if call is None:
            return
        if call.offered_to:
            agent = self._agents[call.offered_to]
            self._clear_offer(agent)
            agent.state = AgentState.AVAILABLE
            call.offered_to = None
            self._agent_updated(agent)
        if call.agent_id and call.status is CallStatus.WITH_AGENT:
            agent = self._agents[call.agent_id]
            if agent.state is AgentState.ON_CALL and agent.call_id == session_id:
                agent.state = AgentState.WRAP_UP
                self._agent_updated(agent)
        call.status = status
        call.ended_at = self._clock.now()
        self._emit({"type": "call_ended", "call": call.to_dict()})
        self._dispatch()

    # ---- offers ------------------------------------------------------------

    def _dispatch(self) -> None:
        """Offer each unoffered waiting call, in order, to the longest idle available agent."""
        for call in self.waiting_calls():
            if call.offered_to:
                continue
            available = [a for a in self._agents.values() if a.state is AgentState.AVAILABLE]
            if not available:
                return
            agent = min(available, key=lambda a: (a.idle_since or 0.0, a.seq))
            self._offer(agent, call)

    def _offer(self, agent: Agent, call: Call) -> None:
        agent.state = AgentState.RINGING
        agent.offer_call_id = call.session_id
        agent.offer_expires_at = self._clock.now() + self._offer_timeout
        call.offered_to = agent.agent_id
        agent_id, session_id = agent.agent_id, call.session_id
        agent._timer = self._clock.call_later(
            self._offer_timeout, lambda: self._on_offer_timeout(agent_id, session_id)
        )
        self._agent_updated(agent)
        self._emit({"type": "call_updated", "call": call.to_dict()})
        self._emit(
            {
                "type": "offer",
                "agent_id": agent.agent_id,
                "call": call.to_dict(),
                "expires_at": agent.offer_expires_at,
            }
        )

    def _clear_offer(self, agent: Agent) -> None:
        if agent._timer is not None:
            agent._timer.cancel()
            agent._timer = None
        agent.offer_call_id = None
        agent.offer_expires_at = None

    def _on_offer_timeout(self, agent_id: str, session_id: str) -> None:
        agent = self._agents.get(agent_id)
        if agent and agent.state is AgentState.RINGING and agent.offer_call_id == session_id:
            agent._timer = None
            self._miss_offer(agent, "timeout")

    def _miss_offer(self, agent: Agent, reason: str) -> None:
        """Missed or declined: agent goes offline (not available), call rings the next."""
        session_id = agent.offer_call_id
        self._clear_offer(agent)
        agent.state = AgentState.OFFLINE
        agent.idle_since = None
        call = self._calls.get(session_id or "")
        if call is not None:
            call.offered_to = None
        self._emit(
            {
                "type": "offer_missed",
                "agent_id": agent.agent_id,
                "session_id": session_id,
                "reason": reason,
            }
        )
        self._agent_updated(agent)
        if call is not None:
            self._emit({"type": "call_updated", "call": call.to_dict()})
        self._dispatch()

    def _check_offer(self, agent_id: str, session_id: str) -> Agent:
        agent = self.get_agent(agent_id)
        if agent.state is not AgentState.RINGING or agent.offer_call_id != session_id:
            raise QueueError(f"no open offer for call {session_id!r}")
        return agent

    def decline(self, agent_id: str, session_id: str) -> None:
        agent = self._check_offer(agent_id, session_id)
        self._miss_offer(agent, "declined")

    async def accept(self, agent_id: str, session_id: str) -> AcceptResult:
        """Take the call: mint the agent's meeting token, return it with the summary."""
        agent = self._check_offer(agent_id, session_id)
        call = self._calls[session_id]
        self._clear_offer(agent)
        agent.state = AgentState.ON_CALL
        agent.idle_since = None
        agent.call_id = session_id
        call.offered_to = None
        call.status = CallStatus.WITH_AGENT
        call.agent_id = agent.agent_id
        call.answered_at = self._clock.now()
        try:
            token, expires_at = await self._mint_token(call.room_name, agent.name)
        except Exception as e:
            logger.error("token mint failed for %s: %s", session_id, type(e).__name__)
            if call.status is CallStatus.WITH_AGENT and call.agent_id == agent.agent_id:
                # Not the agent's fault: put both back and ring again.
                call.status = CallStatus.QUEUED
                call.agent_id = None
                call.answered_at = None
                agent.state = AgentState.AVAILABLE
                agent.call_id = None
                agent.idle_since = self._clock.now()
                self._agent_updated(agent)
                self._dispatch()
            raise TokenError("could not create a meeting token, try again") from None

        if call.status is not CallStatus.WITH_AGENT or call.agent_id != agent.agent_id:
            raise QueueError(f"call {session_id!r} ended before it connected")
        self._agent_updated(agent)
        self._emit({"type": "call_accepted", "agent_id": agent.agent_id, "call": call.to_dict()})
        return AcceptResult(
            session_id=session_id,
            room_url=call.room_url,
            token=token,
            expires_at=expires_at,
            summary=call.summary,
        )
