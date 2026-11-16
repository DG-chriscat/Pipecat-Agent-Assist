"""Queue REST + websocket API (PRD QH-3 to QH-7) and the bot notifier (QH-5, QH-6).

REST:
    POST /agents/sign-in                 {name}                -> agent (AP-1 demo sign in)
    POST /agents/{agent_id}/status       {state}               -> agent
    POST /agents/{agent_id}/accept       {session_id}          -> {token, room_url, summary}
    POST /agents/{agent_id}/decline      {session_id}
    GET  /queue                                                -> snapshot
    POST /queue/calls                    {session_id, summary, outcome, identity_result,
                                          member_id, flags}    -> {position}  (bot handoff)
    POST /queue/calls/{session_id}/end                         (caller left)
    POST /queue/calls/{session_id}/agent-left                  (QH-9, wrap_up_api.py)
    GET  /calls/{session_id}/wrap-up, POST /agents/{agent_id}/wrap-up  (P3-5, wrap_up_api.py)

Websocket /ws/queue: on connect, a {"type": "snapshot", ...} message, then every queue
event (call_queued, call_updated, call_ended, call_accepted, agent_updated, offer,
offer_missed). The same socket takes commands: sign_in, set_status, accept, decline.
Replies are {"type": "signed_in"|"status_set"|"accepted"|"declined"|"error", ...}.
The meeting token only appears in the "accepted" reply to the socket that accepted.

Bot notifier: queue events for a call become Daily app messages in that call's room
(REST /rooms/{room}/send-app-message, sender "api"), so the bot knows it is queued and
when an agent accepted. Message data: {"type": BOT_MESSAGE_TYPE, "event": ...}; events
are queued, position, agent_accepted, and requeued (QH-9: the agent dropped off).

Bot socket (QH-5b): websocket /ws/bot/{session_id}, opened by the call's bot after
handoff (bot key only when login is on). agent_accepted, requeued and call_ended are
pushed on it at once, and still sent as Daily app messages as the fallback. Both
copies carry the same "id"; the bot acts on the first and drops the second. The
socket saves the REST hop through Daily (about 240 ms) before the bot stops the hold
music. The server ignores anything the bot sends on it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from agent_assist_server.agent_token import mint_agent_token
from agent_assist_server.daily import DailyRestClient
from agent_assist_server.queue_service import (
    AcceptResult,
    Agent,
    Event,
    IntakeInfo,
    QueueError,
    QueueService,
    TokenError,
)

logger = logging.getLogger(__name__)

BOT_MESSAGE_TYPE = "agent_assist.queue"
# Pushed on the bot socket too (QH-5b); queued/position only go through Daily.
BOT_SOCKET_EVENTS = frozenset({"agent_accepted", "requeued", "call_ended"})
WS_UNKNOWN_SESSION = 4404
PERSIST_ENV = "QUEUE_PERSIST"
# After the agent's End: time for the bot's goodbye line before the room is deleted.
ROOM_CLOSE_DELAY_SECS = 6.0

router = APIRouter()


def _daily_factory(app: FastAPI):
    factory = getattr(app.state, "daily_client_factory", None)
    return factory or DailyRestClient.from_env


def get_queue(app: FastAPI) -> QueueService:
    """The process wide queue service, created on first use."""
    svc = getattr(app.state, "queue_service", None)
    if svc is None:

        async def mint(room_name: str, user_name: str) -> tuple[str, int]:
            return await mint_agent_token(_daily_factory(app)(), room_name, user_name)

        minter: PrefetchingMinter | None = None

        async def mint_prefetched(room_name: str, user_name: str) -> tuple[str, int]:
            assert minter is not None
            return await minter(room_name, user_name)

        svc = QueueService(mint_token=mint_prefetched)
        minter = PrefetchingMinter(mint, svc)
        svc.subscribe(minter.on_event)
        notifier = BotNotifier(svc, lambda: _daily_factory(app)(), get_bot_channels(app))
        svc.subscribe(notifier.on_event)
        if os.environ.get(PERSIST_ENV) == "1":
            from agent_assist_server.queue_store import QueueStore

            svc.subscribe(
                QueueStore(schema=os.environ.get("DATABASE_SCHEMA", "").strip() or None).on_event
            )
        app.state.queue_service = svc
    return svc


def get_bot_channels(app: FastAPI) -> BotChannels:
    """The process wide registry of open bot sockets (QH-5b)."""
    channels = getattr(app.state, "bot_channels", None)
    if channels is None:
        channels = BotChannels()
        app.state.bot_channels = channels
    return channels


class BotChannels:
    """Open bot sockets by session id. push() never blocks: each socket has an
    outbox its own task drains."""

    def __init__(self) -> None:
        self._outboxes: dict[str, list[asyncio.Queue]] = {}

    def register(self, session_id: str) -> asyncio.Queue:
        outbox: asyncio.Queue = asyncio.Queue()
        self._outboxes.setdefault(session_id, []).append(outbox)
        return outbox

    def unregister(self, session_id: str, outbox: asyncio.Queue) -> None:
        boxes = self._outboxes.get(session_id, [])
        if outbox in boxes:
            boxes.remove(outbox)
        if not boxes:
            self._outboxes.pop(session_id, None)

    def count(self, session_id: str) -> int:
        return len(self._outboxes.get(session_id, []))

    def push(self, session_id: str, payload: dict[str, Any]) -> bool:
        """Queue payload on every socket of the session. False if none is open."""
        boxes = self._outboxes.get(session_id, [])
        for outbox in boxes:
            outbox.put_nowait(payload)
        return bool(boxes)


class SerialWorker:
    """Runs async jobs one at a time, in submit order, on the running loop."""

    def __init__(self, name: str):
        self._name = name
        self._jobs: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None

    def submit(self, job) -> None:
        if self._task is None or self._task.done():
            self._jobs = asyncio.Queue()
            self._task = asyncio.get_running_loop().create_task(self._run())
        self._jobs.put_nowait(job)

    async def _run(self) -> None:
        while True:
            job = await self._jobs.get()
            try:
                await job()
            except Exception as e:
                logger.error("%s job failed: %s", self._name, e)


class PrefetchingMinter:
    """Mints the agent's meeting token as soon as the offer rings, so accept does not
    wait on Daily (P3-5: accept to hearing the caller under 2 s).

    The token is kept here, in memory, and handed over only by the accept call for
    that room and agent, so it still reaches only the accepting agent. A missed or
    declined offer drops it (an unused non-owner token that expires in an hour). If
    the prefetch failed or is missing, accept mints a fresh one.
    """

    def __init__(self, mint, svc: QueueService):
        self._mint = mint
        self._svc = svc
        self.pending: dict[tuple[str, str], asyncio.Task] = {}

    async def __call__(self, room_name: str, user_name: str) -> tuple[str, int]:
        task = self.pending.pop((room_name, user_name), None)
        if task is not None:
            try:
                return await task
            except Exception as e:
                logger.warning("token prefetch failed (%s); minting again", type(e).__name__)
        return await self._mint(room_name, user_name)

    def on_event(self, event: Event) -> None:
        kind = event["type"]
        if kind == "offer":
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            key = (event["call"]["room_name"], self._svc.get_agent(event["agent_id"]).name)
            self._drop(key)
            task = loop.create_task(self._mint(*key))
            # A failed prefetch is retried on accept; do not log it as unhandled.
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
            self.pending[key] = task
        elif kind == "offer_missed":
            try:
                room = self._svc.get_call(event["session_id"] or "").room_name
                name = self._svc.get_agent(event["agent_id"]).name
            except QueueError:
                return
            self._drop((room, name))
        elif kind == "call_ended":
            room = event["call"]["room_name"]
            for key in [k for k in self.pending if k[0] == room]:
                self._drop(key)

    def _drop(self, key: tuple[str, str]) -> None:
        task = self.pending.pop(key, None)
        if task is not None and not task.done():
            task.cancel()


class BotNotifier:
    """Turns queue events into Daily app messages for the call's bot.

    One Daily client with a kept-alive connection serves every message (QH-5): the
    bot stops hold music when agent_accepted arrives, so that message must not pay
    for a new TLS connection. When an offer rings, the notifier makes a cheap call
    (room presence) to open or refresh that connection, so it is warm when the agent
    accepts within the 20 s offer window.
    """

    def __init__(self, svc: QueueService, daily_factory, channels: BotChannels | None = None):
        self._svc = svc
        self._daily_factory = daily_factory
        self._channels = channels or BotChannels()
        self._daily = None
        self._worker = SerialWorker("bot notifier")
        self._closing: set[asyncio.Task] = set()
        self._next_id = 0

    def _client(self):
        if self._daily is None:
            client = self._daily_factory()
            keep = getattr(client, "keep_alive", None)
            self._daily = keep() if callable(keep) else client
        return self._daily

    def _send(self, call: dict[str, Any] | Any, data: dict[str, Any]) -> None:
        """`call` is a call dict (from an event) or a Call."""
        room_name = call["room_name"] if isinstance(call, dict) else call.room_name
        payload = {"type": BOT_MESSAGE_TYPE, **data}
        if data["event"] in BOT_SOCKET_EVENTS:
            # QH-5b: straight to the bot's socket now; Daily is the fallback copy.
            self._next_id += 1
            payload["id"] = self._next_id
            session_id = call["session_id"] if isinstance(call, dict) else call.session_id
            self._channels.push(session_id, payload)

        async def job():
            await self._client().send_app_message(room_name, payload)

        self._worker.submit(job)

    def _warm(self, room_name: str) -> None:
        async def job():
            await self._client().get_presence(room_name)

        self._worker.submit(job)

    def on_event(self, event: Event) -> None:
        kind = event["type"]
        if kind == "call_queued":
            # QH-9: a re-queued call (agent dropped) tells the bot to speak again.
            what = "requeued" if event.get("requeued") else "queued"
            self._send(event["call"], {"event": what, "position": event["position"]})
            if event.get("requeued"):
                self._send_positions(skip=event["call"]["session_id"])
        elif kind == "offer":
            self._warm(event["call"]["room_name"])
        elif kind == "call_accepted":
            agent = self._svc.get_agent(event["agent_id"])
            self._send(event["call"], {"event": "agent_accepted", "agent_name": agent.name})
            self._send_positions()
        elif kind == "call_ended":
            call = event["call"]
            if call.get("ended_by") == "agent":
                # P3-5: the agent pressed End with the caller still on the line. The
                # bot says goodbye and leaves; then the room closes, which hangs up a
                # dial-in caller and ends a browser caller's call.
                self._send(call, {"event": "call_ended"})
                self._close_room_later(call["room_name"])
            self._send_positions()

    def _close_room_later(self, room_name: str) -> None:
        async def job():
            try:
                await asyncio.sleep(ROOM_CLOSE_DELAY_SECS)
                await self._client().delete_room(room_name)
                logger.info("room %s closed after the agent ended the call", room_name)
            except Exception as e:
                logger.error("closing room %s failed: %s", room_name, type(e).__name__)

        task = asyncio.get_running_loop().create_task(job())
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)

    def _send_positions(self, skip: str | None = None) -> None:
        """The line changed: tell each waiting caller their place."""
        for i, call in enumerate(self._svc.waiting_calls(), start=1):
            if call.session_id != skip:
                self._send(call, {"event": "position", "position": i})


# ---- REST -----------------------------------------------------------------------


class SignInRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class StatusRequest(BaseModel):
    state: str


class OfferRequest(BaseModel):
    session_id: str


class EnqueueRequest(BaseModel):
    """Bot handoff body: QueueHandoff posts the P2-3 IntakeHandoff fields."""

    session_id: str
    summary: dict[str, Any] = Field(default_factory=dict)
    call_id: str | None = None
    outcome: Literal["completed", "asked_for_person", "emergency"] | None = None
    identity_result: (
        Literal["verified", "close_match", "not_verified", "not_found", "incomplete"] | None
    ) = None
    member_id: str | None = Field(default=None, max_length=64)
    flags: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        default_factory=list, max_length=20
    )

    def info(self) -> IntakeInfo:
        return IntakeInfo(
            outcome=self.outcome,
            identity_result=self.identity_result,
            member_id=self.member_id,
            flags=tuple(self.flags),
        )


def _accept_dict(res: AcceptResult) -> dict[str, Any]:
    return {
        "session_id": res.session_id,
        "room_url": res.room_url,
        "token": res.token,
        "expires_at": res.expires_at,
        "summary": res.summary,
        "outcome": res.outcome,
        "identity_result": res.identity_result,
        "member_id": res.member_id,
        "flags": list(res.flags),
    }


def _conflict(e: QueueError) -> HTTPException:
    return HTTPException(status_code=502 if isinstance(e, TokenError) else 409, detail=str(e))


@router.get("/queue")
async def queue_snapshot(request: Request) -> dict[str, Any]:
    return get_queue(request.app).snapshot()


@router.post("/agents/sign-in")
async def agent_sign_in(body: SignInRequest, request: Request) -> dict[str, Any]:
    try:
        return get_queue(request.app).sign_in(body.name).to_dict()
    except QueueError as e:
        raise _conflict(e) from None


@router.post("/agents/{agent_id}/status")
async def agent_set_status(agent_id: str, body: StatusRequest, request: Request) -> dict[str, Any]:
    try:
        return get_queue(request.app).set_status(agent_id, body.state).to_dict()
    except QueueError as e:
        raise _conflict(e) from None


@router.post("/agents/{agent_id}/accept")
async def agent_accept(agent_id: str, body: OfferRequest, request: Request) -> dict[str, Any]:
    try:
        return _accept_dict(await get_queue(request.app).accept(agent_id, body.session_id))
    except QueueError as e:
        raise _conflict(e) from None


@router.post("/agents/{agent_id}/decline")
async def agent_decline(agent_id: str, body: OfferRequest, request: Request) -> dict[str, str]:
    try:
        get_queue(request.app).decline(agent_id, body.session_id)
    except QueueError as e:
        raise _conflict(e) from None
    return {"status": "declined"}


@router.post("/queue/calls")
async def enqueue_call(body: EnqueueRequest, request: Request) -> dict[str, Any]:
    """Bot handoff: intake is done, put the call in line. Only for bots this server started."""
    svc = get_queue(request.app)
    room_url = svc.room_for(body.session_id)
    if room_url is None:
        raise HTTPException(status_code=404, detail="unknown session")
    try:
        call = svc.enqueue_call(body.session_id, room_url, body.summary, body.info())
    except QueueError as e:
        raise _conflict(e) from None
    position = [c.session_id for c in svc.waiting_calls()].index(call.session_id) + 1
    return {"session_id": call.session_id, "status": call.status.value, "position": position}


@router.post("/queue/calls/{session_id}/end")
async def end_call(session_id: str, request: Request) -> dict[str, str]:
    get_queue(request.app).end_call(session_id)
    return {"status": "ok"}


# ---- websocket --------------------------------------------------------------------


async def _handle_command(svc: QueueService, msg: Any) -> dict[str, Any]:
    if not isinstance(msg, dict):
        raise QueueError("command must be a JSON object")
    kind = msg.get("type")
    if kind == "sign_in":
        agent: Agent = svc.sign_in(str(msg.get("name", "")))
        return {"type": "signed_in", "agent": agent.to_dict()}
    if kind == "set_status":
        agent = svc.set_status(str(msg.get("agent_id")), str(msg.get("state")))
        return {"type": "status_set", "agent": agent.to_dict()}
    if kind == "accept":
        res = await svc.accept(str(msg.get("agent_id")), str(msg.get("session_id")))
        return {"type": "accepted", **_accept_dict(res)}
    if kind == "decline":
        svc.decline(str(msg.get("agent_id")), str(msg.get("session_id")))
        return {"type": "declined", "session_id": msg.get("session_id")}
    raise QueueError(f"unknown command {kind!r}")


@router.websocket("/ws/queue")
async def queue_ws(ws: WebSocket) -> None:
    svc = get_queue(ws.app)
    await ws.accept()
    outbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    listener = outbox.put_nowait
    svc.subscribe(listener)
    await outbox.put({"type": "snapshot", **svc.snapshot()})

    async def pump() -> None:
        while True:
            await ws.send_json(await outbox.get())

    sender = asyncio.create_task(pump())
    try:
        while True:
            text = await ws.receive_text()
            try:
                reply = await _handle_command(svc, json.loads(text))
            except json.JSONDecodeError:
                reply = {"type": "error", "message": "invalid JSON"}
            except QueueError as e:
                reply = {"type": "error", "message": str(e)}
            await outbox.put(reply)
    except WebSocketDisconnect:
        pass
    finally:
        svc.unsubscribe(listener)
        sender.cancel()


@router.websocket("/ws/bot/{session_id}")
async def bot_ws(ws: WebSocket, session_id: str) -> None:
    """QH-5b: the call's bot listens here for agent_accepted, requeued and call_ended."""
    svc = get_queue(ws.app)
    await ws.accept()
    if svc.room_for(session_id) is None:
        await ws.close(code=WS_UNKNOWN_SESSION)
        return
    channels = get_bot_channels(ws.app)
    outbox = channels.register(session_id)
    logger.info("bot socket open for %s", session_id)

    async def pump() -> None:
        while True:
            await ws.send_json(await outbox.get())

    sender = asyncio.create_task(pump())
    try:
        while True:
            await ws.receive_text()  # nothing to act on; this notices the close
    except WebSocketDisconnect:
        pass
    finally:
        channels.unregister(session_id, outbox)
        sender.cancel()
        logger.info("bot socket closed for %s", session_id)
