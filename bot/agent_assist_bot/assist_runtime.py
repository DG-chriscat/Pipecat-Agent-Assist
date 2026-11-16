"""Glue between the bot and the assist engine (P4-3).

`AssistSession` is created with the bot and does nothing until the server's
`agent_accepted` message. Then it:

- builds a `ConnectorSession` for the call, scoped to the verified member (an
  unverified caller gets no member scope, so member connectors return no_member);
- starts `AssistEngine` with the intake fields (date of birth is left out) and the
  intake pack as the default;
- feeds it every final transcript line from `TranscriptRelay.on_final`, through a
  queue so a slow connector never stalls the audio pipeline;
- delivers engine output as RTVI server messages addressed to the agent's Daily
  participant id only (`DailyOutputTransportMessageUrgentFrame(participant_id=...)`),
  sent straight through the output transport, never broadcast. With no agent in the
  room nothing is sent; when the agent (re)joins, the whole state is sent again;
- applies agent feedback (RTVI client messages from the agent's participant only)
  and logs it, with every card sent, to `assist_events` (`EventRecorder`);
- answers the agent's Ask bar questions (P4-6, AP-8; agent only), each as its own
  task so a slow lookup never blocks Daily events or transcript cards.

It never queues frames into the pipeline, so it has no way to make the bot speak.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any

from loguru import logger
from pipecat.transports.daily.transport import DailyOutputTransportMessageUrgentFrame

from agent_assist_bot.assist import AssistEngine, identity_gate, max_cards_from_env
from agent_assist_bot.assist_contract import parse_ask_message, parse_feedback
from agent_assist_bot.packs import Pack
from agent_assist_bot.transcript import CallRoles, rtvi_server_message

# Intake keys the assist does not need to see.
PRIVATE_INTAKE_KEYS = frozenset({"dob"})


class EventRecorder:
    """Writes assist events to the call record in order, off the hot path."""

    def __init__(self, store, call_id: Callable[[], str | None]):
        self._store = store
        self._call_id = call_id
        self._queue: asyncio.Queue[tuple[str, str | None, dict] | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def add(self, kind: str, card_id: str | None, data: dict[str, Any]) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run())
        self._queue.put_nowait((kind, card_id, dict(data)))

    async def _run(self) -> None:
        while (item := await self._queue.get()) is not None:
            call_id = self._call_id()
            if call_id is None:
                logger.warning("[assist] no calls row; event not saved")
                continue
            try:
                await self._store.add_assist_event(call_id, *item)
            except Exception as e:  # noqa: BLE001 - the live call goes on
                logger.error(f"[assist] could not save an event: {type(e).__name__}")

    async def close(self, timeout: float = 10) -> None:
        if self._task is None:
            return
        self._queue.put_nowait(None)
        try:
            await asyncio.wait_for(self._task, timeout)
        except TimeoutError:
            logger.error("[assist] saving events timed out")
        self._task = None


def connectors_from_env(call_id: str, member_id: str | None):
    """ConnectorSession on the mock DB (DATABASE_URL), or None without a database."""
    from agent_assist_bot.connectors import ConnectorSession, PostgresReader

    reader = PostgresReader.from_env()
    if reader is None:
        logger.warning("[assist] DATABASE_URL not set: assist has no data")
        return None
    return ConnectorSession(reader, call_id=call_id, member_id=member_id)


class AssistSession:
    def __init__(
        self,
        *,
        packs: Mapping[str, Pack],
        roles: CallRoles,
        output,
        store,
        intake,
        connectors_factory: Callable[[str, str | None], Any] = connectors_from_env,
        llm=None,
        llm_factory: Callable[[], Any] | None = None,
        max_cards_per_min: int | None = None,
        llm_debounce_secs: float | None = None,
        clock=None,
        trend=None,
    ):
        """`intake` is the IntakeSession (pack, intake, identity, call_id); `output` is
        the Daily output transport (anything with `send_message(frame)`). `trend` is the
        call's MoodTrend (P4-5), already holding the intake readings."""
        self.packs = dict(packs)
        self.roles = roles
        self.output = output
        self.intake = intake
        self.trend = trend
        self._connectors_factory = connectors_factory
        self._llm = llm
        self._llm_factory = llm_factory
        self._max_cards = max_cards_per_min
        self._debounce = llm_debounce_secs
        self._clock = clock
        self.recorder = EventRecorder(store, lambda: intake.call_id)
        self.engine: AssistEngine | None = None
        self._connectors = None
        self._lines: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._asks: set[asyncio.Task] = set()
        self.sent = 0

    # ---- lifecycle ----

    async def on_accept(self) -> None:
        if self.engine is not None:
            return
        identity = getattr(self.intake, "identity", None)
        # The member matched by exact member ID (verified, close_match, not_verified).
        # For anyone not fully verified the engine locks those records until the agent
        # marks the caller verified (IN-13), so the scope is set now and nothing shows.
        member_id = identity.member_id if identity is not None else None
        call_id = self.intake.call_id or "unknown"
        self._connectors = self._connectors_factory(call_id, member_id)
        if self._connectors is None:
            return
        llm = self._llm
        if llm is None and self._llm_factory is not None:
            try:
                llm = self._llm_factory()
            except Exception as e:  # noqa: BLE001 - cards still come from triggers
                logger.error(f"[assist] no assist LLM: {type(e).__name__}: {e}")
        kwargs: dict[str, Any] = {}
        if self._clock is not None:
            kwargs["clock"] = self._clock
        self.engine = AssistEngine(
            packs=self.packs,
            connectors=self._connectors,
            send=self._send,
            record=self.recorder.add,
            llm=llm,
            max_cards_per_min=self._max_cards or max_cards_from_env(),
            llm_debounce_secs=self._debounce,
            trend=self.trend,
            **kwargs,
        )
        self.engine.agent_name = self.roles.agent_name or None
        intake = {k: v for k, v in dict(self.intake.intake).items() if k not in PRIVATE_INTAKE_KEYS}
        gate = identity_gate(identity, str(self.intake.intake.get("full_name") or ""))
        await self.engine.start(intake, default_pack=self.intake.pack.id, identity=gate)
        self._worker = asyncio.get_running_loop().create_task(self._run())

    async def close(self) -> None:
        if self._worker is not None:
            self._lines.put_nowait(None)
            try:
                await asyncio.wait_for(self._worker, 10)
            except TimeoutError:
                self._worker.cancel()
            self._worker = None
        for t in list(self._asks):
            t.cancel()
        if self.engine is not None:
            await self.engine.stop()
        reader = getattr(self._connectors, "reader", None)
        if reader is not None:
            try:
                await reader.close()
            except Exception:  # noqa: BLE001
                pass
        await self.recorder.close()

    # ---- input ----

    def on_final(self, data: dict[str, Any]) -> None:
        """TranscriptRelay's final line callback (sync, on the pipeline task)."""
        if self.engine is not None and not self.engine.stopped:
            self._lines.put_nowait(data)

    async def drain(self) -> None:
        """Wait until every queued line is processed (tests)."""
        await self._lines.join()

    async def _run(self) -> None:
        while True:
            line = await self._lines.get()
            try:
                if line is None:
                    return
                await self.engine.on_line(line)
            except Exception as e:  # noqa: BLE001 - one bad line never ends assist
                logger.error(f"[assist] line failed: {type(e).__name__}: {e}")
            finally:
                self._lines.task_done()

    async def on_app_message(self, message: Any, sender: str) -> None:
        if self.engine is None:
            return
        ask = parse_ask_message(message, sender, self.roles.agent_id)
        if ask is not None:
            # An Ask may wait on a connector or the LLM: never block the Daily handler.
            task = asyncio.get_running_loop().create_task(self._ask(ask))
            self._asks.add(task)
            task.add_done_callback(self._asks.discard)
            return
        fb = parse_feedback(message, sender, self.roles.agent_id)
        if fb is None:
            return
        await self.engine.on_feedback(fb)

    async def _ask(self, ask) -> None:
        try:
            await self.engine.on_ask(ask)
        except Exception as e:  # noqa: BLE001 - one bad ask never ends assist
            logger.error(f"[assist] ask failed: {type(e).__name__}: {e}")

    async def drain_asks(self) -> None:
        """Wait for every running Ask (tests)."""
        while self._asks:
            await asyncio.gather(*list(self._asks), return_exceptions=True)

    async def on_participant_joined(self, participant_id: str) -> None:
        if self.engine is not None and participant_id == self.roles.agent_id:
            logger.info("[assist] agent joined: sending the current assist state")
            await self.engine.resync()

    # ---- output ----

    async def _send(self, data: dict[str, Any]) -> None:
        agent_id = self.roles.agent_id
        if agent_id is None:
            return  # nobody to show it to; never broadcast
        await self.output.send_message(
            DailyOutputTransportMessageUrgentFrame(
                message=rtvi_server_message(data), participant_id=agent_id
            )
        )
        self.sent += 1
