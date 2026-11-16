"""Anthropic LLM service that sends the context's tool_choice.

Pipecat 1.15's Anthropic adapter drops `LLMContext.tool_choice`: every request goes
out with Anthropic's default (auto), so the model may answer in text even when the
step must end in a function call. That is how the Nov 16 prod call stalled in
identity_confirm (the LLM said "a person will join" instead of calling
verify_identity). This subclass maps the context's choice to Anthropic's:

    "auto"                                   -> {"type": "auto"}
    "required"                               -> {"type": "any"}
    "none"                                   -> {"type": "none"}
    {"type": "function", "function": {name}} -> {"type": "tool", "name": name}

Anthropic rejects a forced tool choice when extended thinking is on, so if the model
runs with thinking enabled the forced choice falls back to auto (logged); the intake
fallback in `intake.py` still covers that case.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from pipecat.processors.aggregators.llm_context import LLMContext, is_given
from pipecat.services.anthropic.llm import AnthropicLLMService

_FORCED = ("any", "tool")


def anthropic_tool_choice(choice: Any) -> dict[str, Any] | None:
    """Anthropic tool_choice for an LLMContext tool_choice, or None to leave it unset."""
    if not is_given(choice) or choice is None:
        return None
    if choice == "required":
        return {"type": "any"}
    if choice in ("auto", "none"):
        return {"type": choice}
    if isinstance(choice, dict):
        name = (choice.get("function") or {}).get("name") or choice.get("name")
        if name:
            return {"type": "tool", "name": name}
    logger.warning(f"[llm] unsupported tool_choice {choice!r}; using auto")
    return None


def _thinking_on(params: dict[str, Any]) -> bool:
    thinking = params.get("thinking")
    return isinstance(thinking, dict) and thinking.get("type") not in (None, "disabled")


class ToolChoiceAnthropicLLMService(AnthropicLLMService):
    """AnthropicLLMService plus `tool_choice` from the context (see module doc)."""

    async def _get_llm_invocation_params(self, context: LLMContext):
        params = await super()._get_llm_invocation_params(context)
        choice = anthropic_tool_choice(context.tool_choice)
        # Anthropic only accepts tool_choice alongside tools.
        if choice is not None and params.get("tools"):
            params["tool_choice"] = choice
        return params

    def _maybe_apply_thinking_default(self, params: dict[str, Any]):
        super()._maybe_apply_thinking_default(params)
        choice = params.get("tool_choice")
        if isinstance(choice, dict) and choice.get("type") in _FORCED and _thinking_on(params):
            logger.warning("[llm] thinking is on, so a forced tool_choice is sent as auto")
            params["tool_choice"] = {"type": "auto"}
