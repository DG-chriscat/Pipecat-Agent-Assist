"""The Deepgram LLM provider (P5-9, DC-11): request shaping for POST /v1/think.

Every request is recorded with an httpx MockTransport, so these tests check the exact
JSON the OpenAI client sends, not what Pipecat meant to send.
"""

from __future__ import annotations

import json

import httpx2
import pytest
from agent_assist_bot.llm import LLMConfigError, create_llm, provider_for_model
from agent_assist_bot.llm_deepgram import (
    ALLOWED_FIELDS,
    PHONELLM,
    THINK_FAST,
    DeepgramLLMService,
    SpokenTextFilter,
    shape_think_request,
)
from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.processors.aggregators.llm_context import LLMContext

REJECTED = (
    "max_tokens",
    "max_completion_tokens",
    "top_p",
    "stop",
    "n",
    "seed",
    "tool_choice",
    "response_format",
    "frequency_penalty",
    "presence_penalty",
    "logprobs",
    "user",
    "service_tier",
)


def _completion(content: str = "Hello.", tool_calls=None) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": "c1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _sse(chunks: list[dict]) -> bytes:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return body.encode()


def _chunk(delta: dict, finish=None) -> dict:
    return {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


class Recorder:
    def __init__(self, reply=None, stream_chunks=None):
        self.requests: list[httpx2.Request] = []
        self.reply = reply or _completion()
        self.stream_chunks = stream_chunks

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        if body.get("stream"):
            chunks = self.stream_chunks or [_chunk({"content": "Hi."}, "stop")]
            return httpx2.Response(
                200, content=_sse(chunks), headers={"content-type": "text/event-stream"}
            )
        return httpx2.Response(200, json=self.reply)

    @property
    def body(self) -> dict:
        return json.loads(self.requests[-1].content)


def _llm(model: str, rec: Recorder, system: str | None = "Be brief.") -> DeepgramLLMService:
    return create_llm(
        provider="deepgram",
        model=model,
        api_key="dg-test-key",
        system_instruction=system,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(rec)),
    )


def _context_with_everything() -> LLMContext:
    tools = ToolsSchema(
        standard_tools=[
            FunctionSchema(
                name="save_identity",
                description="Save the caller's identity.",
                properties={"full_name": {"type": "string"}},
                required=["full_name"],
            )
        ]
    )
    ctx = LLMContext(
        messages=[
            {"role": "developer", "content": "Ask for the caller's name."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Hi, "},
                    {"type": "text", "text": "I'm Maria."},
                ],
            },
        ],
        tools=tools,
        tool_choice="required",
    )
    return ctx


# ---- the pure request shaper ---------------------------------------------------------


def test_shaper_keeps_only_allowed_fields():
    body = {k: 1 for k in REJECTED} | {"model": PHONELLM, "messages": [], "stream": False}
    out = shape_think_request(body)
    assert set(out) <= set(ALLOWED_FIELDS)
    assert not set(REJECTED) & set(out)


def test_shaper_maps_developer_to_system_and_flattens_content():
    body = {
        "model": THINK_FAST,
        "messages": [
            {"role": "developer", "content": "Rules."},
            {
                "role": "user",
                "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
            },
            {"role": "assistant", "content": None, "tool_calls": [{"id": "t1"}]},
        ],
    }
    out = shape_think_request(body)
    assert out["messages"][0] == {"role": "system", "content": "Rules."}
    assert out["messages"][1] == {"role": "user", "content": "ab"}
    # An assistant tool call message may keep content null.
    assert out["messages"][2]["content"] is None
    # The caller's body is not modified (it is shared with the LLM context).
    assert body["messages"][0]["role"] == "developer"


def test_shaper_phonellm_opts_out_of_mip_at_temperature_zero_without_reasoning():
    out = shape_think_request(
        {"model": PHONELLM, "messages": [], "temperature": 0.7, "reasoning_effort": "high"}
    )
    assert out["mip_opt_out"] is True
    assert out["temperature"] == 0.0
    assert out["reasoning_effort"] == "none"


def test_shaper_think_fast_never_opts_out_and_drops_reasoning_effort():
    out = shape_think_request(
        {"model": THINK_FAST, "messages": [], "mip_opt_out": True, "reasoning_effort": "low"}
    )
    assert "mip_opt_out" not in out
    assert "reasoning_effort" not in out


def test_shaper_stream_options_exactly_include_usage():
    out = shape_think_request(
        {"model": PHONELLM, "messages": [], "stream": True, "stream_options": {"x": 1}}
    )
    assert out["stream_options"] == {"include_usage": True}
    out = shape_think_request(
        {
            "model": PHONELLM,
            "messages": [],
            "stream": False,
            "stream_options": {"include_usage": True},
        }
    )
    assert "stream_options" not in out


def test_shaper_drops_empty_tools():
    assert "tools" not in shape_think_request({"model": PHONELLM, "messages": [], "tools": []})


# ---- the real Pipecat request, recorded ----------------------------------------------


