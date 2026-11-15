"""Turn taking helpers for scripted live callers (P2-3 intake, P2-5 end to end)."""

from __future__ import annotations

import asyncio
import os
import time

import httpx

from harness.participant import SAMPLE_RATE

SPEECH_RMS = 300.0


async def wait_bot_turn(rec, after: float, start_timeout: float = 25, quiet: float = 1.2):
    """Wait for the bot to start speaking after `after`, then to go quiet.

    Returns (start, end) or None when the bot never started within start_timeout.
    """
    deadline = time.time() + start_timeout
    start = None
    while start is None:
        start = rec.first_speech_after(after, threshold=SPEECH_RMS)
        if start is None:
            if time.time() > deadline:
                return None
            await asyncio.sleep(0.05)
    while True:
        segs = [s for s in rec.segments(threshold=SPEECH_RMS) if s[1] > start]
        if segs and time.time() - segs[-1][1] >= quiet:
            return start, segs[-1][1]
        if time.time() - start > 60:  # never wait forever on a dead connection
            return start, segs[-1][1] if segs else start
        await asyncio.sleep(0.1)


def audio_slice(rec, start: float, end: float) -> bytes:
    return b"".join(c for t, c in rec.chunks if start - 0.05 <= t <= end + 0.05)


async def transcribe(pcm: bytes) -> str:
    """Deepgram prerecorded STT of 16 kHz mono PCM (needs DEEPGRAM_API_KEY)."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.deepgram.com/v1/listen",
            params={"model": "nova-3", "encoding": "linear16", "sample_rate": SAMPLE_RATE},
            headers={"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}"},
            content=pcm,
        )
    resp.raise_for_status()
    return resp.json()["results"]["channels"][0]["alternatives"][0]["transcript"]
