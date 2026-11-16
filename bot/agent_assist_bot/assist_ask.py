"""Ask bar (P4-6, AP-8): the agent types a question to the assist.

`parse_ask` reads the question for a connector ("claims", "payments", "prior
authorizations", "referrals", "calls", "plan"), a row count ("last 3"), and simple
filters ("denied", "premium"). The engine (`AssistEngine.on_ask`) then:

1. A named connector: searches it through the same `ConnectorSession` (member scoped,
   2 s timeout, cache) if the active pack allows it, and renders the rows with the
   pack's record card fields for that connector. A connector the pack does not allow
   is never called.
2. No connector named: searches the knowledge base with the question's words, if the
   pack allows `kb_articles`.
3. Otherwise the assist LLM picks a pack card template and record ids for the
   question; the engine re-reads the records and renders the template, as on the
   LLM path for transcript lines. The LLM's own words never reach the screen.

The answer is a supporting card (`ask:<ask_id>`, `trigger.role` "ask") with its
source, sent at once (asks are not rate limited: the agent asked for it), then an
`ask_result` message. No answer gives `ask_result` `no_answer` with a short reason.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from agent_assist_bot.assist_contract import Card, Source, Trigger
from agent_assist_bot.connectors.catalog import CONNECTORS, kb_terms
from agent_assist_bot.packs import Pack

MAX_ROWS = 5
MAX_ARTICLES = 2
ID_FIELD = {c.name: c.id_field for c in CONNECTORS}
SOURCES = {c.name: c.source for c in CONNECTORS}

# Which connector a question is about, first match wins (more specific first).
_CONNECTOR_WORDS: tuple[tuple[str, str], ...] = (
    ("prior_authorizations", r"prior auth\w*|pre ?auth\w*|authori[sz]ations?|auths?"),
    ("referrals", r"referrals?"),
    ("payments", r"payments?|charges?|premiums?|drafts?|refunds?|copays?"),
    ("claims", r"claims?"),
    ("interactions", r"calls|call history|interactions?|contacts"),
    ("members", r"plan|coverage|member record"),
)

# Status and kind words per connector -> search filters (connector enums).
_FILTER_WORDS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "claims": (
        (r"denied|denials?", "status", "denied"),
        (r"paid", "status", "paid"),
        (r"pending", "status", "pending_info"),
        (r"approved", "status", "approved"),
        (r"in review", "status", "in_review"),
    ),
    "payments": (
        (r"premiums?", "kind", "premium"),
        (r"copays?", "kind", "copay"),
        (r"refunds?", "kind", "refund"),
        (r"autopay", "method", "autopay"),
        (r"reversed", "status", "reversed"),
    ),
    "prior_authorizations": (
        (r"pending", "status", "pending"),
        (r"approved", "status", "approved"),
        (r"denied", "status", "denied"),
        (r"expired", "status", "expired"),
    ),
    "referrals": (
        (r"pending", "status", "pending"),
        (r"approved", "status", "approved"),
        (r"expired", "status", "expired"),
    ),
}

_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_COUNT = re.compile(r"\b(\d{1,2}|" + "|".join(_NUMBERS) + r")\b")

# Fields shown when the pack has no record card for a connector. No date of birth
# or member id: the caller panel already shows identity (masked).
DEFAULT_FIELDS: dict[str, list[str]] = {
    "members": ["plan_name", "plan_type", "coverage_start", "coverage_end"],
    "plans": ["plan_id", "name", "plan_type", "deductible", "out_of_pocket_max"],
    "claims": ["claim_id", "service_date", "provider", "service", "billed_amount", "status"],
    "payments": ["payment_id", "paid_at", "amount", "kind", "method", "status"],
    "referrals": ["referral_id", "to_provider", "specialty", "status", "valid_to"],
    "prior_authorizations": ["auth_id", "service", "provider", "status", "requested_on"],
    "interactions": ["interaction_id", "started_at", "queue", "outcome"],
    "kb_articles": ["article_id", "title", "summary"],
}
_TITLES = {
    "members": "Plan",
    "plans": "Plans",
    "claims": "Claims",
    "payments": "Payments",
    "referrals": "Referrals",
    "prior_authorizations": "Prior authorizations",
    "interactions": "Past calls",
    "kb_articles": "Knowledge base",
}


@dataclass(frozen=True)
class AskQuery:
    """A question that names a connector: search it with `filters`, show `limit` rows."""

    connector: str
    filters: dict[str, str] = field(default_factory=dict)
    limit: int = MAX_ROWS


def _has(pattern: str, text: str) -> bool:
    return re.search(rf"\b(?:{pattern})\b", text) is not None


def _first_connector(text: str) -> str | None:
    """The connector named earliest in the text (ties: the order above)."""
    best: tuple[int, int, str] | None = None
    for i, (conn, words) in enumerate(_CONNECTOR_WORDS):
        m = re.search(rf"\b(?:{words})\b", text)
        if m and (best is None or (m.start(), i) < best[:2]):
            best = (m.start(), i, conn)
    return best[2] if best else None


def parse_ask(text: str) -> AskQuery | None:
    """The connector, filters, and row count a question asks for, or None."""
    t = " ".join((text or "").lower().split())
    if not t:
        return None
    conn = _first_connector(t)
    if conn is None:
        return None
    if conn == "members":
        return AskQuery("members", {}, 1)
    filters: dict[str, str] = {}
    for words, key, value in _FILTER_WORDS.get(conn, ()):
        if key not in filters and _has(words, t):
            filters[key] = value
    limit = MAX_ROWS
    m = _COUNT.search(t)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _NUMBERS[m.group(1)]
        if n > 0:
            limit = min(n, MAX_ROWS)
    return AskQuery(conn, filters, limit)


def ask_trigger(ask_id: str, text: str, at: float) -> Trigger:
    """The \"trigger\" of an Ask card: the agent's own question (no phrase to underline)."""
    return Trigger(
        phrase=None,
        text=text,
        role="ask",
        line_id=f"ask-{ask_id}",
        start=None,
        end=None,
        heard_at=at,
    )


