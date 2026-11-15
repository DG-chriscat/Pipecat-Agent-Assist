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
    provider = (provider or os.getenv("LLM_PROVIDER") or DEFAULT_PROVIDER).lower()

    if provider == "anthropic":
        return _create_anthropic(model, api_key, system_instruction, http_client)

    raise LLMConfigError(f"Unknown LLM provider: {provider!r}")


def _create_anthropic(
    model: str | None,
    api_key: str | None,
    system_instruction: str | None,
    http_client: Any = None,
) -> LLMService:
    from anthropic import AsyncAnthropic
    from pipecat.services.anthropic.llm import AnthropicLLMService

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
