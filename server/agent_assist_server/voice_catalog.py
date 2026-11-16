"""Voices for the Settings page (P5-5, PRD AP-20, IN-11) and the Preview sample.

- Aura-2: Deepgram's models API (`GET /v1/models`), English voices of the aura-2
  architecture. Cached once it succeeds; if it fails (or there is no key) the known
  list below is used so the page still works.
- Flux: the models API does not list Flux voices (checked Nov 16, 2026), so this is the
  English list from https://developers.deepgram.com/docs/flux-tts/voices. Each id is
  checked with a real /v2/speak call by server/tests/test_voice_catalog_live.py.

Preview (`synthesize`) is one REST call: /v1/speak for Aura-2, /v2/speak (batch) for
Flux, MP3 back. The key never leaves the server and never appears in an error.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

from agent_assist_server.bot_settings import Voice

logger = logging.getLogger(__name__)

API_URL = "https://api.deepgram.com"
KEY_ENV = "DEEPGRAM_API_KEY"
TIMEOUT_SECS = 10.0

FLUX_VOICES = (
    Voice("flux-alexis-en", "Alexis", "American", "Female"),
    Voice("flux-bree-en", "Bree", "American", "Female"),
    Voice("flux-brittany-en", "Brittany", "American", "Female"),
    Voice("flux-brooke-en", "Brooke", "American", "Female"),
    Voice("flux-bruce-en", "Bruce", "American", "Male"),
    Voice("flux-cliff-en", "Cliff", "American", "Male"),
    Voice("flux-cole-en", "Cole", "American", "Male"),
    Voice("flux-colin-en", "Colin", "British", "Male"),
    Voice("flux-conor-en", "Conor", "British", "Male"),
    Voice("flux-donovan-en", "Donovan", "American", "Male"),
    Voice("flux-drew-en", "Drew", "American", "Male"),
    Voice("flux-elise-en", "Elise", "American", "Female"),
    Voice("flux-gemma-en", "Gemma", "British", "Female"),
    Voice("flux-haley-en", "Haley", "American", "Female"),
    Voice("flux-hannah-en", "Hannah", "American", "Female"),
    Voice("flux-heather-en", "Heather", "American", "Female"),
    Voice("flux-jack-en", "Jack", "British", "Male"),
    Voice("flux-kai-en", "Kai", "Singaporean", "Male"),
    Voice("flux-kelsey-en", "Kelsey", "American", "Female"),
    Voice("flux-kit-en", "Kit", "British", "Male"),
    Voice("flux-maeve-en", "Maeve", "Irish", "Female"),
    Voice("flux-marcelo-en", "Marcelo", "Filipino", "Male"),
    Voice("flux-marcus-en", "Marcus", "American", "Male"),
    Voice("flux-meena-en", "Meena", "Indian", "Female"),
    Voice("flux-meghan-en", "Meghan", "American", "Female"),
    Voice("flux-miles-en", "Miles", "American", "Male"),
    Voice("flux-naveen-en", "Naveen", "Indian", "Male"),
    Voice("flux-paige-en", "Paige", "American", "Female"),
    Voice("flux-priya-en", "Priya", "Indian", "Female"),
    Voice("flux-rufus-en", "Rufus", "British", "Male"),
    Voice("flux-sean-en", "Sean", "British", "Male"),
    Voice("flux-sharon-en", "Sharon", "Australian", "Female"),
    Voice("flux-sienna-en", "Sienna", "American", "Female"),
    Voice("flux-tanner-en", "Tanner", "British", "Male"),
    Voice("flux-wade-en", "Wade", "American", "Male"),
    Voice("flux-wes-en", "Wes", "American", "Male"),
)

# Fallback when the models API cannot be reached (from the API on Nov 16, 2026).
_AURA_FALLBACK = {
    "amalthea": "Female", "andromeda": "Female", "apollo": "Male", "arcas": "Male",
    "aries": "Male", "asteria": "Female", "athena": "Female", "atlas": "Male",
    "aurora": "Female", "callista": "Female", "cora": "Female", "cordelia": "Female",
    "delia": "Female", "draco": "Male", "electra": "Female", "harmonia": "Female",
    "helena": "Female", "hera": "Female", "hermes": "Male", "hyperion": "Male",
    "iris": "Female", "janus": "Female", "juno": "Female", "jupiter": "Male",
    "luna": "Female", "mars": "Male", "minerva": "Female", "neptune": "Male",
    "odysseus": "Male", "ophelia": "Female", "orion": "Male", "orpheus": "Male",
    "pandora": "Female", "phoebe": "Female", "pluto": "Male", "saturn": "Male",
    "selene": "Female", "thalia": "Female", "theia": "Female", "vesta": "Female",
    "zeus": "Male",
}  # fmt: skip
AURA_FALLBACK = tuple(
    Voice(f"aura-2-{n}-en", n.capitalize(), "", g) for n, g in sorted(_AURA_FALLBACK.items())
)


def _gender(tags: list[str]) -> str:
    if "feminine" in tags:
        return "Female"
    if "masculine" in tags:
        return "Male"
    return ""


def aura_voices_from_models(data: dict) -> list[Voice]:
    seen: dict[str, Voice] = {}
    for m in data.get("tts") or []:
        cid = str(m.get("canonical_name") or "")
        langs = m.get("languages") or []
        if m.get("architecture") != "aura-2" or not cid.endswith("-en") or "en" not in langs:
            continue
        if cid in seen:
            continue
        meta = m.get("metadata") or {}
        name = str(m.get("name") or cid).capitalize()
        seen[cid] = Voice(cid, name, str(meta.get("accent") or ""), _gender(meta.get("tags") or []))
    return sorted(seen.values(), key=lambda v: v.id)


class DeepgramVoiceCatalog:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_url: str = API_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._api_key = os.environ.get(KEY_ENV, "") if api_key is None else api_key
        self._api_url = api_url
        self._transport = transport
        self._aura: list[Voice] | None = None
        self._lock = asyncio.Lock()

    async def _fetch_aura(self) -> list[Voice] | None:
        if not self._api_key:
            return None
        try:
            async with httpx.AsyncClient(
                base_url=self._api_url, transport=self._transport, timeout=TIMEOUT_SECS
            ) as client:
                r = await client.get(
                    "/v1/models", headers={"Authorization": f"Token {self._api_key}"}
                )
            r.raise_for_status()
            return aura_voices_from_models(r.json()) or None
        except (httpx.HTTPError, ValueError) as e:
            logger.warning(
                "Deepgram models API failed (%s); using the known Aura-2 list", type(e).__name__
            )
            return None

    async def voices(self, model: str) -> list[Voice]:
        if model == "flux":
            return list(FLUX_VOICES)
        if model != "aura-2":
            return []
        if self._aura is None:
            async with self._lock:
                if self._aura is None:
                    fetched = await self._fetch_aura()
                    if fetched is None:
                        return list(AURA_FALLBACK)
                    self._aura = fetched
        return list(self._aura)


class SpeakError(RuntimeError):
    pass


def speak_request(
    model: str,
    voice: str,
    api_url: str = API_URL,
    *,
    speed: float | None = None,
    expressivity: int | None = None,
) -> tuple[str, dict[str, str]]:
    """URL and query for a sample. Flux takes speed and expressivity (P5-8, AP-22) as
    query parameters; Aura-2 does not get them."""
    path = "/v2/speak" if model == "flux" else "/v1/speak"
    params = {"model": voice, "encoding": "mp3"}
    if model == "flux":
        if speed is not None:
            params["speed"] = str(speed)
        if expressivity is not None:
            params["expressivity"] = str(expressivity)
    return f"{api_url}{path}", params


async def synthesize(
    model: str,
    voice: str,
    text: str,
    *,
    api_key: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    speed: float | None = None,
    expressivity: int | None = None,
) -> bytes:
    """One sample line as MP3 (Settings Preview)."""
    key = os.environ.get(KEY_ENV, "") if api_key is None else api_key
    if not key:
        raise SpeakError(f"{KEY_ENV} is not configured")
    url, params = speak_request(model, voice, speed=speed, expressivity=expressivity)
    async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT_SECS) as client:
        try:
            r = await client.post(
                url,
                params=params,
                json={"text": text},
                headers={"Authorization": f"Token {key}"},
            )
        except httpx.HTTPError as e:
            raise SpeakError(f"Deepgram speak failed: {type(e).__name__}") from None
    if r.status_code != 200:
        code = ""
        try:
            code = str(r.json().get("err_code") or "")
        except ValueError:
            pass
        raise SpeakError(f"Deepgram speak returned {r.status_code} {code}".strip())
    return r.content
