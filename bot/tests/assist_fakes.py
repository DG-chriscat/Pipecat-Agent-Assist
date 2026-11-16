"""Fakes for the assist engine tests: connectors over in-memory rows, a scripted LLM."""

from __future__ import annotations

from typing import Any

from agent_assist_bot.connectors.catalog import CONNECTORS

ID_FIELD = {c.name: c.id_field for c in CONNECTORS}

MARIA_CLAIMS = [
    {
        "claim_id": "CLM-900001",
        "service_date": "2026-10-13",
        "provider": "Riverside Imaging Center",
        "service": "MRI, knee, left",
        "billed_amount": 1850.0,
        "status": "denied",
        "denial_code": "CO-197",
        "denial_reason": "Precertification or authorization absent",
        "received_on": "2026-10-17",
        "processed_on": "2026-10-31",
    },
    {
        "claim_id": "CLM-900002",
        "service_date": "2026-09-01",
        "provider": "Lakeside Family Medicine",
        "service": "Office visit",
        "billed_amount": 180.0,
        "status": "paid",
        "denial_code": None,
        "denial_reason": None,
        "received_on": "2026-09-03",
        "processed_on": "2026-09-10",
    },
]

KB = [
    {
        "article_id": "KB-CL-002",
        "title": "CO-197: no prior authorization",
        "category": "claims",
        "tags": ["CO-197"],
        "summary": "When a claim is denied CO-197 ...",
    },
    {
        "article_id": "KB-CL-010",
        "title": "Reading an Explanation of Benefits",
        "category": "claims",
        "tags": ["EOB"],
        "summary": "An EOB is not a bill ...",
    },
]

PAYMENTS = [
    {
        "payment_id": "PAY-000064",
        "paid_at": "2026-11-01T14:00:00Z",
        "amount": 610.0,
        "kind": "premium",
        "method": "autopay",
        "status": "posted",
        "reference": "AUTO-2",
    },
    {
        "payment_id": "PAY-000063",
        "paid_at": "2026-11-01T09:00:00Z",
        "amount": 610.0,
        "kind": "premium",
        "method": "autopay",
        "status": "posted",
        "reference": "AUTO-1",
    },
]


def default_rows() -> dict[str, list[dict[str, Any]]]:
    return {
        "claims": [dict(r) for r in MARIA_CLAIMS],
        "kb_articles": [dict(r) for r in KB],
        "payments": [dict(r) for r in PAYMENTS],
        "members": [{"member_id": "EVM100231", "name": "Maria Alvarez"}],
    }


class FakeConnectors:
    """ConnectorSession stand in: `<connector>_search` filters rows by equal fields."""

    def __init__(self, rows: dict[str, list[dict[str, Any]]] | None = None):
        self.rows = default_rows() if rows is None else rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def tool_schemas(self, connectors=None) -> list[dict[str, Any]]:
        names = connectors if connectors is not None else list(ID_FIELD)
        out = []
        for c in names:
            for kind in ("search", "get"):
                out.append(
                    {
                        "name": f"{c}_{kind}",
                        "description": f"{kind} {c}",
                        "parameters": {"type": "object", "properties": {}, "required": []},
                    }
                )
        return out

    async def call(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        args = dict(args or {})
        self.calls.append((name, args))
        connector, _, kind = name.rpartition("_")
        rows = self.rows.get(connector, [])
        if kind == "get":
            want = args.get(ID_FIELD[connector])
            item = next((r for r in rows if r[ID_FIELD[connector]] == want), None)
            return {"tool": name, "source": "fake", "found": item is not None, "item": item}
        args.pop("limit", None)
        items = [r for r in rows if all(str(r.get(k)) == str(v) for k, v in args.items())]
        return {"tool": name, "source": "fake", "count": len(items), "items": items}


class FakeLLM:
    """Returns scripted proposals in order; records every request."""

    def __init__(self, *proposals: dict[str, Any], tool_calls=()):
        self.proposals = list(proposals)
        self.tool_calls = list(tool_calls)
        self.requests: list[Any] = []

    async def propose(self, request, run_tool) -> dict[str, Any]:
        self.requests.append(request)
        for name, args in self.tool_calls:
            await run_tool(name, args)
        if self.proposals:
            return self.proposals.pop(0)
        return {"next_steps": [], "cards": []}


class FailingLLM:
    def __init__(self):
        self.calls = 0

    async def propose(self, request, run_tool):
        self.calls += 1
        raise RuntimeError("model unavailable")


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, secs: float) -> None:
        self.t += secs
