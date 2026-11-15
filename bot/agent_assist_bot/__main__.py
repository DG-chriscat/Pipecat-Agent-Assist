"""Dial-in entry point used by the server.

`python -m agent_assist_bot --room-url ... --call-id ... --call-domain ...`

The meeting token comes from the DAILY_ROOM_TOKEN env var so it stays off argv.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass

from agent_assist_bot.bot import (
    DEFAULT_DAILY_API_URL,
    DialinConfig,
    MissingKeyError,
    require_keys,
    run_bot,
)

TOKEN_ENV = "DAILY_ROOM_TOKEN"


@dataclass(frozen=True)
class DialinArgs:
    room_url: str
    token: str
    call_id: str
    call_domain: str
    session_id: str = ""

    def dialin_config(self, daily_api_key: str, api_url: str) -> DialinConfig:
        return DialinConfig(
            call_id=self.call_id,
            call_domain=self.call_domain,
            api_key=daily_api_key,
            api_url=api_url,
        )


def parse_args(argv: list[str] | None = None, env: dict[str, str] | None = None) -> DialinArgs:
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
    return DialinArgs(
        room_url=a.room_url,
        token=token,
        call_id=a.call_id,
        call_domain=a.call_domain,
        session_id=a.session_id,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        keys = require_keys()
    except MissingKeyError as e:
        print(f"agent_assist_bot: {e}", file=sys.stderr)
        return 2
    api_url = os.environ.get("DAILY_API_URL", DEFAULT_DAILY_API_URL)
    dialin = args.dialin_config(keys["DAILY_API_KEY"], api_url)
    asyncio.run(run_bot(args.room_url, args.token, dialin))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
