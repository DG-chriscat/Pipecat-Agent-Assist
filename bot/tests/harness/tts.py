"""Generate harness utterances with Deepgram TTS at test time (nothing committed)."""

from __future__ import annotations

import asyncio
import os

import httpx

DEEPGRAM_SPEAK_URL = "https://api.deepgram.com/v1/speak"
DEFAULT_VOICE = "aura-2-arcas-en"
ATTEMPTS = 5
RETRY_DELAY_SECS = 3.0


async def synthesize(
    text: str,
    *,
    sample_rate: int = 16000,
    voice: str = DEFAULT_VOICE,
    api_key: str | None = None,
) -> bytes:
    """Return raw 16-bit mono PCM for `text` at `sample_rate`.

    Retries a non-200 reply (P5-4: a one-off HTTP 400 on a line that synthesized fine
    a minute later failed whole demo runs before the call started). The key is read
    into a local, not the `api_key` argument, so a traceback never shows it."""
    key = api_key or os.environ["DEEPGRAM_API_KEY"]
    status, body = 0, ""
    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(ATTEMPTS):
            if attempt:
                await asyncio.sleep(RETRY_DELAY_SECS * attempt)
            resp = await client.post(
                DEEPGRAM_SPEAK_URL,
                params={
                    "model": voice,
                    "encoding": "linear16",
                    "sample_rate": sample_rate,
                    "container": "none",
                },
                headers={"Authorization": f"Token {key}"},
                json={"text": text},
            )
            if resp.status_code == 200:
                return resp.content
            status, body = resp.status_code, resp.text[:300]
    raise RuntimeError(f"Deepgram TTS failed: HTTP {status} after {ATTEMPTS} attempts: {body}")
