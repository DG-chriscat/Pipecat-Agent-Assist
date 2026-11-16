"""Stand-in for `python -m agent_assist_bot --warm` in the warm pool tests (P5-2).

    fake_warm_bot.py OUT_DIR warm   prints the ready line, reads one assignment from
                                    stdin, writes it to OUT_DIR/<pid>.json, then runs
                                    until SIGTERM; EOF before an assignment exits 0
    fake_warm_bot.py OUT_DIR crash  exits 3 before it is ready
    fake_warm_bot.py OUT_DIR cold SESSION   a cold start: writes OUT_DIR/cold-SESSION
                                    with the token from the env, then exits

FAKE_READY_DELAY (seconds) delays the ready line.
"""

import json
import os
import sys
import time
from pathlib import Path

READY_LINE = "agent_assist_bot warm ready"


def main() -> int:
    out, mode = Path(sys.argv[1]), sys.argv[2]
    if mode == "crash":
        return 3
    if mode == "cold":
        (out / f"cold-{sys.argv[3]}").write_text(os.environ.get("DAILY_ROOM_TOKEN", ""))
        return 0
    time.sleep(float(os.environ.get("FAKE_READY_DELAY") or 0))
    (out / f"spawned-{os.getpid()}").write_text(os.environ.get("DAILY_ROOM_TOKEN", "<unset>"))
    print("loading...", flush=True)
    print(READY_LINE, flush=True)
    line = sys.stdin.readline()
    if not line:
        return 0
    (out / f"{os.getpid()}.json").write_text(json.dumps(json.loads(line)))
    while True:  # "in the call" until terminated
        time.sleep(0.1)


if __name__ == "__main__":
    raise SystemExit(main())
