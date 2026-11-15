"""Mock database access (ADR 0002): plain Postgres via DATABASE_URL, psycopg 3 async.

The URL is a secret. It is read from the environment and never logged or put
in exception messages.
"""

from __future__ import annotations

import os

import psycopg
from psycopg import sql

ENV_VAR = "DATABASE_URL"


class DatabaseConfigError(Exception):
    """DATABASE_URL is not configured."""


def database_url() -> str:
    url = os.environ.get(ENV_VAR, "").strip()
    if not url:
        raise DatabaseConfigError(f"{ENV_VAR} is not set")
    return url


async def connect(
    *, schema: str | None = None, url: str | None = None, autocommit: bool = True
) -> psycopg.AsyncConnection:
    """Open an async connection. With `schema`, search_path is that schema only.

    Setting search_path with SET (not a startup option) keeps this working
    through Supabase's Session pooler.
    """
    conn = await psycopg.AsyncConnection.connect(url or database_url(), autocommit=autocommit)
    if schema:
        await conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
    return conn
