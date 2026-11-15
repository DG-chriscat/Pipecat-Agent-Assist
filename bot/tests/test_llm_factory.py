import httpx2
import pytest
from agent_assist_bot.llm import DEFAULT_ANTHROPIC_MODEL, LLMConfigError, create_llm
from pipecat.services.anthropic.llm import AnthropicLLMService


def test_default_is_anthropic_fast_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    llm = create_llm()

    assert isinstance(llm, AnthropicLLMService)
    assert llm._settings.model == DEFAULT_ANTHROPIC_MODEL
    assert "haiku" in DEFAULT_ANTHROPIC_MODEL


def test_model_override_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-5")

    llm = create_llm()

    assert llm._settings.model == "claude-sonnet-5"


def test_explicit_args_win_over_env(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-5")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    llm = create_llm(provider="anthropic", model="claude-opus-5", api_key="k")

    assert llm._settings.model == "claude-opus-5"


def test_system_instruction_is_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    llm = create_llm(system_instruction="Be brief.")

    assert llm._settings.system_instruction == "Be brief."


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMConfigError, match="ANTHROPIC_API_KEY"):
        create_llm(provider="anthropic")


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nope")
    with pytest.raises(LLMConfigError, match="nope"):
        create_llm(api_key="k")


async def test_anthropic_client_hits_messages_api_with_model(monkeypatch):
    """The service's HTTP client sends the configured model (mocked HTTP)."""
    requests = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-5-5",
                "content": [{"type": "text", "text": "Hello"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    llm = create_llm(
        api_key="k", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    resp = await llm._client.messages.create(
        model=llm._settings.model,
        max_tokens=16,
        messages=[{"role": "user", "content": "hi"}],
    )

    assert resp.content[0].text == "Hello"
    assert requests[0].url.path == "/v1/messages"
    assert b'"model":"claude-haiku-5-5"' in requests[0].content.replace(b" ", b"")
    assert requests[0].headers["x-api-key"] == "k"
