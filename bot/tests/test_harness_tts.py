"""P5-4: the harness TTS retries a transient Deepgram error (a 400 on one caller line
failed 3 of 10 demo runs before the call started)."""

import httpx
import pytest
from harness import tts


def _patch_client(monkeypatch, statuses: list[int]) -> list[int]:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = statuses[min(len(calls), len(statuses) - 1)]
        calls.append(status)
        return httpx.Response(status, content=b"\x00\x01" if status == 200 else b"{}")

    real = httpx.AsyncClient

    def client(**kw):
        return real(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(tts.httpx, "AsyncClient", client)
    monkeypatch.setattr(tts, "RETRY_DELAY_SECS", 0)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key-not-real")
    return calls


async def test_a_transient_error_is_retried(monkeypatch):
    calls = _patch_client(monkeypatch, [400, 200])
    assert await tts.synthesize("Yes, that's right.") == b"\x00\x01"
    assert calls == [400, 200]


async def test_gives_up_after_the_last_attempt(monkeypatch):
    calls = _patch_client(monkeypatch, [400])
    with pytest.raises(RuntimeError, match="HTTP 400"):
        await tts.synthesize("Yes, that's right.")
    assert len(calls) == tts.ATTEMPTS


async def test_the_error_says_what_deepgram_said(monkeypatch):
    _patch_client(monkeypatch, [400])
    with pytest.raises(RuntimeError, match=r"\{\}"):
        await tts.synthesize("Yes, that's right.")
