"""Real time assist after handoff (PRD "Real time assist", steps 1 to 5; P4-3).

After an agent accepts, the bot is listen only. Every final transcript line (both
roles, P3-4) goes to `AssistEngine.on_line`:

1. Pack triggers first (cheap, deterministic): the line is matched against the
   active pack's next best step rules. A triggered rule pulls the records it needs
   through the pack's allowed connectors (P4-1) and, if the rule applies, is
   rendered from those record facts (P4-2, DC-10). No LLM on this path, so a trigger
   phrase becomes a card in one connector round trip (DC-6).
2. The assist LLM (debounced, off the hot path) reads the rolling transcript window,
   the intake data, and the pack, may call the pack's connector tools, and proposes
   candidates: a rule id plus record ids, or a card template plus record ids. It
   never writes card text: the engine re-reads the records itself and renders the
   pack's templates, so the LLM only chooses which facts fill them.
3. Ranking (step 5): one hero (the next best step). A candidate replaces the hero
   only when it ranks higher, or when the hero is accepted or dismissed; everything
   else is a supporting card. Rank = rule priority x 10, +5 when the record was named
   in the call (intake or transcript), +3 when a pack alert flagged the record.
4. Rate limit (DC-7): at most `max_cards_per_min` new cards per rolling minute;
   extra cards wait (best first) and an `assist_status` tells the portal.
5. Call plan (AP-12): agent lines tick steps whose `done_when.phrases` they contain;
   accepted actions tick steps listing that action (DC-9, simulated).

Guardrail: no clinical advice. Rendered text that reads like medical advice is
never sent (`clinical_advice`). Every card carries its trigger and source.

Output goes through `send(data)` (the bot wraps it as an RTVI server message to the
agent's participant only) and `record(kind, card_id, data)` (assist_events, DC-9).
The engine has no audio path and cannot speak to the caller.

Background tasks (LLM debounce, rate limit flush) run only when
`llm_debounce_secs` is not None; tests drive `llm_pass()` and `flush()` directly.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from loguru import logger

from agent_assist_bot.assist_contract import (
    Card,
    Feedback,
    Source,
    Trigger,
    card_added_message,
    card_removed_message,
    hero_message,
    plan_update_message,
    status_message,
)
from agent_assist_bot.connectors.catalog import CONNECTORS
from agent_assist_bot.packs import (
    CardTemplate,
    NextBestStep,
    Pack,
    TriggerMatch,
    connector_facts,
    match_phrases,
    select_pack,
)

RATE_ENV = "ASSIST_MAX_CARDS_PER_MIN"
DEFAULT_MAX_CARDS_PER_MIN = 4  # DC-7
RATE_WINDOW_SECS = 60.0
WINDOW_LINES = 12
LLM_DEBOUNCE_SECS = 0.6
MAX_ROWS = 5
MAX_ARTICLES = 2
DC6_TARGET_MS = 3000

ID_FIELD = {c.name: c.id_field for c in CONNECTORS}
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")

Send = Callable[[dict[str, Any]], Awaitable[None]]
Record = Callable[[str, str | None, dict[str, Any]], Any]
RunTool = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def max_cards_from_env(env: Mapping[str, str] | None = None) -> int:
    raw = (os.environ if env is None else env).get(RATE_ENV, "").strip()
    try:
        n = int(raw)
    except ValueError:
        return DEFAULT_MAX_CARDS_PER_MIN
    return n if n > 0 else DEFAULT_MAX_CARDS_PER_MIN


# --- guardrail ---------------------------------------------------------------------------

# Medical advice patterns. Billing and authorization language ("prior authorization",
# "clinical notes", the service name on a claim) is fine; telling someone what to take,
# stop, or do about their health is not.
_CLINICAL = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(you should|you need to|i recommend|i'd recommend|try) (take|taking|stop|start|"
        r"increase|decrease|reduce|avoid)\b",
        r"\b(stop|start|keep) taking\b",
        r"\b(dosage|dose|milligrams?|\d+\s?mg)\b",
        r"\b(diagnos\w*|prognos\w*)\b",
        r"\b(ibuprofen|acetaminophen|tylenol|advil|aspirin|antibiotics?|opioids?|insulin)\b",
        r"\b(symptoms?)\b.*\b(mean|means|indicate|suggest)\b",
        r"\byou (need|should have|should get) (surgery|treatment|therapy)\b",
        r"\bneed surgery\b",
        r"\bmedical advice\b",
    )
]


def clinical_advice(text: str) -> str | None:
    """The matching pattern if `text` reads like clinical advice, else None."""
    for p in _CLINICAL:
        m = p.search(text or "")
        if m:
            return m.group(0)
    return None


# --- the LLM side ------------------------------------------------------------------------


@dataclass
class AssistRequest:
    """What the assist LLM sees: the window, intake, pack, and the pack's tools."""

    pack: Pack
    intake: dict[str, Any]
    window: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    shown: list[str] = field(default_factory=list)

    def prompt(self) -> str:
        p = self.pack
        rules = "\n".join(
            f"- {r.id} (priority {r.priority}): needs {', '.join(r.required_facts)}"
            + (f" where {json.dumps(r.when)}" if r.when else "")
            + f"; heard when: {', '.join(r.triggers)}. Action: {r.action.label}."
            for r in p.next_best_steps
        )
        cards = "\n".join(
            f"- {t.id} ({t.type}, connector {t.connector}): {t.title}" for t in p.card_templates
        )
        intake = "\n".join(f"- {k}: {v}" for k, v in self.intake.items() if v)
        window = "\n".join(f"{ln['role'].upper()}: {ln['text']}" for ln in self.window)
        shown = ", ".join(self.shown) or "none"
        return (
            f"Use case pack: {p.name} ({p.id}).\n\n"
            f"Intake (verified caller):\n{intake or '- none'}\n\n"
            f"Next best step rules:\n{rules or '- none'}\n\n"
            f"Card templates:\n{cards or '- none'}\n\n"
            f"Cards already on screen: {shown}\n\n"
            f"Transcript, oldest first:\n{window}\n"
        )


