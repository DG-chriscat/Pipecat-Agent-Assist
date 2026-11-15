"""Dial-in call forwarding and the call timeline log format. No network."""

import json

import httpx
import pytest
from agent_assist_bot import dialin, timing
from pipecat.frames.frames import BotStartedSpeakingFrame, TTSAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection

CFG = dialin.DialinConfig(
    call_id="call-1", call_domain="dom-1", api_key="fake-key", api_url="https://api.test/v1"
)


@pytest.mark.parametrize(
    "data,expected",
    [
        ({"provider": "signalwire", "sipEndpoint": "sip:a@b"}, "sip:a@b"),
        ({"sip_endpoint": "sip:a@b"}, "sip:a@b"),
        ("sip:a@b", "sip:a@b"),
        ({"provider": "signalwire"}, None),
        ("", None),
        (None, None),
    ],
)
def test_sip_uri_from_ready(data, expected):
    assert dialin.sip_uri_from_ready(data) == expected


async def test_forward_call_posts_string_sip_uri():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": "true"})

    await dialin.forward_call(CFG, "sip:room.0@x", transport=httpx.MockTransport(handler))
    assert seen["url"] == "https://api.test/v1/dialin/pinlessCallUpdate"
    assert seen["auth"] == "Bearer fake-key"
    assert seen["body"] == {"callId": "call-1", "callDomain": "dom-1", "sipUri": "sip:room.0@x"}


async def test_forward_call_error_has_status_but_not_key():
    def handler(request):
        return httpx.Response(400, json={"error": "invalid-request-error"})

    with pytest.raises(dialin.DialinForwardError) as e:
        await dialin.forward_call(CFG, "sip:x", transport=httpx.MockTransport(handler))
    assert "400" in str(e.value)
    assert "fake-key" not in str(e.value)


async def test_forward_call_network_error():
    def handler(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(dialin.DialinForwardError, match="ConnectError"):
        await dialin.forward_call(CFG, "sip:x", transport=httpx.MockTransport(handler))


def test_timing_line_round_trips():
    line = "2026-11-15 | INFO | x - " + timing.format_timing(
        "bot_joined", 1794779110.75812, session="s1"
    )
    assert timing.parse_timing(line) == ("bot_joined", 1794779110.758, {"session": "s1"})
    assert timing.parse_timing("no timing here") is None


def test_server_timing_line_matches_bot_format():
    import logging

    from agent_assist_server import dialin as server_dialin

    records = []
    handler = logging.Handler()
    handler.emit = records.append
    log = logging.getLogger(server_dialin.__name__)
    log.addHandler(handler)
    old = log.level
    log.setLevel(logging.INFO)
    try:
        t = server_dialin.log_timing("room_created", session="s1", room="r1")
    finally:
        log.removeHandler(handler)
        log.setLevel(old)
    event, parsed_t, fields = timing.parse_timing(records[0].getMessage())
    assert event == "room_created" and fields == {"session": "s1", "room": "r1"}
    assert abs(parsed_t - t) < 0.001


class _Collect(timing.FirstAudioTimer):
    def __init__(self):
        super().__init__("s1")
        self.pushed = []
        self.events = []

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.pushed.append(frame)

    def _once(self, event):
        if event not in self._seen:
            self._seen.add(event)
            self.events.append(event)


async def test_first_audio_timer_logs_each_event_once(monkeypatch):
    from pipecat.processors.frame_processor import FrameProcessor

    async def passthrough(self, frame, direction):
        return None

    monkeypatch.setattr(FrameProcessor, "process_frame", passthrough)
    t = _Collect()
    audio = TTSAudioRawFrame(audio=b"\x00\x00" * 160, sample_rate=16000, num_channels=1)
    await t.process_frame(audio, FrameDirection.DOWNSTREAM)
    await t.process_frame(audio, FrameDirection.DOWNSTREAM)
    await t.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    await t.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    assert t.events == ["greeting_tts_audio", "greeting_first_audio"]
    assert len(t.pushed) == 4
