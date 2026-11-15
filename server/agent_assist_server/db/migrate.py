"""Apply db/migrations/*.sql in name order, once each, tracked in schema_migrations."""

from __future__ import annotations

from pathlib import Path

import psycopg

# server/agent_assist_server/db/migrate.py -> repo root
MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "db" / "migrations"


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    return sorted(directory.glob("*.sql"))


async def apply_migrations(
    conn: psycopg.AsyncConnection, directory: Path = MIGRATIONS_DIR
) -> list[str]:
    """Apply pending migrations in the connection's search_path. Returns versions applied."""
    files = migration_files(directory)
    if not files:
        raise FileNotFoundError(f"no migrations in {directory}")
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version text PRIMARY KEY,"
        " applied_at timestamptz NOT NULL DEFAULT now())"
    )
    cur = await conn.execute("SELECT version FROM schema_migrations")
    done = {row[0] for row in await cur.fetchall()}
    applied: list[str] = []
    for path in files:
        version = path.stem
        if version in done:
            continue
        async with conn.transaction():
            await conn.execute(path.read_text())
            await conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
        applied.append(version)
    return applied
