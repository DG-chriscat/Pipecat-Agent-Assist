"""Use case packs (PRD IN-4, DC-10): everything one kind of call needs, as YAML.

A builder changes what the bot asks and what the assist suggests by editing
`packs/<id>.yaml`; no code changes. See docs/packs.md for the full format.

Intake (IN-3 to IN-9): the flow's steps are fixed (greeting, identity, reason, the
pack's questions, read back). The pack supplies the wording for each step and the
list of use case questions; each question's `key` becomes a field in `calls.intake`.

Assist (P4-2, used by the assist engine after handoff):

- `triggers`: phrases that activate the pack. One pack is the `fallback`.
- `allowed_connectors`: connector names the assist LLM may call (P4-1 catalog).
- `card_templates`: record, article, and alert cards with fields and source label.
- `call_plan`: the checklist (AP-12), with `done_when` hints for auto ticks.
- `next_best_steps`: rules (DC-10) with trigger phrases, required facts, templates,
  one simulated action (DC-9), and a source.
- `keyterms`: fixed Deepgram STT keyterms for the pack (never per member).

Facts are named `intake.<key>` (core intake keys or the pack's question keys) or
`<connector>.<field>` (a field of an allowed connector's get result). Templates use
`{fact}` placeholders, and every placeholder must be a required fact of its rule.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from agent_assist_bot.connectors.catalog import CONNECTORS

# bot/agent_assist_bot/packs.py -> repo root / packs
PACKS_DIR = Path(__file__).resolve().parents[2] / "packs"
DEFAULT_PACK = "claims_status"
FALLBACK_PACK = "general_question"

# Intake fields the flow itself owns. Pack questions may not reuse these keys.
RESERVED_KEYS = frozenset({"full_name", "dob", "member_id", "reason", "summary"})
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_ID = _KEY
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_FACT = re.compile(r"^([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)$")
# Deepgram accepts up to 100 keyterms per request.
MAX_KEYTERMS = 100

CONNECTOR_FIELDS: dict[str, tuple[str, ...]] = {c.name: tuple(c.get_fields) for c in CONNECTORS}
CONNECTOR_SOURCES: dict[str, str] = {c.name: c.source for c in CONNECTORS}


class PackError(ValueError):
    """A pack is missing or invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _valid_id(v: str) -> str:
    if not _ID.match(v):
        raise ValueError(f"id {v!r} must be snake_case")
    return v


def _unique(items: Iterable[str], what: str) -> None:
    seen: set[str] = set()
    for i in items:
        if i in seen:
            raise ValueError(f"duplicate {what} {i!r}")
        seen.add(i)


# --- phrase matching -----------------------------------------------------------------


class TriggerMatch(BaseModel):
    """A phrase found in a line. `start`/`end` index the original text (underline)."""

    model_config = ConfigDict(frozen=True)

    phrase: str
    start: int
    end: int


def _phrase_regex(phrase: str) -> re.Pattern[str]:
    """Whole-word, case-insensitive match that tolerates punctuation between words.

    "CO-197" matches "co 197" and "CO197"; "where's my claim" matches "wheres my claim".
    """
    tokens = re.findall(r"[a-z0-9]+", phrase.lower())
    if not tokens:
        raise ValueError(f"phrase {phrase!r} has no words")
    body = r"[^a-z0-9]{0,3}".join(re.escape(t) for t in tokens)
    return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])", re.IGNORECASE)


def match_phrases(text: str, phrases: Sequence[str]) -> list[TriggerMatch]:
    """Every phrase found in `text`, ordered by position."""
    out = []
    for p in phrases:
        for m in _phrase_regex(p).finditer(text or ""):
            out.append(TriggerMatch(phrase=p, start=m.start(), end=m.end()))
    return sorted(out, key=lambda m: (m.start, -m.end))


