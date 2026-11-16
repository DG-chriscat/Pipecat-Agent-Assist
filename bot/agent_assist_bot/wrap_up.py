"""Wrap up draft (P5-1, PRD AP-9): a call summary and a reason code for the agent.

When the call ends, the bot drafts what the agent would otherwise type at wrap up:

- a summary of 3 to 5 sentences, facts only, no clinical advice, that names every
  accepted action and its reference numbers (claim, authorization, payment ids);
- a reason code from the active pack's `wrap_up.reason_codes`.

Inputs (`WrapUpInputs`): the final transcript lines of the call, the intake answers,
the actions the agent accepted from the assist (with their record ids), and the call
plan with what was ticked. Date of birth and member ID never go to the LLM and never
come back in the summary (their literal values are redacted on the way in and out).

The LLM (same `create_llm` factory as intake and assist) returns JSON against a
schema whose `reason_code` is an enum of the pack's codes. The reply is checked:
an unknown code becomes the rule suggestion (`suggest_reason_code`: the last accepted
action listed under a code's `actions`, else the pack's first code); more than five
sentences are cut to five; a missing accepted-action reference is added; fewer than
three sentences, clinical advice, an unusable reply, an error, a timeout, or no LLM
at all give `fallback_summary`, written from the same facts without an LLM.

The agent edits the draft and saves it (server `wrap_up_api.py`).

Env: WRAP_UP_LLM_MODEL (default: ASSIST_LLM_MODEL, then LLM_MODEL, then the factory
default).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from pipecat.processors.aggregators.llm_context import LLMContext

from agent_assist_bot.assist import clinical_advice
from agent_assist_bot.packs import Pack

MODEL_ENV = "WRAP_UP_LLM_MODEL"
FALLBACK_MODEL_ENV = "ASSIST_LLM_MODEL"
MAX_TOKENS = 400
TIMEOUT_SECS = 15.0
MAX_TRANSCRIPT_CHARS = 6000
MIN_SENTENCES = 3
MAX_SENTENCES = 5
# Intake keys whose values never reach the LLM or the summary.
PRIVATE_INTAKE_KEYS = ("dob", "member_id")
# Record ids worth quoting: CLM-900001, PA-500101, PAY-000123. Not KB-CL-002 (an
# article, not a reference) and not a member ID (no dash).
_REF = re.compile(r"\b[A-Z]{2,4}-\d{4,}\b")
# Any id-shaped token, including a short one (CLM-900), for fix_references.
_PARTIAL_REF = re.compile(r"\b[A-Z]{2,4}-\d+\b")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")

SYSTEM = """\
You write the after call summary for a health plan contact center agent (Deepgram
Vitals). You get the call transcript, the intake answers, the actions the agent took,
the call plan, and the reason codes of the use case pack.

