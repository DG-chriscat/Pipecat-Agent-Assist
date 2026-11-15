"""Start one bot process per call (dial-in or browser caller).

The bot runs as a separate Python process (`python -m agent_assist_bot`), the same
pattern as Pipecat's Daily dial-in example where the server spawns one bot per room.
Non-secret settings go on argv; the meeting token goes through the child's
environment so it never shows up in `ps` output or logs. API keys are inherited
from the server's environment.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass

from agent_assist_server import auth

logger = logging.getLogger(__name__)

BOT_MODULE = "agent_assist_bot"
TOKEN_ENV = "DAILY_ROOM_TOKEN"


@dataclass(frozen=True)
class BotLaunchRequest:
    """One bot process. call_id/call_domain are set for dial-in calls only; a browser
    caller (IN-12) leaves them None and the bot joins without dial-in settings."""

    room_url: str
    token: str
    session_id: str
    call_id: str | None = None
    call_domain: str | None = None


def build_bot_command(req: BotLaunchRequest, python: str | None = None) -> list[str]:
    cmd = [python or sys.executable, "-m", BOT_MODULE, "--room-url", req.room_url]
    if req.call_id:
        cmd += ["--call-id", req.call_id, "--call-domain", req.call_domain or ""]
    cmd += ["--session-id", req.session_id]
    return cmd


def build_bot_env(req: BotLaunchRequest, base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env[TOKEN_ENV] = req.token
    # Login (P2-6): the bot gets a key for the queue handoff endpoints, never the
    # app password or the session secret itself.
    password = env.pop(auth.PASSWORD_ENV, "")
    secret = env.pop(auth.SECRET_ENV, "")
    env.pop(auth.BOT_KEY_ENV, None)
    if password and secret:
        env[auth.BOT_KEY_ENV] = auth.bot_key(secret)
    return env


class BotLauncher:
    """Spawns bot subprocesses. Swap for a fake in tests."""

    def __init__(self) -> None:
        self.processes: dict[str, asyncio.subprocess.Process] = {}

    async def launch(self, req: BotLaunchRequest) -> int:
        proc = await asyncio.create_subprocess_exec(*build_bot_command(req), env=build_bot_env(req))
        self.processes[req.session_id] = proc
        logger.info("bot started session=%s room=%s pid=%s", req.session_id, req.room_url, proc.pid)
        return proc.pid