def _phrases(v: list[str]) -> list[str]:
    clean = [p.strip() for p in v]
    for p in clean:
        _phrase_regex(p)
    _unique((p.lower() for p in clean), "phrase")
    return clean


# --- intake --------------------------------------------------------------------------


class StepPrompt(_Strict):
    prompt: str = Field(min_length=1)


class Question(_Strict):
    key: str
    ask: str = Field(min_length=1)
    description: str = Field(min_length=1)
    required: bool = False

    @field_validator("key")
    @classmethod
    def _key_shape(cls, v: str) -> str:
        if not _KEY.match(v):
            raise ValueError(f"question key {v!r} must be snake_case")
        if v in RESERVED_KEYS:
            raise ValueError(f"question key {v!r} is reserved for the core intake")
        return v


# --- wrap up -------------------------------------------------------------------------


class ReasonCode(_Strict):
    code: str
    label: str = Field(min_length=1, max_length=60)
    # P5-1 (AP-9): next best step action ids that suggest this code at wrap up.
    actions: list[str] = Field(default_factory=list)

    @field_validator("code")
    @classmethod
    def _code_shape(cls, v: str) -> str:
        if not _KEY.match(v) or len(v) > 64:
            raise ValueError(f"reason code {v!r} must be snake_case")
        return v


class WrapUp(_Strict):
    """Wrap up (P3-5, AP-9): the reason codes an agent picks from after a call."""

    reason_codes: list[ReasonCode] = Field(
        default_factory=lambda: [ReasonCode(code="other", label="Other")], min_length=1
    )

    @field_validator("reason_codes")
    @classmethod
    def _unique_codes(cls, v: list[ReasonCode]) -> list[ReasonCode]:
        codes = [c.code for c in v]
        if len(set(codes)) != len(codes):
            raise ValueError("duplicate reason code")
        return v

    def has_code(self, code: str) -> bool:
        return any(c.code == code for c in self.reason_codes)


# --- assist --------------------------------------------------------------------------


class Triggers(_Strict):
    phrases: list[str] = Field(default_factory=list)
    # The pack that takes any line no other pack's phrases match.
    fallback: bool = False

    @field_validator("phrases")
    @classmethod
    def _check_phrases(cls, v: list[str]) -> list[str]:
        return _phrases(v)


class Duplicate(_Strict):
    """Alert detection: rows that repeat an earlier row in the same period.

    Rows matching `where` are grouped by the `same` fields plus the `period` of
    `date_field`; within a group the earliest row is the original and the rest are
    duplicates.
    """

    kind: Literal["duplicate"] = "duplicate"
    same: list[str] = Field(min_length=1)
    date_field: str
    period: Literal["day", "month"] = "month"
    where: dict[str, str] = Field(default_factory=dict)

    @property
    def fields(self) -> set[str]:
        return {*self.same, self.date_field, *self.where}

    def find(self, rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        n = 7 if self.period == "month" else 10  # YYYY-MM or YYYY-MM-DD
        groups: dict[tuple, list[Mapping[str, Any]]] = {}
        for r in rows:
            if any(str(r.get(k, "")).lower() != v.lower() for k, v in self.where.items()):
                continue
            key = (*(str(r.get(f)) for f in self.same), _iso(r.get(self.date_field))[:n])
            groups.setdefault(key, []).append(r)
        dups = []
        for g in groups.values():
            g.sort(key=lambda r: _iso(r.get(self.date_field)))
            dups.extend(g[1:])
        return sorted(dups, key=lambda r: _iso(r.get(self.date_field)))


def _iso(v: Any) -> str:
    return v.isoformat() if hasattr(v, "isoformat") else str(v or "")


CardType = Literal["record", "article", "alert"]


class CardTemplate(_Strict):
    """A card the assist can show (AP-6): which connector, which fields, source label."""

    id: str
    type: CardType
    title: str = Field(min_length=1)
    connector: str
    fields: list[str] = Field(min_length=1)
    # Optional one line with `{field}` placeholders from `fields`.
    summary: str | None = None
    # Search arguments that select the rows for this card, e.g. {status: denied}.
    search: dict[str, str] = Field(default_factory=dict)
    # Source label shown on the card; defaults to the connector's source.
    source: str = ""
    detect: Duplicate | None = None

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        return _valid_id(v)

    @model_validator(mode="after")
    def _check(self) -> CardTemplate:
        if self.connector not in CONNECTOR_FIELDS:
            raise ValueError(
                f"card {self.id}: unknown connector {self.connector!r}; "
                f"known: {sorted(CONNECTOR_FIELDS)}"
            )
        known = set(CONNECTOR_FIELDS[self.connector])
        used = set(self.fields)
        for name in _PLACEHOLDER.findall(self.summary or ""):
            if name not in self.fields:
                raise ValueError(f"card {self.id}: summary placeholder {{{name}}} not in fields")
        if self.detect:
            used |= self.detect.fields
        bad = sorted(used - known)
        if bad:
            raise ValueError(
                f"card {self.id}: unknown {self.connector} field(s) {bad}; known: {sorted(known)}"
            )
        if self.detect and self.type != "alert":
            raise ValueError(f"card {self.id}: detect is only for alert cards")
        if not self.source:
            object.__setattr__(self, "source", CONNECTOR_SOURCES[self.connector])
        return self


class DoneWhen(_Strict):
    phrases: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)

    @field_validator("phrases")
    @classmethod
    def _check_phrases(cls, v: list[str]) -> list[str]:
        return _phrases(v)


