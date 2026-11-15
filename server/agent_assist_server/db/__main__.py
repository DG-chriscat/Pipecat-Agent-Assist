"""CLI: `uv run python -m agent_assist_server.db migrate|seed [--schema S]`.

Reads DATABASE_URL from the environment (e.g. `uv run --env-file .env ...`).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys

from agent_assist_server.db import DatabaseConfigError, connect, migrate, seed


async def _run(args: argparse.Namespace) -> int:
    async with await connect(schema=args.schema) as conn:
        if args.command == "migrate":
            applied = await migrate.apply_migrations(conn)
            print("applied: " + (", ".join(applied) if applied else "none (up to date)"))
        else:
            as_of = dt.date.fromisoformat(args.as_of) if args.as_of else None
            counts = await seed.load_seed(conn, as_of=as_of)
            for table, n in counts.items():
                print(f"{table:22} {n}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent_assist_server.db")
    parser.add_argument("command", choices=["migrate", "seed"])
    parser.add_argument("--schema", help="target schema (default: server search_path)")
    parser.add_argument("--as-of", help="seed reference date YYYY-MM-DD (default: today)")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except DatabaseConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
