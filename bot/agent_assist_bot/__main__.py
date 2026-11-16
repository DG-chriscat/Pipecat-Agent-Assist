"""Bot entry point used by the server's BotLauncher.

Dial-in call:    `python -m agent_assist_bot --room-url ... --call-id ... --call-domain ...`
Browser caller:  `python -m agent_assist_bot --room-url ...` (PRD IN-12, ADR 0002)
Warm (P5-2):     `python -m agent_assist_bot --warm` (preload, then the call arrives on
                 stdin; see agent_assist_bot.warm)

Both run the same build_worker and greeting; dial-in only adds the transport's
dialin_settings. The meeting token comes from the DAILY_ROOM_TOKEN env var so it
stays off argv. The call's bot settings (P5-5, agent_assist_bot.call_settings) come
with the warm assignment, or as JSON in AGENT_ASSIST_BOT_SETTINGS for a cold start.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any

from agent_assist_bot import warm
from agent_assist_bot.bot import (
    DEFAULT_DAILY_API_URL,
    DialinConfig,
    MissingKeyError,
    require_keys,
    run_bot,
)
from agent_assist_bot.call_settings import CallSettings
from agent_assist_bot.timing import log_timing

TOKEN_ENV = "DAILY_ROOM_TOKEN"
WARM_FLAG = "--warm"


@dataclass(frozen=True)
class DialinArgs:
    room_url: str
    token: str
    call_id: str | None = None
    call_domain: str | None = None
    session_id: str = ""
    # P5-5: from the warm assignment; None means read the env (cold) or defaults.
    settings: dict[str, Any] | None = field(default=None, compare=False)

    def dialin_config(self, daily_api_key: str, api_url: str) -> DialinConfig | None:
        """Dial-in settings, or None for a browser caller (no call_id/call_domain)."""
        if not self.call_id:
            return None
        return DialinConfig(
            call_id=self.call_id,
            call_domain=self.call_domain or "",
            api_key=daily_api_key,
            api_url=api_url,
        )

    def call_settings(self) -> CallSettings:
        if self.settings is not None:
            return CallSettings.from_mapping(self.settings)
        return CallSettings.from_env()


def parse_args(argv: list[str] | None = None, env: dict[str, str] | None = None) -> DialinArgs:
    env = dict(os.environ) if env is None else env
    p = argparse.ArgumentParser(prog="agent_assist_bot")
    p.add_argument("--room-url", required=True)
    p.add_argument("--call-id", default=None, help="dial-in only")
    p.add_argument("--call-domain", default=None, help="dial-in only")
    p.add_argument("--session-id", default="")
    a = p.parse_args(argv)
    if bool(a.call_id) != bool(a.call_domain):
        p.error("--call-id and --call-domain must be given together (dial-in) or not at all")
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
    argv = sys.argv[1:] if argv is None else argv
    if argv == [WARM_FLAG]:
        return warm_main()
    args = parse_args(argv)
    try:
        keys = require_keys()
    except MissingKeyError as e:
        print(f"agent_assist_bot: {e}", file=sys.stderr)
        return 2
    return run_call(args, keys)


def run_call(args: DialinArgs, keys: dict[str, str]) -> int:
    api_url = os.environ.get("DAILY_API_URL", DEFAULT_DAILY_API_URL)
    dialin = args.dialin_config(keys["DAILY_API_KEY"], api_url)
    session_id = args.session_id or str(uuid.uuid4())
    settings = args.call_settings()
    asyncio.run(
        run_bot(args.room_url, args.token, dialin, session_id=session_id, settings=settings)
    )
    return 0


def warm_main() -> int:
    """P5-2: load everything, say so on stdout, then wait for the server's call."""
    try:
        keys = require_keys()
    except MissingKeyError as e:
        print(f"agent_assist_bot: {e}", file=sys.stderr)
        return 2
    warm.preload()
    print(warm.READY_LINE, flush=True)
    try:
        args = warm.read_assignment(sys.stdin)
    except warm.AssignmentError as e:
        print(f"agent_assist_bot: {e}", file=sys.stderr)
        return 2
    if args is None:
        return 0
    log_timing("warm_bot_assigned", session=args.session_id)
    return run_call(args, keys)


if __name__ == "__main__":
    raise SystemExit(main())
