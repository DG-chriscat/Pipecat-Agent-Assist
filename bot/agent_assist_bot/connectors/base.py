"""Connector shape (DC-1), per call session with timeout, cache, and logging (DC-4, DC-5).

A connector is a declarative `Connector` spec over one table of the mock DB. Every
connector has the same two tools, `<name>_search` (filters) and `<name>_get` (id).
`ConnectorSession` is created once per call; it runs tools through a `Reader`
(read only SQL, see sql.py), times each call out after 2 s, caches results for the
rest of the call keyed by tool and arguments, and logs every call.

Member scoping: scoped connectors never take a member id from the LLM. The session's
`member_id` (the call's verified member) is bound into every query server side, so
another member's rows cannot be returned whatever the arguments say.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from loguru import logger

from agent_assist_bot.calls import normalize_member_id

TIMEOUT_SECS = 2.0  # DC-4
DEFAULT_LIMIT = 10
MAX_LIMIT = 25


class Reader(Protocol):
    """Runs one read only query and returns rows as dicts."""

    async def fetch(self, query: str, params: Mapping[str, Any]) -> list[dict[str, Any]]: ...

    async def close(self) -> None: ...


@dataclass(frozen=True)
class Filter:
    """One search filter: a JSON schema param and the SQL condition it adds.

    `sql` uses a `%(name)s` placeholder for the value. `loggable` marks values safe to
    log verbatim (enums, ids, dates); free text is logged as its length only (DC-5).
    """

    name: str
    schema: dict[str, Any]
    sql: str
    loggable: bool = True
    transform: Callable[[Any], Any] | None = None


@dataclass(frozen=True)
class Connector:
    name: str
    source: str  # source label shown on cards
    description: str
    table: str  # FROM clause (may include a join)
    id_field: str  # output field used by get
    id_column: str  # SQL column for get
    id_pattern: str  # example id, for the tool description
    columns: Mapping[str, str]  # output field -> SQL expression
    order_by: str
    member_scoped: bool = True
    member_column: str = "member_id"
    filters: Sequence[Filter] = ()
    search_columns: Sequence[str] | None = None  # subset of columns for search rows
    keyword_search: Callable[[Connector, dict[str, Any]], tuple[str, str, dict]] | None = None

    @property
    def search_tool(self) -> str:
        return f"{self.name}_search"

    @property
    def get_tool(self) -> str:
        return f"{self.name}_get"

    @property
    def get_fields(self) -> list[str]:
        return list(self.columns)

    @property
    def search_fields(self) -> list[str]:
        return list(self.search_columns or self.columns)


class ToolError(Exception):
    """Bad arguments or an unusable call state; reported to the LLM, not raised."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, dt.datetime):
        return v.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, list | tuple):
        return [_jsonable(x) for x in v]
    return v


def _check_type(name: str, schema: dict[str, Any], value: Any) -> Any:
    kind = schema.get("type")
    if kind == "string":
        if not isinstance(value, str) or not value.strip():
            raise ToolError("bad_args", f"{name} must be a non empty string")
        value = value.strip()
        if "enum" in schema and value not in schema["enum"]:
            raise ToolError("bad_args", f"{name} must be one of {', '.join(schema['enum'])}")
        if schema.get("format") == "date":
            try:
                dt.date.fromisoformat(value)
            except ValueError:
                raise ToolError("bad_args", f"{name} must be a YYYY-MM-DD date") from None
        return value
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ToolError("bad_args", f"{name} must be an integer")
        lo, hi = schema.get("minimum"), schema.get("maximum")
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            raise ToolError("bad_args", f"{name} must be between {lo} and {hi}")
        return value
    raise AssertionError(f"unsupported schema type {kind}")


LIMIT_SCHEMA = {
    "type": "integer",
    "minimum": 1,
    "maximum": MAX_LIMIT,
    "description": f"Max rows to return (default {DEFAULT_LIMIT}).",
}