class PlanStep(_Strict):
    id: str
    label: str = Field(min_length=1)
    done_when: DoneWhen = Field(default_factory=DoneWhen)

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        return _valid_id(v)


class Action(_Strict):
    """The one primary action of a next best step. Simulated in v1 (DC-9)."""

    id: str
    label: str = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        return _valid_id(v)


class RenderedStep(BaseModel):
    """A next best step with its templates filled from facts."""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    headline: str
    why: str
    script: str
    action_id: str
    action_label: str
    source: str


class NextBestStep(_Strict):
    """DC-10: trigger phrases, required facts, templates, one action, source."""

    id: str
    triggers: list[str] = Field(min_length=1)
    required_facts: list[str] = Field(min_length=1)
    # Fact -> expected value (case-insensitive). Keys must be required facts.
    when: dict[str, str] = Field(default_factory=dict)
    headline: str = Field(min_length=1)
    why: str = Field(min_length=1)
    script: str = Field(min_length=1)
    action: Action
    source: str = Field(min_length=1)
    # Higher ranks first when several steps apply (assist engine, PRD step 5).
    priority: int = 0

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        return _valid_id(v)

    @field_validator("triggers")
    @classmethod
    def _check_triggers(cls, v: list[str]) -> list[str]:
        return _phrases(v)

    @model_validator(mode="after")
    def _check(self) -> NextBestStep:
        _unique(self.required_facts, f"required fact in rule {self.id}:")
        for f in self.required_facts:
            if not _FACT.match(f):
                raise ValueError(
                    f"rule {self.id}: fact {f!r} must look like intake.<key> or <connector>.<field>"
                )
        for k in self.when:
            if k not in self.required_facts:
                raise ValueError(f"rule {self.id}: when key {k!r} is not in required_facts")
        for name in ("headline", "why", "script"):
            for ph in _PLACEHOLDER.findall(getattr(self, name)):
                if ph not in self.required_facts:
                    raise ValueError(
                        f"rule {self.id}: {name} placeholder {{{ph}}} is not in required_facts"
                    )
        return self

    def match_triggers(self, text: str) -> list[TriggerMatch]:
        return match_phrases(text, self.triggers)

    def applies(self, facts: Mapping[str, Any]) -> bool:
        """All required facts present (not empty) and every `when` condition holds."""
        for f in self.required_facts:
            v = facts.get(f)
            if v is None or str(v).strip() == "":
                return False
        return all(str(facts[k]).strip().lower() == v.lower() for k, v in self.when.items())

    def render(self, facts: Mapping[str, Any]) -> RenderedStep:
        def fill(t: str) -> str:
            return _PLACEHOLDER.sub(lambda m: str(facts[m.group(1)]), t)

        return RenderedStep(
            rule_id=self.id,
            headline=fill(self.headline),
            why=fill(self.why),
            script=fill(self.script),
            action_id=self.action.id,
            action_label=self.action.label,
            source=self.source,
        )