class Proposer(Protocol):
    async def propose(self, request: AssistRequest, run_tool: RunTool) -> dict[str, Any]: ...


# --- engine ------------------------------------------------------------------------------


@dataclass
class _Candidate:
    card: Card
    line_ts: float


class AssistEngine:
    def __init__(
        self,
        *,
        packs: Mapping[str, Pack],
        connectors: Any,
        send: Send,
        record: Record | None = None,
        llm: Proposer | None = None,
        clock: Callable[[], float] = time.time,
        max_cards_per_min: int = DEFAULT_MAX_CARDS_PER_MIN,
        llm_debounce_secs: float | None = LLM_DEBOUNCE_SECS,
    ):
        self.packs = dict(packs)
        self.connectors = connectors
        self.llm = llm
        self.llm_debounce_secs = llm_debounce_secs
        self.max_cards_per_min = max_cards_per_min
        self._send = send
        self._record = record or (lambda kind, card_id, data: None)
        self._clock = clock

        self.pack: Pack | None = None
        self.intake: dict[str, Any] = {}
        self.hero: Card | None = None
        self.supporting: dict[str, Card] = {}
        self.closed: set[str] = set()
        self.accepted_actions: list[str] = []
        self.plan_done: dict[str, str] = {}  # step id -> "transcript" | "action"
        self._pending: list[_Candidate] = []
        self._sent_at: deque[float] = deque()
        self._latencies: list[float] = []
        self._window: deque[dict[str, Any]] = deque(maxlen=WINDOW_LINES)
        self._seq = 0
        self._status: tuple[str, int] | None = None
        self._lock = asyncio.Lock()
        self._llm_task: asyncio.Task | None = None
        self._llm_dirty = False
        self._flush_task: asyncio.Task | None = None
        self._n_test_lines = 0
        self.stopped = False

    # ---- lifecycle ----

    async def start(self, intake: Mapping[str, Any], default_pack: str) -> None:
        """On accept: pick the pack from intake, tell the portal we are listening."""
        self.intake = {k: v for k, v in dict(intake).items() if v not in (None, "")}
        text = " ".join(str(self.intake.get(k, "")) for k in ("reason", "summary"))
        chosen = None
        if text.strip():
            chosen = select_pack(text, self.packs)
            if chosen is not None and chosen.triggers.fallback:
                chosen = None
        self.pack = chosen or self.packs.get(default_pack) or next(iter(self.packs.values()))
        logger.info(f"[assist] listening; pack={self.pack.id}")
        await self._set_status("listening", force=True)
        await self._send_plan()

    async def stop(self) -> None:
        self.stopped = True
        for t in (self._llm_task, self._flush_task):
            if t is not None and not t.done():
                t.cancel()
        self._llm_task = self._flush_task = None

    async def resync(self) -> None:
        """Send the whole current state again (the agent rejoined)."""
        if self.pack is None:
            return
        await self._set_status(self._status[0] if self._status else "listening", force=True)
        await self._send_plan()
        if self.hero is not None:
            await self._emit(hero_message(self.hero, **self._stamp(), reason="new", replaces=None))
        for card in self.supporting.values():
            await self._emit(card_added_message(card, **self._stamp()))

    # ---- input ----

    async def on_line(self, line: Mapping[str, Any]) -> None:
        """One final transcript line from either role."""
        if self.stopped or self.pack is None or not line.get("is_final", True):
            return
        entry = {
            "role": line["role"],
            "text": str(line["text"]).strip(),
            "line_id": line.get("line_id") or f"{line['role']}-?",
            "ts": float(line.get("ts") or self._clock()),
        }
        if not entry["text"]:
            return
        self._window.append(entry)
        async with self._lock:
            await self._maybe_switch_pack(entry)
            if entry["role"] == "agent":
                await self._tick_from_transcript(entry["text"])
            await self._trigger_path(entry)
        self._schedule_llm()

    async def on_feedback(self, fb: Feedback) -> None:
        async with self._lock:
            card = self._card(fb.card_id)
            if card is None:
                logger.info(f"[assist] feedback for unknown card {fb.card_id}: {fb.action}")
                return
            data: dict[str, Any] = {"kind": card.kind, "pack_id": card.pack_id}
            if fb.action == "accept":
                action = card.body.get("action") if card.kind == "next_step" else None
                if action:
                    data |= {"action_id": action["id"], "simulated": True}
                self._record("accept", card.id, data)
                logger.info(f"[assist] accepted {card.id} action={data.get('action_id')}")
                if action:
                    self.accepted_actions.append(action["id"])
                    await self._tick_from_action(action["id"])
                await self._close(card, "accepted")
            elif fb.action == "dismiss":
                self._record("dismiss", card.id, data)
                await self._close(card, "dismissed")
            elif fb.action == "rate":
                self._record("rate", card.id, data | {"rating": fb.rating})
            else:  # pin / unpin
                self._record(fb.action, card.id, data)

    # ---- LLM ----

    def window(self) -> list[dict[str, Any]]:
        return list(self._window)

    def window_add_for_test(self, role: str, text: str) -> None:
        self._n_test_lines += 1
        self._window.append(
            {
                "role": role,
                "text": text,
                "line_id": f"{role}-t{self._n_test_lines}",
                "ts": self._clock(),
            }
        )

    def _schedule_llm(self) -> None:
        if self.llm is None or self.llm_debounce_secs is None or self.stopped:
            return
        if self._llm_task is not None and not self._llm_task.done():
            self._llm_dirty = True
            return
        self._llm_task = asyncio.get_running_loop().create_task(self._llm_loop())

    async def _llm_loop(self) -> None:
        while True:
            self._llm_dirty = False
            await asyncio.sleep(self.llm_debounce_secs or 0)
            if self._llm_dirty:
                continue  # more lines arrived while waiting: debounce again
            await self.llm_pass()
            if not self._llm_dirty or self.stopped:
                return

    async def llm_pass(self) -> None:
        """One assist LLM round over the current window."""
        if self.llm is None or self.pack is None or not self._window:
            return
        pack = self.pack
        if not pack.triggers.fallback and self._window_trigger(pack.triggers.phrases) is None:
            return  # nothing in the conversation has fired a trigger yet (PRD step 3)
        allowed = {t["name"] for t in self.connectors.tool_schemas(pack.allowed_connectors)}
        request = AssistRequest(
            pack=pack,
            intake=dict(self.intake),
            window=self.window(),
            tools=self.connectors.tool_schemas(pack.allowed_connectors),
            shown=[c.id for c in self._shown()],
        )
        started = self._clock()
        line_ts = self._window[-1]["ts"]

        async def run_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
            if name not in allowed:
                return {"tool": name, "error": "not_allowed", "message": "not in this pack"}
            return await self.connectors.call(name, args or {})

        try:
            proposal = await self.llm.propose(request, run_tool)
        except Exception as e:  # noqa: BLE001 - the trigger path keeps working
            logger.error(f"[assist] LLM failed: {type(e).__name__}")
            async with self._lock:
                await self._set_status("error", message="assist model unavailable")
                await self._set_status("listening")
            return
        logger.info(f"[assist] LLM pass {(self._clock() - started) * 1000:.0f} ms")
        async with self._lock:
            if self.pack is not pack:
                return
            cands = await self._llm_candidates(proposal, line_ts)
            await self._offer(cands)

    async def _llm_candidates(self, proposal: Any, line_ts: float) -> list[_Candidate]:
        pack = self.pack
        out: list[_Candidate] = []
        if not isinstance(proposal, dict):
            return out
        for p in proposal.get("next_steps") or []:
            try:
                rule = pack.rule(str(p["rule_id"]))
                refs = [(str(r["connector"]), str(r["id"])) for r in p.get("records") or []]
            except (KeyError, TypeError, StopIteration):
                continue
            trig = self._window_trigger(rule.triggers)
            if trig is None:
                continue  # the rule's trigger words were never heard: not grounded
            facts = dict(self._intake_facts())
            records: list[tuple[str, str]] = []
            ok = True
            for conn, rid in refs:
                row = await self._get_row(conn, rid)
                if row is None:
                    ok = False
                    break
                facts |= connector_facts(conn, row)
                records.append((conn, rid))
            if not ok or not rule.applies(facts):
                continue
            card = self._render(rule, facts, records, trig)
            if card is not None:
                out.append(_Candidate(card, line_ts))
        for c in proposal.get("cards") or []:
            try:
                tpl = pack.card_template(str(c["template_id"]))
                ids = [str(i) for i in c.get("ids") or []]
            except (KeyError, TypeError, StopIteration):
                continue
            trig = self._window_trigger(pack.triggers.phrases)
            if trig is None:
                if not pack.triggers.fallback:
                    continue
                trig = self._line_trigger(self._window[-1], None)
            card = await self._template_card(tpl, trig, ids=ids)
            if card is not None:
                out.append(_Candidate(card, line_ts))
        return out

    # ---- deterministic trigger path ----

    async def _maybe_switch_pack(self, entry: dict[str, Any]) -> None:
        best = select_pack(entry["text"], self.packs)
        if best is None or best is self.pack or best.triggers.fallback:
            return
        if len(best.match_triggers(entry["text"])) <= len(self.pack.match_triggers(entry["text"])):
            return
        logger.info(f"[assist] pack {self.pack.id} -> {best.id}")
        self.pack = best
        self.plan_done = {}
        await self._set_status(self._status[0] if self._status else "listening", force=True)
        await self._send_plan()

    async def _trigger_path(self, entry: dict[str, Any]) -> None:
        """Rules first, best one offered at once (DC-6); then its record card, the
        pack's alerts, and the other triggered rules as supporting cards."""
        pack = self.pack
        ts = entry["ts"]
        steps: list[Card] = []
        for rule in pack.rules_triggered(entry["text"]):
            trig = self._line_trigger(entry, rule.match_triggers(entry["text"])[0])
            card = await self._rule_card(rule, trig)
            if card is not None:
                steps.append(card)
        steps.sort(key=lambda c: -c.rank)
        if steps:
            await self._offer([_Candidate(steps[0], ts)])
        more: list[Card] = []
        if steps:
            rec = await self._record_card_for(steps[0])
            if rec is not None:
                more.append(rec)
        pack_hits = pack.match_triggers(entry["text"])
        if pack_hits:
            trig = self._line_trigger(entry, pack_hits[0])
            for tpl in pack.card_templates:
                if tpl.type == "alert":
                    card = await self._template_card(tpl, trig)
                    if card is not None:
                        more.append(card)
        more.extend(steps[1:])
        if more:
            await self._offer([_Candidate(c, ts) for c in more])

    async def _rule_card(self, rule: NextBestStep, trig: Trigger) -> Card | None:
        """The rule rendered for the best record that satisfies it, or None."""
        base = self._intake_facts()
        conns = sorted({f.split(".", 1)[0] for f in rule.required_facts} - {"intake"})
        if len(conns) > 1:
            return None  # v1 rules read one connector
        if not conns:
            return self._render(rule, base, [], trig) if rule.applies(base) else None
        conn = conns[0]
        rows = await self._search(conn, {})
        flagged = await self._flagged_ids(conn)
        best: tuple[int, Card] | None = None
        for row in rows:
            facts = base | connector_facts(conn, row)
            if not rule.applies(facts):
                continue
            rid = str(row.get(ID_FIELD[conn]))
            card = self._render(rule, facts, [(conn, rid)], trig, flagged=rid in flagged)
            if card is not None and (best is None or card.rank > best[0]):
                best = (card.rank, card)
        return best[1] if best else None

    def _render(
        self,
        rule: NextBestStep,
        facts: Mapping[str, Any],
        records: list[tuple[str, str]],
        trig: Trigger,
        *,
        flagged: bool = False,
    ) -> Card | None:
        step = rule.render(facts)
        for text in (step.headline, step.why, step.script):
            hit = clinical_advice(text)
            if hit:
                logger.warning(f"[assist] guardrail blocked rule {rule.id}: {hit!r}")
                self._record("guardrail", None, {"rule_id": rule.id, "pattern": hit})
                return None
        rank = rule.priority * 10
        if any(self._mentioned(rid) for _, rid in records):
            rank += 5
        if flagged:
            rank += 3
        rid = records[0][1] if records else "call"
        return Card(
            id=f"nbs:{rule.id}:{rid}",
            kind="next_step",
            title="Next best step",
            pack_id=self.pack.id,
            rank=rank,
            trigger=trig,
            source=Source(label=step.source, records=records),
            rule_id=rule.id,
            body={
                "headline": step.headline,
                "why": step.why,
                "script": step.script,
                "action": {"id": step.action_id, "label": step.action_label},
            },
        )

    async def _record_card_for(self, step: Card) -> Card | None:
        """The pack's record card for the hero's record (most specific search first)."""
        if not step.source.records:
            return None
        conn, rid = step.source.records[0]
        row = await self._get_row(conn, rid)
        if row is None:
            return None
        tpls = [t for t in self.pack.card_templates if t.type == "record" and t.connector == conn]
        tpls.sort(key=lambda t: -len(t.search))
        for tpl in tpls:
            if all(str(row.get(k, "")).lower() == v.lower() for k, v in tpl.search.items()):
                return await self._template_card(tpl, step.trigger)
        return None

    async def _template_card(
        self, tpl: CardTemplate, trig: Trigger, *, ids: Sequence[str] | None = None
    ) -> Card | None:
        conn = tpl.connector
        idf = ID_FIELD[conn]
        if ids and tpl.type == "article":
            ids = list(dict.fromkeys(ids))[:MAX_ARTICLES]
            rows = [r for i in ids if (r := await self._get_row(conn, i)) is not None]
        else:
            rows = await self._search(conn, dict(tpl.search))
            if ids:
                rows = [r for r in rows if str(r.get(idf)) in ids]
        if tpl.detect is not None:
            rows = list(tpl.detect.find(rows))
        if not rows:
            return None
        rows = rows[: MAX_ARTICLES if tpl.type == "article" else MAX_ROWS]
        shown = [{f: r.get(f) for f in tpl.fields} for r in rows]
        rids = [str(r.get(idf)) for r in rows]
        summary = None
        if tpl.summary:
            summary = _PLACEHOLDER.sub(lambda m: _fmt(rows[0].get(m.group(1))), tpl.summary)
        if tpl.type == "article":
            card_id = "kb:" + "+".join(rids)
        elif tpl.type == "alert":
            card_id = f"alert:{tpl.id}:" + "+".join(rids)
        else:
            card_id = f"card:{tpl.id}"
        for text in (summary or "", *(str(v) for r in shown for v in r.values())):
            if tpl.type != "record" and clinical_advice(text):
                self._record("guardrail", card_id, {"template_id": tpl.id})
                return None
        return Card(
            id=card_id,
            kind=tpl.type,
            title=tpl.title,
            pack_id=self.pack.id,
            rank=3 if tpl.type == "alert" else 0,
            trigger=trig,
            source=Source(label=tpl.source, records=[(conn, i) for i in rids]),
            template_id=tpl.id,
            body={"fields": list(tpl.fields), "rows": shown, "summary": summary},
        )

    async def _flagged_ids(self, conn: str) -> set[str]:
        out: set[str] = set()
        for tpl in self.pack.card_templates:
            if tpl.detect is not None and tpl.connector == conn:
                rows = tpl.detect.find(await self._search(conn, dict(tpl.search)))
                out |= {str(r.get(ID_FIELD[conn])) for r in rows}
        return out

    # ---- connectors ----

    async def _search(self, conn: str, args: dict[str, Any]) -> list[dict[str, Any]]:
        if conn not in self.pack.allowed_connectors:
            return []
        res = await self.connectors.call(f"{conn}_search", args)
        return list(res.get("items") or []) if "error" not in res else []

    async def _get_row(self, conn: str, rid: str) -> dict[str, Any] | None:
        if conn not in self.pack.allowed_connectors or conn not in ID_FIELD:
            return None
        res = await self.connectors.call(f"{conn}_get", {ID_FIELD[conn]: rid})
        return res.get("item") if "error" not in res else None

    # ---- ranking, slots, sending ----

    def _shown(self) -> list[Card]:
        return ([self.hero] if self.hero else []) + list(self.supporting.values())

    def _card(self, card_id: str) -> Card | None:
        if self.hero is not None and self.hero.id == card_id:
            return self.hero
        return self.supporting.get(card_id)

    def _known(self, card_id: str) -> bool:
        return (
            card_id in self.closed
            or self._card(card_id) is not None
            or any(p.card.id == card_id for p in self._pending)
        )

    def _slot(self) -> bool:
        now = self._clock()
        while self._sent_at and now - self._sent_at[0] >= RATE_WINDOW_SECS:
            self._sent_at.popleft()
        return len(self._sent_at) < self.max_cards_per_min

    async def _offer(self, cands: list[_Candidate]) -> None:
        for c in cands:
            if self._known(c.card.id):
                continue
            if self._slot():
                await self._show_new(c)
            else:
                self._pending.append(c)
        await self._after_offer()

    async def flush(self) -> None:
        """Send waiting cards the rate limit now has room for (best first)."""
        async with self._lock:
            await self._flush_locked()

    async def _flush_locked(self) -> None:
        self._pending.sort(key=lambda c: (c.card.kind != "next_step", -c.card.rank))
        while self._pending and self._slot():
            await self._show_new(self._pending.pop(0))
        await self._after_offer()

    async def _after_offer(self) -> None:
        if self._pending:
            await self._set_status("rate_limited", pending=len(self._pending))
            self._schedule_flush()
        elif self._status and self._status[0] == "rate_limited":
            await self._set_status("listening")

    def _schedule_flush(self) -> None:
        if self.llm_debounce_secs is None or self.stopped or not self._sent_at:
            return
        if self._flush_task is not None and not self._flush_task.done():
            return
        delay = max(0.05, self._sent_at[0] + RATE_WINDOW_SECS - self._clock())

        async def later():
            await asyncio.sleep(delay)
            self._flush_task = None
            await self.flush()

        self._flush_task = asyncio.get_running_loop().create_task(later())

    async def _show_new(self, c: _Candidate) -> None:
        card = c.card
        self._sent_at.append(self._clock())
        if card.kind == "next_step" and (self.hero is None or card.rank > self.hero.rank):
            old = self.hero
            self.hero = card
            reason = "new" if old is None else "higher_rank"
            await self._emit(
                hero_message(card, **self._stamp(), reason=reason, replaces=old.id if old else None)
            )
            if old is not None:
                self.supporting[old.id] = old
                await self._emit(card_added_message(old, **self._stamp()))
        else:
            self.supporting[card.id] = card
            await self._emit(card_added_message(card, **self._stamp()))
        self._log_sent(card, c.line_ts)

    def _log_sent(self, card: Card, line_ts: float) -> None:
        ms = (self._clock() - line_ts) * 1000
        self._latencies.append(ms)
        logger.info(
            f"[assist] card {card.id} kind={card.kind} rank={card.rank} "
            f"trigger={card.trigger.phrase!r} latency_ms={ms:.0f}"
            + (" OVER DC-6" if ms > DC6_TARGET_MS else "")
        )
        self._record(
            "card_sent",
            card.id,
            {
                "kind": card.kind,
                "rank": card.rank,
                "pack_id": card.pack_id,
                "trigger_phrase": card.trigger.phrase,
                "trigger_line_id": card.trigger.line_id,
                "latency_ms": round(ms, 1),
            },
        )

    def latencies_ms(self) -> list[float]:
        return list(self._latencies)

    async def _close(self, card: Card, reason: str) -> None:
        """Accepted or dismissed: the card goes; a closed hero is replaced by the best."""
        self.closed.add(card.id)
        if self.hero is not None and card.id == self.hero.id:
            self.hero = None
            nxt = max(
                (c for c in self.supporting.values() if c.kind == "next_step"),
                key=lambda c: c.rank,
                default=None,
            )
            if nxt is None:
                pend = [p for p in self._pending if p.card.kind == "next_step"]
                if pend:
                    best = max(pend, key=lambda p: p.card.rank)
                    self._pending.remove(best)
                    nxt = best.card
                    self._log_sent(nxt, best.line_ts)
            else:
                del self.supporting[nxt.id]
                await self._emit(card_removed_message(nxt.id, "promoted", **self._stamp()))
            self.hero = nxt
            await self._emit(hero_message(nxt, **self._stamp(), reason=reason, replaces=card.id))
        else:
            self.supporting.pop(card.id, None)
            await self._emit(card_removed_message(card.id, reason, **self._stamp()))
        await self._after_offer()

    # ---- call plan ----

    async def _tick_from_transcript(self, text: str) -> None:
        new = False
        for step in self.pack.call_plan:
            if step.id in self.plan_done or not step.done_when.phrases:
                continue
            if match_phrases(text, step.done_when.phrases):
                self.plan_done[step.id] = "transcript"
                new = True
        if new:
            await self._send_plan()

    async def _tick_from_action(self, action_id: str) -> None:
        new = False
        for step in self.pack.call_plan:
            if step.id not in self.plan_done and action_id in step.done_when.actions:
                self.plan_done[step.id] = "action"
                new = True
        if new:
            await self._send_plan()

    async def _send_plan(self) -> None:
        steps = [
            {
                "id": s.id,
                "label": s.label,
                "done": s.id in self.plan_done,
                "by": self.plan_done.get(s.id),
            }
            for s in self.pack.call_plan
        ]
        await self._emit(plan_update_message(self.pack.id, self.pack.name, steps, **self._stamp()))

    # ---- helpers ----

    def _intake_facts(self) -> dict[str, Any]:
        return {f"intake.{k}": v for k, v in self.intake.items()}

    def _mentioned(self, rid: str) -> bool:
        key = _norm(rid)
        if not key:
            return False
        said = " ".join(str(v) for v in self.intake.values())
        said += " " + " ".join(ln["text"] for ln in self._window)
        return key in _norm(said)

    def _line_trigger(self, entry: Mapping[str, Any], m: TriggerMatch | None) -> Trigger:
        return Trigger(
            phrase=m.phrase if m else None,
            text=entry["text"],
            role=entry["role"],
            line_id=entry["line_id"],
            start=m.start if m else None,
            end=m.end if m else None,
            heard_at=entry["ts"],
        )

    def _window_trigger(self, phrases: Sequence[str]) -> Trigger | None:
        """The most recent window line with one of `phrases`, as a trigger."""
        for entry in reversed(self._window):
            hits = match_phrases(entry["text"], phrases)
            if hits:
                return self._line_trigger(entry, hits[0])
        return None

    def _stamp(self) -> dict[str, Any]:
        self._seq += 1
        return {"seq": self._seq, "ts": self._clock()}

    async def _emit(self, data: dict[str, Any]) -> None:
        await self._send(data)

    async def _set_status(self, state: str, *, force: bool = False, **fields: Any) -> None:
        key = (state, int(fields.get("pending", 0)))
        if not force and self._status == key:
            return
        self._status = key
        extra = {"pack_id": self.pack.id if self.pack else None} | fields
        await self._emit(status_message(state, **self._stamp(), **extra))


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def _fmt(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        return f"{v:.2f}"
    return "" if v is None else str(v)
