"""Flux voice controls read back test (P5-8, PRD AP-22). Live.

The P5-5 read back lines plus "Thanks, you are verified." on flux-alexis-en at speeds
0.8 to 1.2 (expressivity 0) and at expressivity -2 and 2 (speed 1.0), over the
/v2/speak websocket the bot uses with speed and expressivity on the URL (as
DeepgramFluxTTSService connects). Each line is transcribed by nova-3 and its entities
scored. The speeds that score 100 percent set the Settings slider range. Raw results go
to READBACK_OUT (default /tmp/tts-flux-controls.json); the tables go into
docs/spikes/tts-flux-controls.md.

    READBACK_RUNS=5 uv run pytest -m live bot/tests/test_tts_flux_controls_live.py -s
"""

from __future__ import annotations

import os
import statistics

import pytest
from harness import readback as rb

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("DEEPGRAM_API_KEY"), reason="DEEPGRAM_API_KEY not set"),
]

RUNS = int(os.environ.get("READBACK_RUNS", "3"))


async def test_read_back_entities_per_flux_setting():
    results = await rb.run_flux_controls(runs=RUNS, keep_audio=os.environ.get("READBACK_AUDIO"))
    out = os.environ.get("READBACK_OUT", "/tmp/tts-flux-controls.json")
    with open(out, "w") as f:
        f.write(rb.to_json(results))
    summary = rb.summarize(results)
    print()
    print(rb.markdown_table(summary))
    for label in summary:
        rs = [r for r in results if r.voice == label]
        print(
            label,
            "audio_s median",
            round(statistics.median(r.audio_s for r in rs), 2),
            {k: round(v, 3) for k, v in summary[label]["ttfa_by_line"].items()},
        )
    for r in results:
        if not all(r.entities.values()):
            print(f"MISS {r.voice} {r.line} run{r.run}: {r.heard!r} {r.entities}")
    assert list(summary) == [s.label for s in rb.FLUX_CONTROL_SETTINGS]
    assert len(results) == len(rb.FLUX_CONTROL_SETTINGS) * len(rb.CONTROL_LINES) * RUNS
    assert all(r.heard for r in results)
