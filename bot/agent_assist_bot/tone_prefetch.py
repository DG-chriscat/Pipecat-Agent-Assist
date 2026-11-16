"""Download the Pipecat Tone reader model ahead of time (image build), P4-5.

The bot's tone reader loads its model while the pipeline starts; without this the
first call in a fresh container would wait for the Hugging Face download. This runs
the reader's own download (`PipecatToneReaderModel.download`: the same
`snapshot_download` of `pipecat-ai/tone-models`, revision `reader-rc2`, fp16 files)
and loads the model once to prove the files work.

    python -m agent_assist_bot.tone_prefetch
"""

from __future__ import annotations

import time

from agent_assist_bot.tone import TONE_PRECISION, TONE_REVISION, TONE_THREADS


def main() -> None:
    from pipecat.processors.tone.reader.model import TONE_REPO, PipecatToneReaderModel

    started = time.perf_counter()
    model = PipecatToneReaderModel.download(
        revision=TONE_REVISION, precision=TONE_PRECISION, threads=TONE_THREADS
    )
    label = model.tag("Thanks, that helps.")["emotion"]["label"]
    print(
        f"tone model ready: {TONE_REPO}@{TONE_REVISION} {TONE_PRECISION} "
        f"in {time.perf_counter() - started:.1f}s (check read: {label})"
    )


if __name__ == "__main__":
    main()