# --- pack ----------------------------------------------------------------------------


class Pack(_Strict):
    id: str
    name: str
    identity: StepPrompt
    reason: StepPrompt
    questions: list[Question] = Field(default_factory=list)
    readback: StepPrompt
    wrap_up: WrapUp = Field(default_factory=WrapUp)

    # Assist (P4-2). All optional so an intake-only pack still loads.
    triggers: Triggers = Field(default_factory=Triggers)
    allowed_connectors: list[str] = Field(default_factory=list)
    keyterms: list[str] = Field(default_factory=list, max_length=MAX_KEYTERMS)
    card_templates: list[CardTemplate] = Field(default_factory=list)
    call_plan: list[PlanStep] = Field(default_factory=list)
    next_best_steps: list[NextBestStep] = Field(default_factory=list)

    @field_validator("questions")
    @classmethod
    def _unique_keys(cls, v: list[Question]) -> list[Question]:
        _unique((q.key for q in v), "question key")
        return v

    @field_validator("allowed_connectors")
    @classmethod
    def _known_connectors(cls, v: list[str]) -> list[str]:
        for name in v:
            if name not in CONNECTOR_FIELDS:
                raise ValueError(
                    f"unknown connector {name!r} in allowed_connectors; "
                    f"known: {sorted(CONNECTOR_FIELDS)}"
                )
        _unique(v, "connector")
        return v

    @field_validator("keyterms")
    @classmethod
    def _keyterms(cls, v: list[str]) -> list[str]:
        clean = [t.strip() for t in v]
        if any(not t for t in clean):
            raise ValueError("keyterms may not be empty")
        _unique(clean, "keyterm")
        return clean

    @model_validator(mode="after")
    def _cross_refs(self) -> Pack:
        allowed = set(self.allowed_connectors)
        _unique((t.id for t in self.card_templates), "card template id")
        _unique((s.id for s in self.call_plan), "call plan step id")
        _unique((r.id for r in self.next_best_steps), "next best step id")
        _unique((r.action.id for r in self.next_best_steps), "action id")

        for t in self.card_templates:
            if t.connector not in allowed:
                raise ValueError(
                    f"card {t.id}: connector {t.connector!r} is not in allowed_connectors"
                )
        intake_keys = RESERVED_KEYS | {q.key for q in self.questions}
        for r in self.next_best_steps:
            for f in r.required_facts:
                ns, field = f.split(".", 1)  # shape checked on the rule
                if ns == "intake":
                    if field not in intake_keys:
                        raise ValueError(
                            f"rule {r.id}: fact {f!r}: {field!r} is not an intake key "
                            f"({sorted(intake_keys)})"
                        )
                elif ns not in allowed:
                    raise ValueError(
                        f"rule {r.id}: fact {f!r}: connector {ns!r} is not in allowed_connectors"
                    )
                elif field not in CONNECTOR_FIELDS[ns]:
                    raise ValueError(f"rule {r.id}: fact {f!r}: {ns} has no field {field!r}")
        actions = {r.action.id for r in self.next_best_steps}
        for s in self.call_plan:
            for a in s.done_when.actions:
                if a not in actions:
                    raise ValueError(
                        f"call plan step {s.id}: unknown action {a!r}; known: {sorted(actions)}"
                    )
        for c in self.wrap_up.reason_codes:
            for a in c.actions:
                if a not in actions:
                    raise ValueError(
                        f"reason code {c.code}: unknown action {a!r}; known: {sorted(actions)}"
                    )
        return self

    # --- helpers for the assist engine ---

    def rule(self, rule_id: str) -> NextBestStep:
        return next(r for r in self.next_best_steps if r.id == rule_id)

    def card_template(self, template_id: str) -> CardTemplate:
        return next(t for t in self.card_templates if t.id == template_id)

    def match_triggers(self, text: str) -> list[TriggerMatch]:
        """Pack trigger phrases found in `text` (cheap, deterministic)."""
        return match_phrases(text, self.triggers.phrases)

    def rules_triggered(self, text: str) -> list[NextBestStep]:
        """Next best step rules with a trigger phrase in `text`, in pack order."""
        return [r for r in self.next_best_steps if r.match_triggers(text)]

    def plan_ticks(self, transcript: str, accepted_actions: Iterable[str]) -> list[str]:
        """Call plan step ids that the transcript or an accepted action shows are done."""
        accepted = set(accepted_actions)
        return [
            s.id
            for s in self.call_plan
            if accepted & set(s.done_when.actions)
            or (s.done_when.phrases and match_phrases(transcript, s.done_when.phrases))
        ]


