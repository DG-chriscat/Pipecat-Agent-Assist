"""Deepgram LLMs (P5-9, PRD DC-11): phoneLLM mini and Think-Fast on POST /v1/think.

Early Access (developers.deepgram.com/docs/llm): free, contract customers only, not for
production traffic, global endpoint only, model ids may change.

/v1/think speaks the OpenAI Chat Completions format, so this is Pipecat's OpenAI
service with a client that reshapes every request before it leaves:

- URL https://agent.deepgram.com/v1/think, `Authorization: Token <DEEPGRAM_API_KEY>`
  (not Bearer).
- Only the fields /v1/think accepts (ALLOWED_FIELDS); it rejects any other top level
  field with 400. Pipecat sends tool_choice, max_tokens, max_completion_tokens,
  response_format, and so on: dropped.
- `developer` messages become `system` (Think-Fast rejects `developer`); content parts
  arrays become one plain string.
- `stream_options` is exactly {"include_usage": true} when streaming, absent otherwise.
- phoneLLM: `mip_opt_out` true (not stored for model training), temperature 0.0, and
  `reasoning_effort` "none" (other values put reasoning text into the spoken reply).
- Think-Fast: never `mip_opt_out` (400: it is always stored for training), no
  `reasoning_effort`.

Shaping happens in the client (`_prepare_options`) rather than in Pipecat's param
builder, so the streaming path, `run_inference`, and anything else that goes through
the OpenAI client all get it.

What the API cannot do, and what this does instead:
- No tool_choice: the intake confirm nodes cannot force a function call. The intake
  fallback (caller said yes and no function ran) and the watchdog still cover them.
- No response_format: `run_inference(response_schema=...)` puts the schema in the system
  message and asks for JSON only; the reply is cleaned (think blocks, code fences).
- Spoken text: `SpokenTextFilter` drops <think>/<reasoning> blocks and markdown marks
  from streamed text before it reaches TTS.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger
from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.openai.base_llm import BaseOpenAILLMService
from pipecat.services.openai.llm import OpenAILLMService

BASE_URL = "https://agent.deepgram.com/v1"
THINK_URL = f"{BASE_URL}/think"
PHONELLM = "phonellm-mini-alpha-2-fp8"
THINK_FAST = "think-fast"
MODELS = (PHONELLM, THINK_FAST)
# Models that can opt out of the Model Improvement Program (Think-Fast cannot).
MIP_OPT_OUT_MODELS = (PHONELLM,)

ALLOWED_FIELDS = (
    "model",
    "messages",
    "temperature",
    "reasoning_effort",
    "tools",
    "stream",
    "stream_options",
    "mip_opt_out",
)
_MESSAGE_ROLES = ("system", "user", "assistant", "tool")


def _given(value: Any) -> bool:
    from openai import NotGiven, Omit

    return not isinstance(value, NotGiven | Omit)


def _plain(content: Any) -> Any:
    """Content parts -> one string. Strings and None (assistant tool calls) pass."""
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                out.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                out.append(part)
            else:
                logger.warning("[llm] deepgram: non-text content part dropped")
        return "".join(out)
    return content


def _message(msg: Any) -> dict[str, Any]:
    out = dict(msg)
    if out.get("role") == "developer":
        out["role"] = "system"
    elif out.get("role") not in _MESSAGE_ROLES:
        logger.warning(f"[llm] deepgram: role {out.get('role')!r} sent as user")
        out["role"] = "user"
    if "content" in out:
        out["content"] = _plain(out["content"])
    elif out["role"] != "assistant":
        out["content"] = ""
    return out


def shape_think_request(body: dict[str, Any]) -> dict[str, Any]:
    """A Chat Completions body made acceptable to /v1/think (see module doc)."""
    model = body.get("model")
    out = {k: v for k, v in body.items() if k in ALLOWED_FIELDS and _given(v) and v is not None}
    out["messages"] = [_message(m) for m in body.get("messages") or []]
    if not out.get("tools"):
        out.pop("tools", None)
    if out.get("stream"):
        out["stream_options"] = {"include_usage": True}
    else:
        out.pop("stream_options", None)
    if model in MIP_OPT_OUT_MODELS:
        out["mip_opt_out"] = True
        out["temperature"] = 0.0
        out["reasoning_effort"] = "none"
    else:
        out.pop("mip_opt_out", None)
        out.pop("reasoning_effort", None)
    dropped = sorted(k for k in body if k not in out and _given(body[k]) and body[k] is not None)
    if dropped:
        logger.trace(f"[llm] deepgram: dropped {dropped}")
    return out


class ThinkClient(AsyncOpenAI):
    """AsyncOpenAI that sends chat completions to /v1/think, shaped for it."""

    async def _prepare_options(self, options):
        options = await super()._prepare_options(options)
        if options.url.rstrip("/").endswith("/chat/completions"):
            options.url = THINK_URL
            body = dict(options.json_data or {})
            if options.extra_json:
                body.update(options.extra_json)
                options.extra_json = None
            options.json_data = shape_think_request(body)
        return options


# ---- spoken text ---------------------------------------------------------------------

_BLOCKS = {"<think>": "</think>", "<reasoning>": "</reasoning>"}
_MARKS = re.compile(r"[*`#]")


class SpokenTextFilter:
    """Streamed LLM text -> what may be spoken: <think>/<reasoning> blocks removed (also
    when a tag is split across chunks), markdown marks (* ` #) removed."""

    def __init__(self) -> None:
        self._buf = ""
        self._close: str | None = None

    def feed(self, text: str) -> str:
        self._buf += text
        out: list[str] = []
        while self._buf:
            if self._close is not None:
                end = self._buf.find(self._close)
                if end < 0:
                    # Keep a tail that may be the start of the closing tag.
                    self._buf = self._buf[-(len(self._close) - 1) :]
                    break
                self._buf = self._buf[end + len(self._close) :]
                self._close = None
                continue
            lt = self._buf.find("<")
            if lt < 0:
                out.append(self._buf)
                self._buf = ""
                break
            out.append(self._buf[:lt])
            rest = self._buf[lt:]
            opened = next((t for t in _BLOCKS if rest.startswith(t)), None)
            if opened is not None:
                self._close = _BLOCKS[opened]
                self._buf = rest[len(opened) :]
                continue
            if any(t.startswith(rest) for t in _BLOCKS):
                self._buf = rest  # maybe a tag split across chunks: wait for more
                break
            out.append("<")
            self._buf = rest[1:]
        return _MARKS.sub("", "".join(out))

    def flush(self) -> str:
        """End of the reply: an unclosed think block is dropped, other text is kept."""
        rest = "" if self._close is not None else self._buf
        self._buf, self._close = "", None
        return _MARKS.sub("", rest)


_THINK_BLOCK = re.compile(r"<(think|reasoning)>.*?(</\1>|$)", re.DOTALL)
_FENCE = re.compile(r"^```[a-zA-Z]*\s*\n?(.*?)\n?```$", re.DOTALL)


def clean_reply(text: str | None) -> str | None:
    """A whole (non streamed) reply: think blocks and a surrounding code fence removed."""
    if text is None:
        return None
    text = _THINK_BLOCK.sub("", text).strip()
    m = _FENCE.match(text)
    return m.group(1).strip() if m else text


def schema_instruction(schema: dict[str, Any]) -> str:
    keys = list((schema.get("properties") or {}).keys())
    named = f" Its top level keys are exactly: {', '.join(keys)}." if keys else ""
    return (
        "\n\nReply with one JSON object only, no other text and no code fence."
        f"{named} It is an instance of this JSON schema (data, not the schema itself):\n"
        + json.dumps(schema)
    )


class DeepgramLLMService(OpenAILLMService):
    """Pipecat's OpenAI service on Deepgram's /v1/think (see module doc)."""

    # The client maps developer to system itself; Pipecat would map it to user.
    supports_developer_role = True
    supports_response_schema = False
    _requires_api_key = True

    def __init__(self, *, api_key: str, http_client: Any = None, **kwargs):
        self._think_http_client = http_client
        super().__init__(api_key=api_key, base_url=BASE_URL, **kwargs)
        self._spoken: SpokenTextFilter | None = None

    def create_client(self, api_key=None, base_url=None, default_headers=None, **kwargs):
        return ThinkClient(
            api_key=api_key,
            base_url=base_url or BASE_URL,
            http_client=self._think_http_client or DefaultAsyncHttpxClient(),
            default_headers={**(default_headers or {}), "Authorization": f"Token {api_key}"},
            max_retries=0,
        )

    async def _process_context(self, context: LLMContext):
        self._spoken = SpokenTextFilter()
        try:
            await super()._process_context(context)
        finally:
            spoken, self._spoken = self._spoken, None
            if rest := spoken.flush():
                await super()._push_llm_text(rest)

    async def _push_llm_text(self, text: str):
        if self._spoken is not None:
            text = self._spoken.feed(text)
        if text:
            await super()._push_llm_text(text)

    async def run_inference(
        self,
        context: LLMContext,
        max_tokens: int | None = None,
        system_instruction: str | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> str | None:
        if response_schema is not None:
            base = system_instruction or self._settings.system_instruction or ""
            system_instruction = base + schema_instruction(response_schema)
        reply = await BaseOpenAILLMService.run_inference(
            self, context, max_tokens=max_tokens, system_instruction=system_instruction
        )
        return clean_reply(reply)