def record_template(pack: Pack, connector: str):
    """The pack's general record card for a connector (no fixed search first), or None."""
    tpls = [t for t in pack.card_templates if t.type == "record" and t.connector == connector]
    tpls.sort(key=lambda t: len(t.search))
    return tpls[0] if tpls else None


def article_template(pack: Pack):
    return next((t for t in pack.card_templates if t.type == "article"), None)


def rows_card(
    *,
    card_id: str,
    kind: str,
    pack: Pack,
    connector: str,
    rows: Sequence[Mapping[str, Any]],
    trigger: Trigger,
) -> Card:
    """A record or article card from connector rows, with the pack's fields if it has them."""
    tpl = article_template(pack) if kind == "article" else record_template(pack, connector)
    fields = list(tpl.fields) if tpl is not None else DEFAULT_FIELDS[connector]
    title = tpl.title if tpl is not None else _TITLES[connector]
    label = (tpl.source if tpl is not None else "") or SOURCES[connector]
    idf = ID_FIELD[connector]
    return Card(
        id=card_id,
        kind=kind,  # type: ignore[arg-type]
        title=title,
        pack_id=pack.id,
        rank=0,
        trigger=trigger,
        source=Source(label=label, records=[(connector, str(r.get(idf))) for r in rows]),
        template_id=tpl.id if tpl is not None else None,
        body={
            "fields": fields,
            "rows": [{f: r.get(f) for f in fields} for r in rows],
            "summary": None,
        },
    )


def not_allowed_message(pack: Pack, connector: str) -> str:
    return f"The {pack.name} pack does not use {_TITLES[connector].lower()}."


def no_rows_message(connector: str) -> str:
    return f"No {_TITLES[connector].lower()} found for this caller."


NO_ANSWER = "No answer from the connected systems. Try naming claims, payments, or a topic."

_TOKEN = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")


@dataclass(frozen=True)
class KbHit:
    """A KB article that a line names, and where: the longest run of the line's words
    found in the article's title or tags (no stop words at either end)."""

    row: Mapping[str, Any]
    phrase: str
    start: int
    end: int


def kb_hits(text: str, rows: Sequence[Mapping[str, Any]]) -> list[KbHit]:
    """Articles from a KB search that the text actually names (title or tag words),
    best first. Body only matches are dropped: they are too loose to show."""
    tokens = [(m.group(0).lower(), m.start(), m.end()) for m in _TOKEN.finditer(text or "")]
    out: list[tuple[int, int, KbHit]] = []
    for i, row in enumerate(rows):
        words = {
            w.lower()
            for w in _TOKEN.findall(
                " ".join([str(row.get("title") or ""), *map(str, row.get("tags") or [])])
            )
        }
        best: tuple[int, int, int] | None = None  # (content words, first, last token)
        a = 0
        while a < len(tokens):
            if tokens[a][0] not in words:
                a += 1
                continue
            b = a
            while b + 1 < len(tokens) and tokens[b + 1][0] in words:
                b += 1
            lo, hi = a, b
            while lo <= hi and not kb_terms(tokens[lo][0]):
                lo += 1
            while hi >= lo and not kb_terms(tokens[hi][0]):
                hi -= 1
            if lo <= hi:
                n = sum(1 for t in tokens[lo : hi + 1] if kb_terms(t[0]))
                if best is None or n > best[0]:
                    best = (n, lo, hi)
            a = b + 1
        if best is not None:
            n, lo, hi = best
            s, e = tokens[lo][1], tokens[hi][2]
            out.append((-n, i, KbHit(row, text[s:e], s, e)))
    return [h for _, _, h in sorted(out, key=lambda x: (x[0], x[1]))]


def is_question(text: str) -> bool:
    return bool(_QUESTION.search(text or ""))


_QUESTION = re.compile(
    r"\?|\b(what|what's|how|why|when|where|which|who)\b"
    r"|^\s*(can|could|do|does|is|are|will|would|should)\b",
    re.IGNORECASE,
)