def connector_facts(connector: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """Facts from one connector result row: `{"<connector>.<field>": value}`."""
    if connector not in CONNECTOR_FIELDS:
        raise KeyError(f"unknown connector {connector!r}")
    return {f"{connector}.{k}": v for k, v in row.items()}


def load_pack(name_or_path: str | Path, packs_dir: Path = PACKS_DIR) -> Pack:
    """Load a pack by id (`packs/<id>.yaml`) or by file path."""
    path = Path(name_or_path)
    if path.suffix not in (".yaml", ".yml"):
        path = packs_dir / f"{name_or_path}.yaml"
    if not path.is_file():
        raise PackError(f"pack not found: {name_or_path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise PackError(f"pack {path.name} is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise PackError(f"pack {path.name} must be a mapping at the top level")
    try:
        return Pack.model_validate(data)
    except ValidationError as e:
        raise PackError(f"pack {path.name} is invalid: {e}") from e


def load_packs(packs_dir: Path = PACKS_DIR) -> dict[str, Pack]:
    """Every `<id>.yaml` in `packs_dir`, keyed by id, in file name order.

    Checks across packs: the file name matches the id, at most one fallback pack,
    and action ids are unique across packs (they are logged on the call record).
    """
    packs: dict[str, Pack] = {}
    for path in sorted(packs_dir.glob("*.yaml")):
        pack = load_pack(path)
        if pack.id != path.stem:
            raise PackError(f"pack {path.name}: id {pack.id!r} must match the file name")
        packs[pack.id] = pack
    fallbacks = [p.id for p in packs.values() if p.triggers.fallback]
    if len(fallbacks) > 1:
        raise PackError(f"only one pack may be the fallback; found {fallbacks}")
    owner: dict[str, str] = {}
    for p in packs.values():
        for r in p.next_best_steps:
            if r.action.id in owner:
                raise PackError(
                    f"action id {r.action.id!r} is in both {owner[r.action.id]} and {p.id}"
                )
            owner[r.action.id] = p.id
    return packs


def select_pack(text: str, packs: Mapping[str, Pack] | None = None) -> Pack | None:
    """The pack whose trigger phrases match `text` most; else the fallback pack.

    Ties go to the earlier pack in file name order. Returns None only when nothing
    matches and no pack is the fallback.
    """
    packs = packs if packs is not None else load_packs()
    best, best_n = None, 0
    for p in packs.values():
        if p.triggers.fallback:
            continue
        n = len(p.match_triggers(text))
        if n > best_n:
            best, best_n = p, n
    if best:
        return best
    return next((p for p in packs.values() if p.triggers.fallback), None)
