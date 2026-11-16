"""Spike S2 entry point: listen only bot, one Deepgram stream per participant.

uv run python -m agent_assist_bot.spike_s2 --room-url URL [--stt-model MODEL]

Prints one `S2LINE {json}` line per final transcript line:
{speaker_role, participant_id, text, is_final, ts}. `--stt-model` is an assist STT
model (P5-7): nova-3-general (default), nova-3-medical, or flux-general-en.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from agent_assist_bot.listen import run_listen_bot
from agent_assist_bot.stt import ASSIST_MODELS, DEFAULT_ASSIST_MODEL


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="agent_assist_bot.spike_s2")
    parser.add_argument("--room-url", required=True, help="Daily room URL to listen in")
    parser.add_argument(
        "--stt-model", choices=ASSIST_MODELS, default=DEFAULT_ASSIST_MODEL, help="STT model"
    )
    return parser.parse_args(argv)


async def amain(argv: list[str]) -> int:
    args = parse_args(argv)
    print(f"Room URL: {args.room_url} stt={args.stt_model}", flush=True)
    await run_listen_bot(args.room_url, model=args.stt_model)
    return 0


def main() -> None:
    sys.exit(asyncio.run(amain(sys.argv[1:])))


if __name__ == "__main__":
    main()
