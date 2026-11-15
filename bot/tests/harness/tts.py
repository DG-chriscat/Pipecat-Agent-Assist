"""Generate harness utterances with Deepgram TTS at test time (nothing committed)."""

from __future__ import annotations

import os

import httpx

DEEPGRAM_SPEAK_URL = "https://api.deepgram.com/v1/speak"
DEFAULT_VOICE = "aura-2-arcas-en"


async def synthesize(
    text: str,
    *,
    sample_rate: int = 16000,
    voice: str = DEFAULT_VOICE,
    api_key: str | None = None,
) -> bytes:
    """Return raw 16-bit mono PCM for `text` at `sample_rate`."""
    api_key = api_key or os.environ["DEEPGRAM_API_KEY"]
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            DEEPGRAM_SPEAK_URL,
            params={
                "model": voice,
                "encoding": "linear16",
                "sample_rate": sample_rate,
                "container": "none",
            },
            headers={"Authorization": f"Token {api_key}"},
            json={"text": text},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Deepgram TTS failed: HTTP {resp.status_code}")
    return resp.content
