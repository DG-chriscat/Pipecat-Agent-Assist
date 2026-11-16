"""Read only Postgres access for connectors (DC-2).

Same conventions as the rest of the bot: async psycopg, DATABASE_URL, optional
DATABASE_SCHEMA (search_path, set with SET so it works through the Supabase pooler).
The session is put in read only mode (`default_transaction_read_only`), and a server
side `statement_timeout` backs up the 2 s client timeout. The URL is a secret and is
never logged.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Any

from agent_assist_bot.calls import DATABASE_SCHEMA_ENV, DATABASE_URL_ENV

STATEMENT_TIMEOUT_MS = 2000


class PostgresReader:
    """One lazily opened connection per call, reopened if dropped or cancelled."""

    def __init__(self, url: str, *, schema: str | None = None):
        self._url = url
        self._schema = schema
        self._conn = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> PostgresReader | None:
        env = dict(os.environ) if env is None else env
        url = env.get(DATABASE_URL_ENV, "").strip()
        schema = env.get(DATABASE_SCHEMA_ENV, "").strip() or None
        return cls(url, schema=schema) if url else None

    async def _connection(self):
        import psycopg
        from psycopg import sql

        if self._conn is None or self._conn.closed:
            conn = await psycopg.AsyncConnection.connect(self._url, autocommit=True)
            await conn.execute("SET default_transaction_read_only = on")
            await conn.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
            if self._schema:
                await conn.execute(
                    sql.SQL("SET search_path TO {}").format(sql.Identifier(self._schema))
                )
            self._conn = conn
        return self._conn

    async def fetch(self, query: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        import psycopg
        from psycopg.rows import dict_row

        async with self._lock:
            for attempt in (1, 2):
                conn = await self._connection()
                try:
                    async with conn.cursor(row_factory=dict_row) as cur:
                        await cur.execute(query, dict(params))
                        return await cur.fetchall()
                except asyncio.CancelledError:
                    # Timed out client side: the connection may be mid query. Drop it.
                    await self._drop()
                    raise
                except psycopg.OperationalError:
                    # Pooler dropped an idle connection: reconnect once.
                    await self._drop()
                    if attempt == 2:
                        raise
        raise AssertionError("unreachable")

    async def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                await conn.close()
            except Exception:  # noqa: BLE001
                pass

    async def close(self) -> None:
        await self._drop()
