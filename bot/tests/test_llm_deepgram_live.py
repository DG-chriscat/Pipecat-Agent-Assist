"""Live: the Deepgram LLM provider against the real /v1/think (P5-9, DC-11).

    uv run pytest -m live bot/tests/test_llm_deepgram_live.py -s -v

Needs DEEPGRAM_API_KEY (a project enrolled in the Deepgram LLM Early Access).
For each model: a streamed reply through the Pipecat service (the request as the
pipeline sends it, developer message and tool_choice included), a tool call, and a
run_inference with a response schema (JSON only, as assist and wrap up ask).
"""

from __future__ import annotations

import json
import os
import time

import pytest
from agent_assist_bot.llm import create_llm
from agent_assist_bot.llm_deepgram import PHONELLM, THINK_FAST
from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import LLMTextFrame
from pipecat.processors.aggregators.llm_context import LLMContext

pytestmark = pytest.mark.live

SAVE_IDENTITY = FunctionSchema(
    name="save_identity",
    description="Save the caller's full name, date of birth, and member ID.",
    properties={
        "full_name": {"type": "string"},
        "dob": {"type": "string", "description": "YYYY-MM-DD"},
        "member_id": {"type": "string"},
    },
    required=["full_name", "dob", "member_id"],
)


@pytest.fixture(autouse=True)
def key():
    if not os.getenv("DEEPGRAM_API_KEY"):
        pytest.skip("missing env: DEEPGRAM_API_KEY")


async def _run(llm, context, monkeypatch):
    texts, calls = [], []

    async def push(frame, direction=None):
        if isinstance(frame, LLMTextFrame):
            texts.append(frame.text)

    async def run_calls(fcs):
        calls.extend(fcs)

    monkeypatch.setattr(llm, "push_frame", push)
    monkeypatch.setattr(llm, "run_function_calls", run_calls)
    t = time.monotonic()
    await llm._process_context(context)
    return "".join(texts), calls, time.monotonic() - t


@pytest.mark.parametrize("model", [PHONELLM, THINK_FAST])
async def test_streamed_reply(model, monkeypatch):
    llm = create_llm(model=model, system_instruction="You are a phone assistant. Be brief.")
    ctx = LLMContext(
        messages=[
            {"role": "developer", "content": "Answer in one short sentence."},
            {"role": "user", "content": "Hi, can you hear me?"},
        ],
        tool_choice="auto",
    )
    text, calls, secs = await _run(llm, ctx, monkeypatch)
    print(f"\n{model}: {secs:.2f} s: {text!r}")
    assert text.strip()
    assert "<think" not in text


async def test_phonellm_calls_save_identity(monkeypatch):
    llm = create_llm(
        model=PHONELLM,
        system_instruction=(
            "You are a health plan intake assistant. When the caller gives their name, "
            "date of birth, and member ID, call save_identity."
        ),
    )
    ctx = LLMContext(
        messages=[
            {
                "role": "user",
                "content": ("My name is Maria Lopez, born March 12 1984, member ID E V M 100231."),
            }
        ],
        tools=ToolsSchema(standard_tools=[SAVE_IDENTITY]),
        tool_choice="required",
    )
    text, calls, secs = await _run(llm, ctx, monkeypatch)
    print(
        f"\nphonellm tool call in {secs:.2f} s: {[(c.function_name, c.arguments) for c in calls]}"
    )
    assert [c.function_name for c in calls] == ["save_identity"]
    args = calls[0].arguments
    assert args["dob"] == "1984-03-12"
    assert args["member_id"].replace(" ", "").upper() == "418302"


@pytest.mark.parametrize("model", [PHONELLM, THINK_FAST])
async def test_run_inference_json(model):
    llm = create_llm(model=model, system_instruction="You classify calls.")
    schema = {
        "type": "object",
        "properties": {"topic": {"type": "string", "enum": ["claims", "payments", "other"]}},
        "required": ["topic"],
        "additionalProperties": False,
    }
    t = time.monotonic()
    reply = await llm.run_inference(
        LLMContext(messages=[{"role": "user", "content": "Why was my MRI claim denied?"}]),
        max_tokens=100,
        response_schema=schema,
    )
    print(f"\n{model} run_inference {time.monotonic() - t:.2f} s: {reply!r}")
    assert json.loads(reply) == {"topic": "claims"}
