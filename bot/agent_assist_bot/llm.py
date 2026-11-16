"""Single factory for the bot's LLM service, so the provider can be swapped in one place.

Selection order for each setting: explicit argument, then environment, then default.
- LLM_PROVIDER (default "anthropic")
- LLM_MODEL (default DEFAULT_ANTHROPIC_MODEL)
- ANTHROPIC_API_KEY
"""

from __future__ import annotations

import os
from typing import Any

from pipecat.services.llm_service import LLMService

DEFAULT_PROVIDER = "anthropic"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"


class LLMConfigError(RuntimeError):
    """Raised when the LLM cannot be configured (unknown provider, missing key)."""


def create_llm(
    *,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    system_instruction: str | None = None,
    http_client: Any = None,
) -> LLMService:
    """Build the LLM service used by the bot pipeline."""
    provider = (
        provider
        or provider_for_model(model)
        or os.getenv("LLM_PROVIDER")
        or provider_for_model(os.getenv("LLM_MODEL"))
        or DEFAULT_PROVIDER
    ).lower()

    if provider == "anthropic":
        return _create_anthropic(model, api_key, system_instruction, http_client)
    if provider == "deepgram":
        return _create_deepgram(model, api_key, system_instruction, http_client)

    raise LLMConfigError(f"Unknown LLM provider: {provider!r}")


def provider_for_model(model: str | None) -> str | None:
    """The provider a model id belongs to (Settings pick models, not providers)."""
    if not model:
        return None
    from agent_assist_bot.llm_deepgram import MODELS

    return "deepgram" if model in MODELS else "anthropic"


def _create_deepgram(
    model: str | None,
    api_key: str | None,
    system_instruction: str | None,
    http_client: Any = None,
) -> LLMService:
    from agent_assist_bot.llm_deepgram import MODELS, PHONELLM, DeepgramLLMService

    api_key = api_key or os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise LLMConfigError("DEEPGRAM_API_KEY is not set")
    env_model = os.getenv("LLM_MODEL")
    model = model or (env_model if env_model in MODELS else None) or PHONELLM
    return DeepgramLLMService(
        api_key=api_key,
        http_client=http_client,
        settings=DeepgramLLMService.Settings(model=model, system_instruction=system_instruction),
    )


def _create_anthropic(
    model: str | None,
    api_key: str | None,
    system_instruction: str | None,
    http_client: Any = None,
) -> LLMService:
    from anthropic import AsyncAnthropic

    # Sends the context's tool_choice, which the stock service drops (llm_anthropic.py).
    from agent_assist_bot.llm_anthropic import ToolChoiceAnthropicLLMService as AnthropicLLMService

    api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMConfigError("ANTHROPIC_API_KEY is not set")

    client = None
    if http_client is not None:
        client = AsyncAnthropic(api_key=api_key, http_client=http_client)

    return AnthropicLLMService(
        api_key=api_key,
        client=client,
        settings=AnthropicLLMService.Settings(
            model=model or os.getenv("LLM_MODEL") or DEFAULT_ANTHROPIC_MODEL,
            system_instruction=system_instruction,
            max_tokens=300,
        ),
    )