Rules:
- 3 to 5 sentences, plain past tense, third person ("The agent", the caller's name).
- Facts only, from the inputs. Do not guess or add anything that was not said or done.
- Mention every action the agent took and its reference numbers exactly as given.
- Use reference numbers only from the Reference numbers list, written exactly.
- Intake answers come from speech recognition and may be partial or wrong; when they
  disagree with the actions' records, use the records. Do not comment on missing or
  unclear intake answers.
- Say what is still open or what happens next when the call shows it.
- No clinical advice: no diagnosis, treatment, dosage, or medication guidance.
- Never include a date of birth or a member ID.
- reason_code: the single code from the list that best fits how the call ended.
"""


@dataclass(frozen=True)
class AcceptedAction:
    """A next best step the agent accepted (simulated, DC-9)."""

    id: str
    label: str
    records: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanStepState:
    id: str
    label: str
    done: bool


@dataclass
class WrapUpInputs:
    pack: Pack
    intake: Mapping[str, Any]
    transcript: Sequence[Mapping[str, Any]] = ()
    accepted: Sequence[AcceptedAction] = ()
    plan: Sequence[PlanStepState] = ()

    def private_values(self) -> list[str]:
        return [
            str(self.intake[k]).strip()
            for k in PRIVATE_INTAKE_KEYS
            if str(self.intake.get(k) or "").strip()
        ]


@dataclass(frozen=True)
class WrapUpDraft:
    summary: str
    reason_code: str
    pack_id: str
    source: str  # "llm" or "fallback"
    inputs: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "reason_code": self.reason_code,
            "pack_id": self.pack_id,
            "source": self.source,
        }


# ---- deterministic parts -----------------------------------------------------------


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split((text or "").strip()) if s.strip()]


def sentence_count(text: str) -> int:
    return len(sentences(text))


def suggest_reason_code(pack: Pack, accepted: Sequence[AcceptedAction]) -> str:
    """The code listing the last accepted action, else the pack's first code."""
    codes = pack.wrap_up.reason_codes
    for action in reversed(list(accepted)):
        for c in codes:
            if action.id in c.actions:
                return c.code
    return codes[0].code


def reference_numbers(inputs: WrapUpInputs) -> list[str]:
    """Reference ids from the accepted actions, intake, and transcript, in that order."""
    private = set(inputs.private_values())
    seen: list[str] = []

    def add(ref: str) -> None:
        if ref not in seen and ref not in private:
            seen.append(ref)

    for a in inputs.accepted:
        for r in a.records:
            add(r)
    for k, v in inputs.intake.items():
        if k not in PRIVATE_INTAKE_KEYS and k != "summary":
            for m in _REF.findall(str(v or "")):
                add(m)
    for line in inputs.transcript:
        for m in _REF.findall(str(line.get("text") or "")):
            add(m)
    return seen


def fix_references(text: str, known: Sequence[str]) -> str:
    """Expand a partial reference (CLM-900, heard short at intake) to the one known
    record id it is a prefix of. Unknown or ambiguous ids are left alone."""

    def repl(m: re.Match) -> str:
        ref = m.group(0)
        if ref in known:
            return ref
        hits = [k for k in known if k.startswith(ref)]
        return hits[0] if len(hits) == 1 else ref

    return _PARTIAL_REF.sub(repl, text)


def _action_text(a: AcceptedAction) -> str:
    return f"{a.label} ({', '.join(a.records)})" if a.records else a.label


def _clause(text: str) -> str:
    return str(text or "").strip().rstrip(".!? ")


def _lower_first(text: str) -> str:
    """Lower the first letter of a sentence-start word, never of an acronym (MRI)."""
    if len(text) > 1 and text[1].isupper():
        return text
    return text[:1].lower() + text[1:]


def fallback_summary(inputs: WrapUpInputs) -> str:
    """3 to 5 plain sentences from the facts alone (no LLM)."""
    name = _clause(inputs.intake.get("full_name")) or "The caller"
    reason = _clause(inputs.intake.get("reason"))
    out = [
        f"{name} called; reason given: {reason}." if reason else f"{name} called with a question."
    ]
    done = [_lower_first(s.label) for s in inputs.plan if s.done]
    if done:
        out.append(f"The agent covered: {'; '.join(done)}.")
    else:
        out.append(f"The call was handled with the {inputs.pack.name} pack.")
    if inputs.accepted:
        out.append(f"Actions taken: {'; '.join(_action_text(a) for a in inputs.accepted)}.")
    else:
        out.append("No actions were taken from the assist.")
    quoted = {r for a in inputs.accepted for r in a.records}
    extra = [r for r in reference_numbers(inputs) if r not in quoted]
    if extra:
        out.append(f"References mentioned: {', '.join(extra)}.")
    still = [_lower_first(s.label) for s in inputs.plan if not s.done]
    if still:
        out.append(f"Still open: {'; '.join(still)}.")
    return " ".join(out[:MAX_SENTENCES])


def _redact(text: str, private: Sequence[str]) -> str:
    for v in private:
        text = text.replace(v, "[redacted]")
    return text


# ---- LLM ---------------------------------------------------------------------------


def response_schema(pack: Pack) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "reason_code": {"type": "string", "enum": [c.code for c in pack.wrap_up.reason_codes]},
        },
        "required": ["summary", "reason_code"],
        "additionalProperties": False,
    }


def _transcript_text(lines: Sequence[Mapping[str, Any]]) -> str:
    """The latest lines that fit in MAX_TRANSCRIPT_CHARS, oldest first."""
    kept: list[str] = []
    used = 0
    for line in reversed(list(lines)):
        text = str(line.get("text") or "").strip()
        if not text:
            continue
        row = f"{str(line.get('role') or 'caller').upper()}: {text}"
        if used + len(row) + 1 > MAX_TRANSCRIPT_CHARS:
            break
        kept.append(row)
        used += len(row) + 1
    return "\n".join(reversed(kept))


def build_prompt(inputs: WrapUpInputs) -> str:
    pack = inputs.pack
    intake = "\n".join(
        f"- {k}: {v}"
        for k, v in inputs.intake.items()
        if k not in PRIVATE_INTAKE_KEYS and v not in (None, "")
    )
    actions = "\n".join(f"- {_action_text(a)}" for a in inputs.accepted)
    plan = "\n".join(f"- [{'x' if s.done else ' '}] {s.label}" for s in inputs.plan)
    codes = "\n".join(f"- {c.code}: {c.label}" for c in pack.wrap_up.reason_codes)
    refs = ", ".join(reference_numbers(inputs)) or "none"
    text = (
        f"Use case pack: {pack.name} ({pack.id}).\n\n"
        f"Intake answers:\n{intake or '- none'}\n\n"
        f"Actions the agent took (simulated, logged on the call):\n{actions or '- none'}\n\n"
        f"Call plan ([x] done):\n{plan or '- none'}\n\n"
        f"Reference numbers list: {refs}\n\n"
        f"Reason codes:\n{codes}\n"
        f"Suggested from the actions: {suggest_reason_code(pack, inputs.accepted)}\n\n"
        f"Transcript, oldest first:\n{_transcript_text(inputs.transcript) or '- nothing'}\n"
    )
    return _redact(text, inputs.private_values())


