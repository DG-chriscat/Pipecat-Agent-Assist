"""Assist message contract, version 1 (docs/assist.md). P4-4 renders it.

Bot -> portal: one RTVI server message per update, sent as a Daily app message to
the agent's participant id only (same delivery as the live transcript, P3-4):

    {"label": "rtvi-ai", "type": "server-message",
     "data": {"type": "assist", "v": 1, "event": <event>, "seq": n, "ts": t, ...}}

Events: `hero`, `card_added`, `card_removed`, `plan_update`, `assist_status`, `mood`.

Portal -> bot: agent feedback as an RTVI client message (`client.sendClientMessage`):

    {"label": "rtvi-ai", "type": "client-message", "id": "...",
     "data": {"t": "assist_feedback", "d": {"v": 1, "card_id": "...",
              "action": "accept" | "dismiss" | "pin" | "unpin" | "rate",
              "rating": "up" | "down" (rate only)}}}

Ask bar (P4-6, AP-8): the agent's typed question, same envelope:

    {"label": "rtvi-ai", "type": "client-message", "id": "...",
     "data": {"t": "assist_ask", "d": {"v": 1, "ask_id": "...", "text": "..."}}}

The answer is a `card_added` (card id `ask:<ask_id>`), then one `ask_result`.

Only messages whose Daily sender is the agent's participant id count.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal

VERSION = 1
MESSAGE_TYPE = "assist"
FEEDBACK_TYPE = "assist_feedback"
ASK_TYPE = "assist_ask"
ASK_STATUSES = ("answered", "no_answer", "error")
MAX_ASK_CHARS = 300
MAX_ASK_ID_CHARS = 64
EVENTS = (
    "hero",
    "card_added",
    "card_removed",
    "plan_update",
    "assist_status",
    "mood",
    "ask_result",
    "identity",
)
# verified / not_verified: the agent's decision on the verify hero closed it (IN-13).
HERO_REASONS = (
    "new",
    "higher_rank",
    "accepted",
    "dismissed",
    "updated",
    "verified",
    "not_verified",
)
# unlocked: a locked tile is sent again with its records after Mark verified (IN-13).
REMOVE_REASONS = ("dismissed", "accepted", "promoted", "replaced", "unlocked")
# mark_verified / could_not_verify: the verify hero's two actions (IN-13).
FEEDBACK_ACTIONS = (
    "accept",
    "dismiss",
    "pin",
    "unpin",
    "rate",
    "mark_verified",
    "could_not_verify",
)
RATINGS = ("up", "down")
STATUS_STATES = ("listening", "rate_limited", "error", "stopped")
# The identity gate (IN-13): pending until the agent decides on the verify hero.
IDENTITY_STATES = ("verified", "pending", "not_verified")

CardKind = Literal["next_step", "record", "article", "alert", "tone_tip", "verify"]


@dataclass(frozen=True)
class Trigger:
    """The words that made the card: the phrase, its line, and where in the line.

    `phrase`, `start`, and `end` are None for a card the assist LLM proposed from the
    conversation as a whole (no single phrase to underline); `text` is still the line.
    """

    phrase: str | None
    text: str
    role: str
    line_id: str
    start: int | None
    end: int | None
    heard_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "phrase": self.phrase,
            "text": self.text,
            "role": self.role,
            "line_id": self.line_id,
            "start": self.start,
            "end": self.end,
            "heard_at": self.heard_at,
        }


@dataclass(frozen=True)
class Source:
    """Where the card's facts come from: a label and the record ids behind it."""

    label: str
    records: list[tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "records": [{"connector": c, "id": i} for c, i in self.records],
        }


@dataclass(frozen=True)
class Card:
    id: str
    kind: CardKind
    title: str
    pack_id: str
    rank: int
    trigger: Trigger
    source: Source
    body: dict[str, Any]
    rule_id: str | None = None
    template_id: str | None = None

    def wire_id(self, locked: bool = False) -> str:
        """The id the portal sees. A locked tile's id hides the record ids in it
        (`nbs:<rule>:CLM-...` -> `locked:<hash>`); Ask answers keep their id."""
        if not locked or self.id.startswith("ask:"):
            return self.id
        return "locked:" + hashlib.sha256(self.id.encode()).hexdigest()[:12]

    def to_dict(self, locked: bool = False) -> dict[str, Any]:
        """The card for the portal. `locked` (IN-13, caller not verified yet): kind,
        title, and trigger only; no record ids, values, or rendered text."""
        if locked:
            return {
                "id": self.wire_id(True),
                "kind": self.kind,
                "title": "Locked record",
                "pack_id": self.pack_id,
                "rank": self.rank,
                "ai_suggested": True,
                "locked": True,
                "trigger": self.trigger.to_dict(),
                "source": {"label": "Records", "records": []},
                "rule_id": None,
                "template_id": None,
                "body": {"locked": True},
            }
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "pack_id": self.pack_id,
            "rank": self.rank,
            "ai_suggested": self.kind != "verify",
            "locked": False,
            "trigger": self.trigger.to_dict(),
            "source": self.source.to_dict(),
            "rule_id": self.rule_id,
            "template_id": self.template_id,
            "body": self.body,
        }


def assist_message(event: str, *, seq: int, ts: float, **fields: Any) -> dict[str, Any]:
    if event not in EVENTS:
        raise ValueError(f"unknown assist event {event!r}")
    return {"type": MESSAGE_TYPE, "v": VERSION, "event": event, "seq": seq, "ts": ts, **fields}


