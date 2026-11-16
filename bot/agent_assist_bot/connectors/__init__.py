"""Read only data connectors the assist exposes as LLM tools (P4-1, PRD DC-1 to DC-5, DC-8).

    session = ConnectorSession(PostgresReader.from_env(), call_id=..., member_id=...)
    result = await session.call("claims_search", {"status": "denied"})

See docs/connectors.md for every tool and its fields.
"""

from agent_assist_bot.connectors.base import (
    TIMEOUT_SECS,
    Connector,
    ConnectorSession,
    Reader,
    Tool,
    ToolError,
)
from agent_assist_bot.connectors.catalog import CONNECTORS
from agent_assist_bot.connectors.postgres import PostgresReader

__all__ = [
    "CONNECTORS",
    "TIMEOUT_SECS",
    "Connector",
    "ConnectorSession",
    "PostgresReader",
    "Reader",
    "Tool",
    "ToolError",
]