def _parse(reply: Any) -> tuple[str, str] | None:
    try:
        data = json.loads(reply or "")
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    summary, code = data.get("summary"), data.get("reason_code")
    if not isinstance(summary, str) or not isinstance(code, str) or not summary.strip():
        return None
    return summary.strip(), code.strip()


def check_summary(text: str, inputs: WrapUpInputs) -> str | None:
    """The LLM's summary made safe, or None to use the fallback."""
    private = inputs.private_values()
    parts = [s for s in sentences(text) if not any(v in s for v in private)]
    if len(parts) < MIN_SENTENCES:
        return None
    hit = clinical_advice(" ".join(parts))
    if hit:
        logger.warning(f"[wrap_up] draft blocked as clinical advice: {hit!r}")
        return None
    record_ids = [r for a in inputs.accepted for r in a.records]
    parts = [fix_references(s, record_ids) for s in parts]
    body = " ".join(parts)
    missing = [r for r in record_ids if r not in body]
    if missing:
        parts = parts[: MAX_SENTENCES - 1]
        parts.append(f"Reference numbers: {', '.join(dict.fromkeys(missing))}.")
    return " ".join(parts[:MAX_SENTENCES])


class WrapUpDrafter:
    def __init__(self, service: Any | None, *, timeout: float = TIMEOUT_SECS):
        self.service = service
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> WrapUpDrafter:
        from agent_assist_bot.llm import create_llm

        model = os.getenv(MODEL_ENV) or os.getenv(FALLBACK_MODEL_ENV) or None
        try:
            return cls(create_llm(model=model, system_instruction=SYSTEM))
        except Exception as e:  # noqa: BLE001 - the fallback still drafts
            logger.error(f"[wrap_up] no LLM: {type(e).__name__}: {e}")
            return cls(None)

    async def _ask(self, inputs: WrapUpInputs) -> tuple[str, str] | None:
        if self.service is None:
            return None
        try:
            reply = await asyncio.wait_for(
                self.service.run_inference(
                    LLMContext(messages=[{"role": "user", "content": build_prompt(inputs)}]),
                    max_tokens=MAX_TOKENS,
                    system_instruction=SYSTEM,
                    response_schema=response_schema(inputs.pack),
                ),
                self.timeout,
            )
        except Exception as e:  # noqa: BLE001 - the fallback still drafts
            logger.error(f"[wrap_up] LLM failed: {type(e).__name__}: {e}")
            return None
        parsed = _parse(reply)
        if parsed is None:
            logger.warning("[wrap_up] LLM reply not usable")
        return parsed

    async def draft(self, inputs: WrapUpInputs) -> WrapUpDraft:
        pack = inputs.pack
        suggested = suggest_reason_code(pack, inputs.accepted)
        parsed = await self._ask(inputs)
        summary, source, code = None, "fallback", suggested
        if parsed is not None:
            text, llm_code = parsed
            if pack.wrap_up.has_code(llm_code):
                code = llm_code
            summary = check_summary(text, inputs)
            if summary is not None:
                source = "llm"
        if summary is None:
            summary = fallback_summary(inputs)
        summary = _redact(summary, inputs.private_values())
        logger.info(f"[wrap_up] drafted ({source}) code={code} sentences={sentence_count(summary)}")
        return WrapUpDraft(
            summary=summary,
            reason_code=code,
            pack_id=pack.id,
            source=source,
        )


PostDraft = Callable[[str, dict[str, Any]], Awaitable[None]]
# Drafts in flight in this process; run_bot waits for them before it exits.
_PENDING: set[asyncio.Task] = set()


async def drain_pending(timeout: float = TIMEOUT_SECS + 10) -> None:
    """Wait for every wrap up draft still in flight (the bot is about to exit)."""
    tasks = [t for t in _PENDING if not t.done()]
    if not tasks:
        return
    logger.info(f"[wrap_up] waiting for {len(tasks)} draft(s) before exit")
    _, late = await asyncio.wait(tasks, timeout=timeout)
    if late:
        logger.error("[wrap_up] draft timed out at exit")


class WrapUpReporter:
    """Drafts once per call when it ends and posts the draft to the server.

    `start` is sync (called from the end paths); `wait` is awaited before the bot
    process exits so the draft is not lost when the pipeline stops.
    """

    def __init__(self, drafter_factory: Callable[[], WrapUpDrafter], post: PostDraft):
        self._factory = drafter_factory
        self._post = post
        self._task: asyncio.Task | None = None

    def start(self, session_id: str, inputs: WrapUpInputs) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run(session_id, inputs))
            _PENDING.add(self._task)
            self._task.add_done_callback(_PENDING.discard)

    async def _run(self, session_id: str, inputs: WrapUpInputs) -> None:
        try:
            draft = await self._factory().draft(inputs)
            await self._post(session_id, draft.to_payload())
        except Exception as e:  # noqa: BLE001 - the agent can still write it by hand
            logger.error(f"[wrap_up] draft not sent: {type(e).__name__}: {e}")

    async def wait(self, timeout: float = TIMEOUT_SECS + 10) -> None:
        if self._task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout)
        except TimeoutError:
            logger.error("[wrap_up] draft timed out")
