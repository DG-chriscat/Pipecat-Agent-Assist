"""Connector session unit tests with a fake reader: cache, timeout, logging, args, schemas.

The DB-backed per connector tests live in test_connectors_db.py.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from agent_assist_bot.connectors import CONNECTORS, ConnectorSession
from loguru import logger

MARIA = "418302"


class FakeReader:
    def __init__(self, rows=None, delay=0.0, error=None):
        self.rows = rows if rows is not None else [{"claim_id": "CLM-1", "status": "denied"}]
        self.delay = delay
        self.error = error
        self.queries: list[tuple[str, dict]] = []

    async def fetch(self, query, params):
        self.queries.append((query, dict(params)))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return [dict(r) for r in self.rows]

    async def close(self):
        pass


@pytest.fixture
def logs():
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(m.record["message"]), level="DEBUG")
    yield lines
    logger.remove(sink)


def make(reader=None, member_id=MARIA, **kw):
    return ConnectorSession(reader or FakeReader(), call_id="call-1", member_id=member_id, **kw)


def connector_lines(logs):
    return [line for line in logs if line.startswith("[connector]")]


async def test_same_tool_and_args_hit_the_cache_for_the_rest_of_the_call():
    reader = FakeReader()
    s = make(reader)
    first = await s.call("claims_search", {"status": "denied", "limit": 5})
    again = await s.call("claims_search", {"limit": 5, "status": "denied"})
    assert again == first
    assert len(reader.queries) == 1
    await s.call("claims_search", {"status": "paid"})
    assert len(reader.queries) == 2


async def test_cache_is_per_call():
    reader = FakeReader()
    await make(reader).call("claims_search", {})
    await make(reader).call("claims_search", {})
    assert len(reader.queries) == 2


async def test_timeout_returns_an_error_result_after_2_seconds_and_is_not_cached():
    s = make(FakeReader(delay=0.5), timeout_secs=0.05)
    out = await s.call("claims_search", {})
    assert out["error"] == "timeout"
    assert out["tool"] == "claims_search"
    assert out["source"] == "Claims system"
    s.reader = FakeReader()
    ok = await s.call("claims_search", {})
    assert ok["count"] == 1


def test_default_timeout_is_2_seconds():
    assert make().timeout_secs == 2.0


async def test_db_error_is_reported_without_details(logs):
    secret = "postgresql://user:hunter2@db.example/postgres"
    out = await make(FakeReader(error=RuntimeError(secret))).call("claims_search", {})
    assert out["error"] == "unavailable"
    assert "hunter2" not in json.dumps(out)
    assert all("hunter2" not in line for line in logs)


async def test_every_call_is_logged_with_call_id_tool_args_size_and_ms(logs):
    s = make()
    out = await s.call("claims_search", {"status": "denied"})
    await s.call("claims_search", {"status": "denied"})
    lines = connector_lines(logs)
    assert len(lines) == 2
    size = len(json.dumps(out, separators=(",", ":")))
    for line, cache in zip(lines, ("miss", "hit"), strict=True):
        assert "call_id=call-1" in line
        assert "tool=claims_search" in line
        assert "args=status=denied" in line
        assert "rows=1" in line
        assert f"bytes={size}" in line
        assert " ms=" in line
        assert f"cache={cache}" in line


async def test_log_never_dumps_free_text_or_result_rows(logs):
    reader = FakeReader(rows=[{"article_id": "KB-1", "title": "Secret Title Text"}])
    s = make(reader, member_id=None)
    await s.call("kb_articles_search", {"query": "my mri for my left knee"})
    await make().call("claims_search", {"provider": "Riverside Imaging"})
    text = "\n".join(connector_lines(logs))
    assert "mri" not in text.lower()
    assert "Riverside" not in text
    assert "Secret Title" not in text
    assert "query=<23 chars>" in text
    # Member id is not an argument and is never logged.
    assert MARIA not in text


@pytest.mark.parametrize(
    "tool, args, code",
    [
        ("claims_search", {"status": "lost"}, "bad_args"),
        ("claims_search", {"service_from": "last week"}, "bad_args"),
        ("claims_search", {"limit": 0}, "bad_args"),
        ("claims_search", {"limit": 500}, "bad_args"),
        ("claims_search", {"limit": "5"}, "bad_args"),
        ("claims_search", {"sql": "DROP TABLE claims"}, "bad_args"),
        ("claims_get", {}, "bad_args"),
        ("claims_get", {"claim_id": "   "}, "bad_args"),
        ("claims_delete", {}, "unknown_tool"),
    ],
)
async def test_bad_calls_return_an_error_without_querying(tool, args, code, logs):
    reader = FakeReader()
    out = await make(reader).call(tool, args)
    assert out["error"] == code
    assert out["message"]
    assert reader.queries == []
    assert len(connector_lines(logs)) == 1


async def test_member_is_always_bound_server_side():
    reader = FakeReader()
    await make(reader).call("payments_search", {"kind": "premium"})
    query, params = reader.queries[0]
    assert "member_id = %(_member_id)s" in query
    assert params["_member_id"] == MARIA
    assert params["kind"] == "premium"


async def test_values_are_bound_never_interpolated():
    reader = FakeReader()
    evil = "x'; DROP TABLE claims; --"
    await make(reader).call("claims_search", {"provider": evil})
    query, params = reader.queries[0]
    assert "DROP" not in query
    assert params["provider"] == "%x'; DROP TABLE claims; --%"


async def test_results_are_compact_json():
    import datetime as dt
    from decimal import Decimal

    reader = FakeReader(
        rows=[
            {
                "payment_id": "PAY-1",
                "paid_at": dt.datetime(2026, 11, 1, 6, 2, tzinfo=dt.UTC),
                "amount": Decimal("610.00"),
            }
        ]
    )
    out = await make(reader).call("payments_search", {})
    assert out == {
        "tool": "payments_search",
        "source": "Billing system",
        "count": 1,
        "items": [{"payment_id": "PAY-1", "paid_at": "2026-11-01T06:02:00Z", "amount": 610.0}],
    }
    json.dumps(out)


EXPECTED_TOOLS = [
    f"{c}_{k}"
    for c in (
        "members",
        "plans",
        "claims",
        "payments",
        "referrals",
        "prior_authorizations",
        "interactions",
        "kb_articles",
    )
    for k in ("search", "get")
]


def test_every_connector_has_search_and_get():
    assert make().tool_names == EXPECTED_TOOLS
    assert len(CONNECTORS) == 8


def test_tool_schemas_are_llm_function_schemas():
    s = make()
    schemas = s.tool_schemas()
    assert [t["name"] for t in schemas] == EXPECTED_TOOLS
    for t in schemas:
        assert set(t) == {"name", "description", "parameters"}
        assert t["description"]
        params = t["parameters"]
        assert params["type"] == "object"
        assert "member_id" not in params["properties"] or t["name"] == "members_get"
        assert set(params["required"]) <= set(params["properties"])
        for p in params["properties"].values():
            assert p["type"] in ("string", "integer")
            assert p["description"]
    get = next(t for t in schemas if t["name"] == "claims_get")
    assert get["parameters"]["required"] == ["claim_id"]
    search = next(t for t in schemas if t["name"] == "claims_search")
    assert search["parameters"]["required"] == []
    assert search["parameters"]["properties"]["status"]["enum"][0] == "received"
    assert "current caller" in search["description"]


def test_tool_schemas_can_be_limited_to_a_packs_connectors():
    names = [t["name"] for t in make().tool_schemas(["claims", "kb_articles"])]
    assert names == ["claims_search", "claims_get", "kb_articles_search", "kb_articles_get"]
    with pytest.raises(KeyError):
        make().tool_schemas(["claims", "nope"])


def test_docs_list_every_tool_param_and_field():
    from pathlib import Path

    doc = (Path(__file__).resolve().parents[2] / "docs" / "connectors.md").read_text()
    s = make()
    for name in s.tool_names:
        tool = s.tool(name)
        assert f"`{name}`" in doc, name
        for p in tool.params:
            assert f"`{p}`" in doc, (name, p)
        fields = tool.connector.get_fields if tool.kind == "get" else tool.connector.search_fields
        for f in fields:
            assert f"`{f}`" in doc, (name, f)
        assert tool.connector.source in doc


async def test_pipecat_function_schemas_carry_handlers_that_return_results():
    from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
    from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema

    reader = FakeReader()
    s = make(reader)
    fns = s.function_schemas(["claims"])
    assert all(isinstance(f, FunctionSchema) for f in fns)
    assert [f.name for f in fns] == ["claims_search", "claims_get"]
    assert fns[0].to_default_dict() == s.tool_schemas(["claims"])[0]
    ToolsSchema(standard_tools=fns)  # registers with Pipecat's LLM function calling

    got = []

    async def result_callback(result, **_):
        got.append(result)

    params = SimpleNamespace(
        function_name="claims_search",
        arguments={"status": "denied"},
        result_callback=result_callback,
    )
    await fns[0].handler(params)
    assert got == [await s.call("claims_search", {"status": "denied"})]
    assert len(reader.queries) == 1  # second call was a cache hit
