"""Every voice the Settings page offers really speaks (P5-5). Live: Deepgram key.

Flux voices are not in the models API, so the catalog's Flux list is the documented one;
this checks each id with a real /v2/speak (batch) call, plus one Aura-2 voice from the
models API on /v1/speak. Run: `uv run pytest -m live server/tests/test_voice_catalog_live.py`.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from agent_assist_server import voice_catalog as vc

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("DEEPGRAM_API_KEY"), reason="DEEPGRAM_API_KEY not set"),
]

LINE = "Hi."


async def speak_all(model: str, ids: list[str]) -> dict[str, str]:
    sem = asyncio.Semaphore(4)

    async def one(voice: str) -> tuple[str, str]:
        async with sem:
            try:
                audio = await vc.synthesize(model, voice, LINE)
            except vc.SpeakError as e:
                return voice, str(e)
            return voice, "" if len(audio) > 500 else f"only {len(audio)} bytes"

    return {v: err for v, err in await asyncio.gather(*(one(i) for i in ids)) if err}


async def test_every_flux_voice_speaks_on_v2():
    ids = [v.id for v in await vc.DeepgramVoiceCatalog().voices("flux")]
    failed = await speak_all("flux", ids)
    print(f"flux voices checked: {len(ids)}, failed: {failed}")
    assert failed == {}


async def test_aura_voices_come_from_the_api_and_speak_on_v1():
    voices = await vc.DeepgramVoiceCatalog().voices("aura-2")
    ids = [v.id for v in voices]
    assert "aura-2-thalia-en" in ids and len(ids) >= 20
    assert await speak_all("aura-2", ["aura-2-thalia-en", ids[0]]) == {}


async def test_a_flux_voice_on_v1_is_refused():
    """Why the bot needs DeepgramFluxTTSService for Flux, not the Aura service."""
    url, params = vc.speak_request("aura-2", "flux-hannah-en")
    assert url.endswith("/v1/speak")
    with pytest.raises(vc.SpeakError):
        await vc.synthesize("aura-2", "flux-hannah-en", LINE)
