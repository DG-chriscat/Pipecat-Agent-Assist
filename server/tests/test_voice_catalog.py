"""Voice catalog for the Settings page (P5-5): Aura-2 from Deepgram's models API, Flux
from the documented list, no network (the API is faked with httpx.MockTransport)."""

from __future__ import annotations

import json

import httpx
import pytest
from agent_assist_server import voice_catalog as vc

MODELS = {
    "stt": [{"canonical_name": "nova-3-general"}],
    "tts": [
        {
            "canonical_name": "aura-2-thalia-en",
            "name": "thalia",
            "architecture": "aura-2",
            "languages": ["en", "en-US"],
            "metadata": {"accent": "American", "tags": ["feminine", "clear"]},
        },
        {
            "canonical_name": "aura-2-draco-en",
            "name": "draco",
            "architecture": "aura-2",
            "languages": ["en", "en-GB"],
            "metadata": {"accent": "British", "tags": ["masculine"]},
        },
        {
            "canonical_name": "aura-2-celeste-es",
            "name": "celeste",
            "architecture": "aura-2",
            "languages": ["es"],
            "metadata": {},
        },
        {"canonical_name": "aura-asteria-en", "name": "asteria", "architecture": "aura"},
        # Listed twice by the API (two versions): shown once.
        {
            "canonical_name": "aura-2-thalia-en",
            "name": "thalia",
            "architecture": "aura-2",
            "languages": ["en"],
            "metadata": {"accent": "American", "tags": ["feminine"]},
        },
    ],
}


class Api:
    def __init__(self, status=200, body=None):
        self.calls: list[httpx.Request] = []
        self.status = status
        self.body = MODELS if body is None else body

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return httpx.Response(self.status, content=json.dumps(self.body))


def catalog(api: Api, **kw) -> vc.DeepgramVoiceCatalog:
    return vc.DeepgramVoiceCatalog(
        api_key="test-key", transport=httpx.MockTransport(api.handler), **kw
    )


async def test_aura_voices_come_from_the_models_api_english_only_sorted():
    api = Api()
    voices = await catalog(api).voices("aura-2")
    assert [(v.id, v.name, v.accent, v.gender) for v in voices] == [
        ("aura-2-draco-en", "Draco", "British", "Male"),
        ("aura-2-thalia-en", "Thalia", "American", "Female"),
    ]
    (req,) = api.calls
    assert req.url.path == "/v1/models"
    assert req.headers["authorization"] == "Token test-key"


async def test_aura_list_is_cached():
    api = Api()
    c = catalog(api)
    await c.voices("aura-2")
    await c.voices("aura-2")
    assert len(api.calls) == 1


async def test_flux_voices_are_the_documented_english_list_without_a_call():
    api = Api()
    voices = await catalog(api).voices("flux")
    ids = [v.id for v in voices]
    assert {"flux-hannah-en", "flux-alexis-en", "flux-heather-en"} <= set(ids)
    assert all(i.startswith("flux-") and i.endswith("-en") for i in ids)
    assert ids == sorted(ids)
    assert api.calls == []


async def test_models_api_failure_falls_back_to_the_known_aura_list():
    voices = await catalog(Api(status=500)).voices("aura-2")
    assert "aura-2-thalia-en" in [v.id for v in voices]


async def test_a_failed_lookup_is_retried_on_the_next_request():
    api = Api(status=500)
    c = catalog(api)
    await c.voices("aura-2")
    api.status = 200
    voices = await c.voices("aura-2")
    assert [v.id for v in voices] == ["aura-2-draco-en", "aura-2-thalia-en"]
    assert len(api.calls) == 2


async def test_no_api_key_uses_the_known_aura_list():
    c = vc.DeepgramVoiceCatalog(api_key="")
    assert "aura-2-thalia-en" in [v.id for v in await c.voices("aura-2")]


async def test_unknown_model_has_no_voices():
    assert await catalog(Api()).voices("aura-1") == []


@pytest.mark.parametrize("model", ["aura-2", "flux"])
def test_preview_request_targets_the_models_endpoint(model):
    voice = "aura-2-thalia-en" if model == "aura-2" else "flux-hannah-en"
    url, params = vc.speak_request(model, voice)
    if model == "aura-2":
        assert url == "https://api.deepgram.com/v1/speak"
    else:
        assert url == "https://api.deepgram.com/v2/speak"
    assert params["model"] == voice and params["encoding"] == "mp3"


async def test_preview_posts_the_sample_line_and_returns_audio():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"ID3fake", headers={"content-type": "audio/mpeg"})

    audio = await vc.synthesize(
        "flux",
        "flux-hannah-en",
        "Hello there.",
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    assert audio == b"ID3fake"
    (req,) = seen
    assert req.url.path == "/v2/speak" and req.url.params["model"] == "flux-hannah-en"
    assert json.loads(req.content) == {"text": "Hello there."}


async def test_preview_error_does_not_echo_the_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"err_code": "BAD", "err_msg": "nope"})

    with pytest.raises(vc.SpeakError) as e:
        await vc.synthesize(
            "aura-2",
            "aura-2-thalia-en",
            "Hi.",
            api_key="sekrit",
            transport=httpx.MockTransport(handler),
        )
    assert "sekrit" not in str(e.value) and "400" in str(e.value)
