"""The assist LLM proposer: create_llm + run_inference with a strict JSON schema.

Tools are proposed in the JSON (`tool_calls`) and run by the engine's `run_tool`
(which only allows the pack's connectors); results go back for one more round.
"""

from __future__ import annotations

import json

import pytest
from agent_assist_bot import assist_llm
from agent_assist_bot.assist import AssistRequest
from agent_assist_bot.packs import load_pack


class FakeService:
    def __init__(self, *replies: str | None):
        self.replies = list(replies)
        self.calls: list[dict] = []

    async def run_inference(
        self, context, max_tokens=None, system_instruction=None, response_schema=None
    ):
        self.calls.append(
            {
                "messages": [dict(m) for m in context.get_messages()],
                "system": system_instruction,
                "schema": response_schema,
                "max_tokens": max_tokens,
            }
        )
        return self.replies.pop(0)


def _request():
    return AssistRequest(
        pack=load_pack("claims_status"),
        intake={"full_name": "Maria Alvarez", "claim_number": "CLM-900001"},
        window=[{"role": "caller", "text": "It was denied.", "line_id": "caller-1", "ts": 1.0}],
        tools=[
            {
                "name": "claims_search",
                "description": "Search claims.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }
        ],
    )


def _reply(tool_calls=(), next_steps=(), cards=()):
    return json.dumps(
        {"tool_calls": list(tool_calls), "next_steps": list(next_steps), "cards": list(cards)}
    )


def test_schema_is_strict():
    s = assist_llm.RESPONSE_SCHEMA

    def walk(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert sorted(node["required"]) == sorted(node["properties"])
            for child in node["properties"].values():
                walk(child)
        if node.get("type") == "array":
            walk(node["items"])

    walk(s)


async def test_no_tools_needed_returns_the_proposal_in_one_round():
    nxt = [{"rule_id": "retro_auth_review", "records": [{"connector": "claims", "id": "C1"}]}]
    svc = FakeService(_reply(next_steps=nxt))
    out = await assist_llm.LLMProposer(svc).propose(_request(), _no_tools)
    assert out == {"next_steps": nxt, "cards": []}
    [call] = svc.calls
    assert call["schema"] == assist_llm.RESPONSE_SCHEMA
    assert "clinical" in call["system"].lower()
    user = call["messages"][0]["content"]
    assert "CALLER: It was denied." in user and "claims_search" in user


async def test_tool_calls_run_then_a_second_round_decides():
    ran = []

    async def run_tool(name, args):
        ran.append((name, args))
        return {"tool": name, "count": 1, "items": [{"claim_id": "CLM-900001"}]}

    svc = FakeService(
        _reply(tool_calls=[{"name": "claims_search", "args_json": '{"status": "denied"}'}]),
        _reply(cards=[{"template_id": "claims_table", "ids": ["CLM-900001"]}]),
    )
    out = await assist_llm.LLMProposer(svc).propose(_request(), run_tool)
    assert ran == [("claims_search", {"status": "denied"})]
    assert out["cards"] == [{"template_id": "claims_table", "ids": ["CLM-900001"]}]
    second = svc.calls[1]["messages"]
    assert second[1]["role"] == "assistant" and second[2]["role"] == "user"
    assert "CLM-900001" in second[2]["content"]


async def test_rounds_are_capped():
    loop = _reply(tool_calls=[{"name": "claims_search", "args_json": "{}"}])
    svc = FakeService(*([loop] * 10))
    n = []

    async def run_tool(name, args):
        n.append(name)
        return {"count": 0, "items": []}

    out = await assist_llm.LLMProposer(svc).propose(_request(), run_tool)
    assert len(svc.calls) == assist_llm.MAX_ROUNDS
    assert out == {"next_steps": [], "cards": []}


@pytest.mark.parametrize("reply", [None, "not json", "[]", '{"next_steps": "x"}'])
async def test_bad_replies_give_an_empty_proposal(reply):
    out = await assist_llm.LLMProposer(FakeService(reply)).propose(_request(), _no_tools)
    assert out == {"next_steps": [], "cards": []}


async def test_bad_tool_args_are_reported_back_not_raised():
    svc = FakeService(
        _reply(tool_calls=[{"name": "claims_search", "args_json": "{oops"}]),
        _reply(),
    )
    out = await assist_llm.LLMProposer(svc).propose(_request(), _no_tools)
    assert out == {"next_steps": [], "cards": []}
    assert "bad_args" in svc.calls[1]["messages"][2]["content"]


def test_from_env_uses_create_llm(monkeypatch):
    seen = {}

    def fake_create_llm(**kw):
        seen.update(kw)
        return FakeService()

    monkeypatch.setattr(assist_llm, "create_llm", fake_create_llm)
    monkeypatch.setenv("ASSIST_LLM_MODEL", "claude-test")
    p = assist_llm.LLMProposer.from_env()
    assert isinstance(p.service, FakeService)
    assert seen["model"] == "claude-test"


async def _no_tools(name, args):
    raise AssertionError("no tool expected")
