"""Print the docs/demo.md results table from DEMO_RESULTS JSON lines.

    uv run python bot/tests/harness/demo_table.py /tmp/demo.jsonl
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.demo import HEADER, scorecard, table_row  # noqa: E402


def main(path: str) -> None:
    runs = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    runs.sort(key=lambda r: r["run"])
    print(HEADER)
    for r in runs:
        print(table_row(r))
    print()
    for k, v in scorecard(runs).items():
        print(f"{k}: {v}")
    print()
    for r in runs:
        gaps = r.get("bot_response_secs") or []
        print(
            f"run {r['run']}: heard={r.get('heard_name')!r} "
            f"response_median={r.get('bot_response_median_secs')} max={max(gaps, default=None)} "
            f"draft={r.get('end_to_draft_secs')} reason={r.get('wrap_up_reason')} "
            f"decision={r.get('agent_decision')} error={r.get('error')}"
        )


if __name__ == "__main__":
    main(sys.argv[1])
