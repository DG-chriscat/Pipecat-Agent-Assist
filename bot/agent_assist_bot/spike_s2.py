"""Spike S2 entry point: listen only bot, one Deepgram stream per participant.

uv run python -m agent_assist_bot.spike_s2 --room-url URL

Prints one `S2LINE {json}` line per final transcript line:
{speaker_role, participant_id, text, is_final, ts}.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from agent_assist_bot.listen import run_listen_bot


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="agent_assist_bot.spike_s2")
    parser.add_argument("--room-url", required=True, help="Daily room URL to listen in")
    return parser.parse_args(argv)


async def amain(argv: list[str]) -> int:
    args = parse_args(argv)
    print(f"Room URL: {args.room_url}", flush=True)
    await run_listen_bot(args.room_url)
    return 0


def main() -> None:
    sys.exit(asyncio.run(amain(sys.argv[1:])))


if __name__ == "__main__":
    main()