def hero_message(
    card: Card | None,
    *,
    seq: int,
    ts: float,
    reason: str,
    replaces: str | None,
    locked: bool = False,
) -> dict[str, Any]:
    """The next best step. `card` None clears the hero (back to "Listening")."""
    if reason not in HERO_REASONS:
        raise ValueError(f"unknown hero reason {reason!r}")
    return assist_message(
        "hero",
        seq=seq,
        ts=ts,
        card=card.to_dict(locked) if card else None,
        reason=reason,
        replaces=replaces,
    )


def card_added_message(card: Card, *, seq: int, ts: float, locked: bool = False) -> dict[str, Any]:
    return assist_message("card_added", seq=seq, ts=ts, card=card.to_dict(locked))


def identity_message(
    state: str,
    *,
    seq: int,
    ts: float,
    result: str | None,
    checks: list[str],
    by: str | None = None,
    at: float | None = None,
) -> dict[str, Any]:
    """The identity gate (IN-13). `pending`: records are locked until the agent decides
    on the verify hero; `verified`: by intake (`by` None) or by the agent;
    `not_verified`: the agent could not verify, records stay locked."""
    if state not in IDENTITY_STATES:
        raise ValueError(f"unknown identity state {state!r}")
    return assist_message(
        "identity", seq=seq, ts=ts, state=state, result=result, checks=checks, by=by, at=at
    )


def card_removed_message(card_id: str, reason: str, *, seq: int, ts: float) -> dict[str, Any]:
    if reason not in REMOVE_REASONS:
        raise ValueError(f"unknown remove reason {reason!r}")
    return assist_message("card_removed", seq=seq, ts=ts, card_id=card_id, reason=reason)


def plan_update_message(
    pack_id: str, pack_name: str, steps: list[dict[str, Any]], *, seq: int, ts: float
) -> dict[str, Any]:
    return assist_message(
        "plan_update",
        seq=seq,
        ts=ts,
        pack_id=pack_id,
        pack_name=pack_name,
        steps=steps,
        done=sum(1 for s in steps if s["done"]),
        total=len(steps),
    )


def status_message(state: str, *, seq: int, ts: float, **fields: Any) -> dict[str, Any]:
    if state not in STATUS_STATES:
        raise ValueError(f"unknown assist state {state!r}")
    return assist_message("assist_status", seq=seq, ts=ts, state=state, **fields)


def mood_message(
    *,
    seq: int,
    ts: float,
    label: str | None,
    tone: str | None,
    step: int | None,
    readings: list[dict[str, Any]],
) -> dict[str, Any]:
    """The caller's mood trend (P4-5, AP-14): every tone reading of the call so far."""
    return assist_message(
        "mood", seq=seq, ts=ts, label=label, tone=tone, step=step, readings=readings
    )


def ask_result_message(
    ask_id: str,
    status: str,
    card_id: str | None,
    *,
    seq: int,
    ts: float,
    message: str | None = None,
) -> dict[str, Any]:
    """How an Ask ended (P4-6): `answered` (the card is `card_id`), `no_answer`, `error`."""
    if status not in ASK_STATUSES:
        raise ValueError(f"unknown ask status {status!r}")
    return assist_message(
        "ask_result",
        seq=seq,
        ts=ts,
        ask_id=ask_id,
        status=status,
        card_id=card_id,
        message=message,
    )


@dataclass(frozen=True)
class Ask:
    ask_id: str
    text: str


def _client_data(message: Any, sender: str | None, agent_id: str | None, t: str) -> dict | None:
    """The `d` of an agent's RTVI client message of type `t`, or None."""
    if agent_id is None or sender != agent_id or not isinstance(message, dict):
        return None
    if message.get("label") != "rtvi-ai" or message.get("type") != "client-message":
        return None
    data = message.get("data")
    if not isinstance(data, dict) or data.get("t") != t:
        return None
    d = data.get("d")
    if not isinstance(d, dict) or d.get("v", VERSION) != VERSION:
        return None
    return d


def parse_ask_message(message: Any, sender: str | None, agent_id: str | None) -> Ask | None:
    """The agent's typed question (P4-6, AP-8), or None. Only the agent may ask."""
    d = _client_data(message, sender, agent_id, ASK_TYPE)
    if d is None:
        return None
    ask_id, text = d.get("ask_id"), d.get("text")
    if not isinstance(ask_id, str) or not ask_id.strip() or len(ask_id) > MAX_ASK_ID_CHARS:
        return None
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_ASK_CHARS:
        return None
    return Ask(ask_id=ask_id.strip(), text=text.strip())


@dataclass(frozen=True)
class Feedback:
    card_id: str
    action: str
    rating: str | None = None


def parse_feedback(message: Any, sender: str | None, agent_id: str | None) -> Feedback | None:
    """Agent feedback from a Daily app message, or None if it is not valid feedback.

    Only the agent's own participant may send feedback; the caller and the REST API
    (sender "api") cannot.
    """
    d = _client_data(message, sender, agent_id, FEEDBACK_TYPE)
    if d is None:
        return None
    card_id, action, rating = d.get("card_id"), d.get("action"), d.get("rating")
    if not isinstance(card_id, str) or not card_id.strip() or len(card_id) > 200:
        return None
    if action not in FEEDBACK_ACTIONS:
        return None
    if action == "rate":
        if rating not in RATINGS:
            return None
    else:
        rating = None
    return Feedback(card_id=card_id, action=action, rating=rating)
