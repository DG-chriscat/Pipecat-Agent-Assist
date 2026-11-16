"""The assist LLM (P4-3): proposes which rules and cards fit, never their wording.

Built with `create_llm` (same provider as intake, PRD "any Pipecat supported model")
and called out of band with `run_inference(..., response_schema=...)`, so the reply
is JSON the provider enforces. Tools: the pack's connector tools are listed in the
prompt; the model asks for them in `tool_calls` and the engine runs them through
`run_tool` (pack allowed connectors only, member scoped, 2 s timeout, cached). Their
results go back for another round, at most MAX_ROUNDS rounds per pass.

The reply only names rule ids, card template ids, and record ids. The engine
re-reads every record and renders the pack's templates itself, so nothing the model
writes reaches the agent's screen.

Env: ASSIST_LLM_MODEL (default: LLM_MODEL, then the factory default).
"""

from __future__ import annotations

import json
import os
from typing import Any

from loguru import logger
from pipecat.processors.aggregators.llm_context import LLMContext

from agent_assist_bot.assist import AssistRequest, RunTool
from agent_assist_bot.llm import create_llm

MODEL_ENV = "ASSIST_LLM_MODEL"
MAX_ROUNDS = 3
MAX_TOKENS = 600
MAX_TOOL_CALLS = 4
MAX_RESULT_CHARS = 6000

SYSTEM = """\
You are the real time assist for a health plan contact center agent (Deepgram Vitals).
You read a live call between a member (CALLER) and the agent (AGENT), the intake data,
and the active use case pack. You decide which of the pack's next best step rules and
card templates would help the agent right now, and which records they are about.

Rules:
- Only suggest a next best step rule when the transcript shows its situation and the
  record you name satisfies its required facts and conditions.
- Name records by id only (claim_id, payment_id, auth_id, article_id, ...). Look them up
  with the tools first; never invent ids.
- You do not write any text for the agent. The pack's templates do.
- No clinical advice, ever: no diagnosis, treatment, or medication guidance. Records and
  approved articles only.
- Do not repeat cards already on screen. Return empty lists when nothing new helps.
- Tools: put calls in tool_calls with args_json as a JSON object string. You get the
  results and decide in the next round. Use at most a few calls.
"""

_RECORD = {
    "type": "object",
    "properties": {"connector": {"type": "string"}, "id": {"type": "string"}},
    "required": ["connector", "id"],
    "additionalProperties": False,
}
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tool_calls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "args_json": {"type": "string"}},
                "required": ["name", "args_json"],
                "additionalProperties": False,
            },
        },
        "next_steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rule_id": {"type": "string"},
                    "records": {"type": "array", "items": _RECORD},
                },
                "required": ["rule_id", "records"],
                "additionalProperties": False,
            },
        },
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "template_id": {"type": "string"},
                    "ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["template_id", "ids"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["tool_calls", "next_steps", "cards"],
    "additionalProperties": False,
}

EMPTY: dict[str, list] = {"next_steps": [], "cards": []}


def _tools_text(tools: list[dict[str, Any]]) -> str:
    lines = []
    for t in tools:
        params = t.get("parameters", {}).get("properties", {})
        args = ", ".join(f"{k}: {v.get('type', 'string')}" for k, v in params.items())
        lines.append(f"- {t['name']}({args}): {t.get('description', '')}")
    return "\n".join(lines) or "- none"


def _parse(reply: str | None) -> dict[str, Any] | None:
    try:
        data = json.loads(reply or "")
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    for k in ("tool_calls", "next_steps", "cards"):
        if not isinstance(data.get(k, []), list):
            return None
    return data


class LLMProposer:
    def __init__(self, service: Any, *, max_rounds: int = MAX_ROUNDS):
        self.service = service
        self.max_rounds = max_rounds

    @classmethod
    def from_env(cls, model: str | None = None) -> LLMProposer:
        """`model`: the call's llm_assist setting (P5-9); else ASSIST_LLM_MODEL."""
        model = model or os.getenv(MODEL_ENV) or None
        return cls(create_llm(model=model, system_instruction=SYSTEM))

    async def propose(self, request: AssistRequest, run_tool: RunTool) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": request.prompt() + "\nTools:\n" + _tools_text(request.tools),
            }
        ]
        for round_no in range(1, self.max_rounds + 1):
            reply = await self.service.run_inference(
                LLMContext(messages=list(messages)),
                max_tokens=MAX_TOKENS,
                system_instruction=SYSTEM,
                response_schema=RESPONSE_SCHEMA,
            )
            data = _parse(reply)
            if data is None:
                logger.warning(f"[assist] LLM reply not usable (round {round_no})")
                return dict(EMPTY)
            calls = data.get("tool_calls") or []
            if not calls:
                return {"next_steps": data.get("next_steps", []), "cards": data.get("cards", [])}
            if round_no == self.max_rounds:
                break
            results = []
            for c in calls[:MAX_TOOL_CALLS]:
                name = str(c.get("name", ""))
                try:
                    args = json.loads(c.get("args_json") or "{}")
                    if not isinstance(args, dict):
                        raise ValueError
                except ValueError:
                    results.append({"tool": name, "error": "bad_args", "message": "not JSON"})
                    continue
                results.append(await run_tool(name, args))
            messages.append({"role": "assistant", "content": reply})
            text = json.dumps(results, default=str)[:MAX_RESULT_CHARS]
            messages.append({"role": "user", "content": f"Tool results:\n{text}"})
        logger.warning("[assist] LLM used every round on tools; no proposal")
        return dict(EMPTY)
