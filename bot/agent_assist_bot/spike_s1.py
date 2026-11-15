"""Spike S1 entry point.

uv run python -m agent_assist_bot.spike_s1 [--room-url URL]

Without --room-url a temporary room (expires in under 10 minutes) is created and
deleted when the bot exits. The room URL is printed so a person or the test harness
can join.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable

from agent_assist_bot.bot import run_bot
from agent_assist_bot.daily_rooms import DailyRoomsClient


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="agent_assist_bot.spike_s1")
    parser.add_argument("--room-url", help="Existing Daily room URL; created if omitted")
    return parser.parse_args(argv)


async def amain(
    argv: list[str],
    rooms_factory: Callable[[], DailyRoomsClient] = DailyRoomsClient,
) -> int:
    args = parse_args(argv)
    if args.room_url:
        print(f"Room URL: {args.room_url}", flush=True)
        await run_bot(args.room_url)
        return 0

    async with rooms_factory() as rooms, rooms.temporary_room() as room:
        print(f"Room URL: {room.url}", flush=True)
        await run_bot(room.url)
    return 0


def main() -> None:
    sys.exit(asyncio.run(amain(sys.argv[1:])))


if __name__ == "__main__":
    main()