@dataclass(frozen=True)
class Tool:
    name: str
    connector: Connector
    kind: str  # "search" | "get"

    @property
    def params(self) -> dict[str, dict[str, Any]]:
        c = self.connector
        if self.kind == "get":
            return {
                c.id_field: {
                    "type": "string",
                    "description": f"The {c.id_field}, for example {c.id_pattern}.",
                }
            }
        props = {f.name: f.schema for f in c.filters}
        props["limit"] = LIMIT_SCHEMA
        return props

    @property
    def required(self) -> list[str]:
        return [self.connector.id_field] if self.kind == "get" else []

    @property
    def description(self) -> str:
        c = self.connector
        scope = " Only the current caller's records are returned." if c.member_scoped else ""
        if self.kind == "get":
            return f"Get one {c.description} by {c.id_field}.{scope} Source: {c.source}."
        return f"Search {c.description}s.{scope} Source: {c.source}."

    def schema(self) -> dict[str, Any]:
        """LLM tool schema: name, description, JSON schema parameters."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": self.params,
                "required": self.required,
            },
        }

    def validate(self, args: Mapping[str, Any] | None) -> dict[str, Any]:
        args = dict(args or {})
        params = self.params
        unknown = sorted(set(args) - set(params))
        if unknown:
            raise ToolError("bad_args", f"unknown argument(s): {', '.join(unknown)}")
        clean: dict[str, Any] = {}
        for name, value in args.items():
            if value is None:
                continue
            clean[name] = _check_type(name, params[name], value)
        for name in self.required:
            if name not in clean:
                raise ToolError("bad_args", f"{name} is required")
        return clean


def build_query(tool: Tool, args: dict[str, Any], member_id: str | None) -> tuple[str, dict]:
    c = tool.connector
    fields = c.get_fields if tool.kind == "get" else c.search_fields
    select = ", ".join(f"{c.columns[f]} AS {f}" for f in fields)
    where: list[str] = []
    params: dict[str, Any] = {}
    if c.member_scoped:
        where.append(f"{c.member_column} = %(_member_id)s")
        params["_member_id"] = member_id
    order = c.order_by
    if tool.kind == "get":
        where.append(f"{c.id_column} = %(_id)s")
        params["_id"] = args[c.id_field]
        limit = 1
    else:
        for f in c.filters:
            if f.name in args and f.sql:
                where.append(f.sql)
                v = args[f.name]
                params[f.name] = f.transform(v) if f.transform else v
        if c.keyword_search is not None:
            cond, order_expr, extra = c.keyword_search(c, args)
            if cond:
                where.append(cond)
                params.update(extra)
                order = f"{order_expr}, {order}"
        limit = args.get("limit", DEFAULT_LIMIT)
    params["_limit"] = limit
    sql = f"SELECT {select} FROM {c.table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order} LIMIT %(_limit)s"
    return sql, params


def summarize_args(tool: Tool, args: Mapping[str, Any]) -> str:
    """Args for the log line: enums, ids, dates verbatim; free text as its length only."""
    loggable = {f.name: f.loggable for f in tool.connector.filters}
    parts = []
    for k in sorted(args):
        v = args[k]
        if loggable.get(k, True):
            parts.append(f"{k}={v}")
        else:
            parts.append(f"{k}=<{len(str(v))} chars>")
    return ",".join(parts) or "-"


@dataclass
class ConnectorSession:
    """All connector tools for one call: member scoping, 2 s timeout, cache, logs."""

    reader: Reader
    call_id: str
    member_id: str | None
    connectors: Sequence[Connector] | None = None
    timeout_secs: float = TIMEOUT_SECS
    _cache: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict, repr=False)
    _tools: dict[str, Tool] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        from agent_assist_bot.connectors.catalog import CONNECTORS

        for c in self.connectors if self.connectors is not None else CONNECTORS:
            self._tools[c.search_tool] = Tool(c.search_tool, c, "search")
            self._tools[c.get_tool] = Tool(c.get_tool, c, "get")

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools)

    def _selected(self, connectors: Sequence[str] | None) -> list[Tool]:
        if connectors is None:
            return list(self._tools.values())
        known = {t.connector.name for t in self._tools.values()}
        missing = [c for c in connectors if c not in known]
        if missing:
            raise KeyError(f"unknown connector(s): {', '.join(missing)}")
        return [t for t in self._tools.values() if t.connector.name in connectors]

    def tool_schemas(self, connectors: Sequence[str] | None = None) -> list[dict[str, Any]]:
        """LLM tool schemas (name, description, JSON schema params), optionally per pack."""
        return [t.schema() for t in self._selected(connectors)]

    def function_schemas(self, connectors: Sequence[str] | None = None) -> list[Any]:
        """Pipecat FunctionSchemas with handlers bound to this session.

        Put them in `ToolsSchema(standard_tools=...)` on the LLMContext; Pipecat registers
        the handlers automatically.
        """
        from pipecat.adapters.llm.schemas.function_schema import FunctionSchema

        out = []
        for t in self._selected(connectors):
            s = t.schema()
            out.append(
                FunctionSchema(
                    name=s["name"],
                    description=s["description"],
                    properties=s["parameters"]["properties"],
                    required=s["parameters"]["required"],
                    handler=self._handler(t.name),
                )
            )
        return out

    def _handler(self, name: str):
        async def handler(params) -> None:
            await params.result_callback(await self.call(name, params.arguments))

        handler.__name__ = name
        return handler

    def tool(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolError("unknown_tool", f"unknown tool {name}") from None

    async def call(self, name: str, args: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Run one tool. Never raises for tool level problems: returns an error result."""
        start = time.perf_counter()
        tool = self._tools.get(name)
        source = tool.connector.source if tool else None
        try:
            if tool is None:
                raise ToolError("unknown_tool", f"unknown tool {name}")
            clean = tool.validate(self._check_member_arg(tool, args))
            if tool.connector.member_scoped and not self.member_id:
                raise ToolError("no_member", "no verified member on this call")
        except ToolError as e:
            return self._done(name, args or {}, tool, start, "-", _error(name, source, e))

        key = (name, json.dumps(clean, sort_keys=True))
        cached = self._cache.get(key)
        if cached is not None:
            return self._done(name, clean, tool, start, "hit", cached)
        sql, params = build_query(tool, clean, self.member_id)
        try:
            rows = await asyncio.wait_for(self.reader.fetch(sql, params), self.timeout_secs)
        except TimeoutError:
            err = ToolError("timeout", f"{name} timed out after {self.timeout_secs:g} s")
            return self._done(name, clean, tool, start, "miss", _error(name, source, err))
        except Exception as e:  # noqa: BLE001
            # Never echo the exception: it can carry the database URL.
            logger.error(f"[connector] call_id={self.call_id} tool={name} {type(e).__name__}")
            err = ToolError("unavailable", f"{tool.connector.source} is unavailable")
            return self._done(name, clean, tool, start, "miss", _error(name, source, err))
        result = _result(tool, rows)
        self._cache[key] = result
        return self._done(name, clean, tool, start, "miss", result)

    def _check_member_arg(self, tool: Tool, args: Mapping[str, Any] | None) -> dict[str, Any]:
        """Scoped tools take no member id; an LLM that sends one anyway must send this call's.

        The id is dropped either way: the query always binds the session's member.
        """
        args = dict(args or {})
        if not tool.connector.member_scoped or "member_id" in tool.params:
            return args
        if "member_id" in args:
            given = normalize_member_id(str(args.pop("member_id") or ""))
            if given and given != normalize_member_id(self.member_id or ""):
                raise ToolError("member_scope", "only the current caller's records are available")
        return args

    def _done(self, name, args, tool, start, cache, result) -> dict[str, Any]:
        ms = (time.perf_counter() - start) * 1000
        size = len(json.dumps(result, separators=(",", ":")))
        count = result.get("count", 1 if result.get("item") else 0)
        status = result.get("error", "ok")
        summary = summarize_args(tool, args) if tool else "-"
        logger.info(
            f"[connector] call_id={self.call_id} tool={name} args={summary} status={status} "
            f"rows={count} bytes={size} ms={ms:.1f} cache={cache}"
        )
        return result


def _error(name: str, source: str | None, e: ToolError) -> dict[str, Any]:
    return {"tool": name, "source": source, "error": e.code, "message": e.message}


def _result(tool: Tool, rows: list[dict[str, Any]]) -> dict[str, Any]:
    items = [{k: _jsonable(v) for k, v in r.items()} for r in rows]
    base = {"tool": tool.name, "source": tool.connector.source}
    if tool.kind == "get":
        return {**base, "found": bool(items), "item": items[0] if items else None}
    return {**base, "count": len(items), "items": items}
