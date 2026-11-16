"""TTS read back test: Aura-2 Thalia vs two Flux voices (P5-5, PRD IN-11). Live.

Synthesizes the bot's read back and hold lines on each voice over the speak websocket
the bot uses, transcribes each with Deepgram nova-3, and scores the key entities
(member ID, DOB, claim number, amount, time, address, business days). Writes the raw
results to READBACK_OUT (default /tmp/tts-readback.json) and prints the table that
goes into docs/spikes/tts-flux-vs-aura.md.

    uv run pytest -m live bot/tests/test_tts_readback_live.py -s
"""

from __future__ import annotations

import os

import pytest
from harness import readback as rb

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("DEEPGRAM_API_KEY"), reason="DEEPGRAM_API_KEY not set"),
]

VOICES = ["aura-2-thalia-en", "flux-hannah-en", "flux-alexis-en"]
RUNS = int(os.environ.get("READBACK_RUNS", "3"))


async def test_read_back_entities_per_voice():
    results = await rb.run(VOICES, runs=RUNS, keep_audio=os.environ.get("READBACK_AUDIO"))
    out = os.environ.get("READBACK_OUT", "/tmp/tts-readback.json")
    with open(out, "w") as f:
        f.write(rb.to_json(results))
    summary = rb.summarize(results)
    print()
    print(rb.markdown_table(summary))
    for voice, s in summary.items():
        print(voice, {k: round(v, 3) for k, v in s["ttfa_by_line"].items()})
    for r in results:
        if not all(r.entities.values()):
            print(f"MISS {r.voice} {r.line} run{r.run}: {r.heard!r} {r.entities}")
    assert set(summary) == set(VOICES)
    # Every line made audio and a transcript on every voice.
    assert len(results) == len(VOICES) * len(rb.LINES) * RUNS
    assert all(r.heard for r in results)
