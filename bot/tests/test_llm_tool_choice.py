"""ToolChoiceAnthropicLLMService sends the context's tool_choice (stock 1.15 drops it)."""

import pytest
from agent_assist_bot.llm import create_llm
from agent_assist_bot.llm_anthropic import ToolChoiceAnthropicLLMService, anthropic_tool_choice
from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.processors.aggregators.llm_context import NOT_GIVEN, LLMContext


@pytest.mark.parametrize(
    "choice, want",
    [
        (NOT_GIVEN, None),
        (None, None),
        ("auto", {"type": "auto"}),
        ("required", {"type": "any"}),
        ("none", {"type": "none"}),
        (
            {"type": "function", "function": {"name": "verify_identity"}},
            {"type": "tool", "name": "verify_identity"},
        ),
    ],
)
def test_tool_choice_mapping(choice, want):
    assert anthropic_tool_choice(choice) == want


def tools():
    return ToolsSchema(
        standard_tools=[
            FunctionSchema(name="verify_identity", description="d", properties={}, required=[])
        ]
    )


@pytest.fixture
def llm(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    return create_llm()


def test_factory_builds_the_tool_choice_service(llm):
    assert isinstance(llm, ToolChoiceAnthropicLLMService)


async def test_required_is_sent_as_any(llm):
    ctx = LLMContext(messages=[{"role": "user", "content": "Correctamundo"}], tools=tools())
    ctx.set_tool_choice("required")
    params = await llm._get_llm_invocation_params(ctx)
    assert params["tool_choice"] == {"type": "any"}


async def test_unset_choice_sends_nothing(llm):
    ctx = LLMContext(messages=[{"role": "user", "content": "hi"}], tools=tools())
    params = await llm._get_llm_invocation_params(ctx)
    assert "tool_choice" not in params


async def test_no_tool_choice_without_tools(llm):
    ctx = LLMContext(messages=[{"role": "user", "content": "hi"}])
    ctx.set_tool_choice("required")
    params = await llm._get_llm_invocation_params(ctx)
    assert "tool_choice" not in params


def test_forced_choice_falls_back_to_auto_when_thinking_is_on(llm):
    params = {
        "tool_choice": {"type": "any"},
        "thinking": {"type": "enabled", "budget_tokens": 1024},
    }
    llm._maybe_apply_thinking_default(params)
    assert params["tool_choice"] == {"type": "auto"}


def test_forced_choice_kept_with_the_default_model(llm):
    # claude-haiku-5-5 runs with thinking disabled by default, so "any" is kept.
    params = {"tool_choice": {"type": "any"}}
    llm._maybe_apply_thinking_default(params)
    assert params["tool_choice"] == {"type": "any"}