async def test_streaming_request_is_shaped_for_think(monkeypatch):
    rec = Recorder()
    llm = _llm(PHONELLM, rec)
    stream = await llm.get_chat_completions(_context_with_everything())
    async for _ in stream:
        pass

    req = rec.requests[0]
    assert str(req.url) == "https://agent.deepgram.com/v1/think"
    assert req.headers["authorization"] == "Token dg-test-key"
    body = rec.body
    assert set(body) <= set(ALLOWED_FIELDS), sorted(set(body) - set(ALLOWED_FIELDS))
    assert body["model"] == PHONELLM
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["mip_opt_out"] is True
    assert body["temperature"] == 0.0
    assert "tool_choice" not in body
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "system", "user"]
    assert all(isinstance(m["content"], str) for m in body["messages"])
    assert body["messages"][2]["content"] == "Hi, I'm Maria."
    assert body["tools"][0]["function"]["name"] == "save_identity"


async def test_run_inference_drops_max_tokens_and_schema_but_asks_for_json():
    rec = Recorder(reply=_completion('```json\n{"a": 1}\n```'))
    llm = _llm(THINK_FAST, rec)
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}
    reply = await llm.run_inference(
        LLMContext(messages=[{"role": "user", "content": "go"}]),
        max_tokens=50,
        system_instruction="Draft it.",
        response_schema=schema,
    )
    body = rec.body
    assert set(body) <= set(ALLOWED_FIELDS), sorted(set(body) - set(ALLOWED_FIELDS))
    assert body["stream"] is False
    assert "mip_opt_out" not in body  # Think-Fast cannot opt out
    assert "response_format" not in body
    # The schema the API cannot enforce goes into the request instead: in the system
    # message and again at the end of the last user message (phoneLLM ignored it in a
    # long system prompt alone).
    system = body["messages"][0]
    assert system["role"] == "system"
    assert system["content"].startswith("Draft it.")
    assert '"required": ["a"]' in system["content"]
    last = body["messages"][-1]
    assert last["role"] == "user" and last["content"].startswith("go")
    assert "one JSON object only" in last["content"]
    # Code fences around the JSON are removed.
    assert json.loads(reply) == {"a": 1}


async def test_run_inference_strips_think_tags():
    rec = Recorder(reply=_completion("<think>the caller wants x</think>Sure."))
    llm = _llm(PHONELLM, rec)
    reply = await llm.run_inference(LLMContext(messages=[{"role": "user", "content": "go"}]))
    assert reply == "Sure."


async def test_streamed_tool_call_reaches_function_calls(monkeypatch):
    chunks = [
        _chunk(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "save_identity", "arguments": '{"full_name":'},
                    }
                ],
            }
        ),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"Maria Alvarez"}'}}]}),
        _chunk({}, "tool_calls"),
    ]
    rec = Recorder(stream_chunks=chunks)
    llm = _llm(PHONELLM, rec)
    seen = []

    async def fake_run(calls):
        seen.extend(calls)

    monkeypatch.setattr(llm, "run_function_calls", fake_run)
    pushed = []

    async def fake_push(frame, direction=None):
        pushed.append(frame)

    monkeypatch.setattr(llm, "push_frame", fake_push)
    await llm._process_context(_context_with_everything())
    assert [(c.function_name, c.arguments) for c in seen] == [
        ("save_identity", {"full_name": "Maria Alvarez"})
    ]


async def test_streamed_text_has_think_blocks_removed(monkeypatch):
    pieces = ["<thi", "nk>plan: ask", " the name</th", "ink>What is ", "your name?"]
    rec = Recorder(stream_chunks=[_chunk({"content": p}) for p in pieces] + [_chunk({}, "stop")])
    llm = _llm(PHONELLM, rec)
    texts = []

    async def fake_push(frame, direction=None):
        if hasattr(frame, "text"):
            texts.append(frame.text)

    monkeypatch.setattr(llm, "push_frame", fake_push)
    await llm._process_context(_context_with_everything())
    assert "".join(texts) == "What is your name?"


# ---- the spoken text filter ----------------------------------------------------------


@pytest.mark.parametrize(
    "pieces, spoken",
    [
        (["Hello there."], "Hello there."),
        (["<think>x</think>Hi."], "Hi."),
        (["Hi ", "<", "think>x<", "/think> there"], "Hi  there"),
        (["a < b"], "a < b"),
        (["<reasoning>r</reasoning>ok"], "ok"),
        (["**Sure**, let's ", "go."], "Sure, let's go."),
    ],
)
def test_spoken_text_filter(pieces, spoken):
    f = SpokenTextFilter()
    out = "".join(f.feed(p) for p in pieces) + f.flush()
    assert out == spoken


def test_spoken_text_filter_drops_unclosed_think_at_end():
    f = SpokenTextFilter()
    out = f.feed("Okay. <think>still thinking") + f.flush()
    assert out == "Okay. "


# ---- factory -------------------------------------------------------------------------


def test_provider_for_model():
    assert provider_for_model(PHONELLM) == "deepgram"
    assert provider_for_model(THINK_FAST) == "deepgram"
    assert provider_for_model("claude-haiku-5-5") == "anthropic"
    assert provider_for_model(None) is None


def test_deepgram_model_picks_the_deepgram_provider(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    llm = create_llm(model=PHONELLM)
    assert isinstance(llm, DeepgramLLMService)
    assert llm._settings.model == PHONELLM


def test_deepgram_needs_a_key(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    with pytest.raises(LLMConfigError, match="DEEPGRAM_API_KEY"):
        create_llm(provider="deepgram", model=PHONELLM)


def test_deepgram_default_model_is_phonellm(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert create_llm(provider="deepgram")._settings.model == PHONELLM
