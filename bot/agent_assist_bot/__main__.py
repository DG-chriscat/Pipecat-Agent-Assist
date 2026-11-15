"""Entry point used by the server: `python -m agent_assist_bot --room-url ... --call-id ...`.

The meeting token comes from the DAILY_ROOM_TOKEN env var so it stays off argv.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from agent_assist_bot.bot import BotConfig, MissingKeyError, require_keys, run_bot

TOKEN_ENV = "DAILY_ROOM_TOKEN"


def parse_args(argv: list[str] | None = None, env: dict[str, str] | None = None) -> BotConfig:
    env = dict(os.environ) if env is None else env
    p = argparse.ArgumentParser(prog="agent_assist_bot")
    p.add_argument("--room-url", required=True)
    p.add_argument("--call-id", required=True)
    p.add_argument("--call-domain", required=True)
    p.add_argument("--session-id", default="")
    a = p.parse_args(argv)
    token = env.get(TOKEN_ENV, "")
    if not token:
        p.error(f"{TOKEN_ENV} must be set")
    return BotConfig(
        room_url=a.room_url,
        token=token,
        call_id=a.call_id,
        call_domain=a.call_domain,
        session_id=a.session_id,
        daily_api_url=env.get("DAILY_API_URL", "https://api.daily.co/v1"),
    )


def main(argv: list[str] | None = None) -> int:
    cfg = parse_args(argv)
    try:
        keys = require_keys()
    except MissingKeyError as e:
        print(f"agent_assist_bot: {e}", file=sys.stderr)
        return 2
    asyncio.run(run_bot(cfg, keys))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
