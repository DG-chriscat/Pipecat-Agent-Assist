"""HarnessParticipant records app messages it receives (P3-4: the caller must get none).

No CallClient here: Daily.init aborts the process if Pipecat already initialized
Daily earlier in the same test run, so only the recording logic is exercised.
"""

import asyncio

from harness.participant import HarnessParticipant


def _bare() -> HarnessParticipant:
    p = HarnessParticipant.__new__(HarnessParticipant)
    p._loop = asyncio.get_running_loop()
    p._changed = asyncio.Event()
    p.app_messages = []
    p.user_name = "agent"
    return p


async def test_harness_records_app_messages_with_sender_and_time():
    p = _bare()
    # Daily calls handlers on its own thread; the harness hops back to the loop.
    await asyncio.to_thread(
        p._on_app_message, {"label": "rtvi-ai", "type": "server-message"}, "bot-id"
    )
    await p.wait_for_app_messages(1, timeout=2)
    [(t, sender, msg)] = p.app_messages
    assert sender == "bot-id"
    assert msg == {"label": "rtvi-ai", "type": "server-message"}
    assert t > 0


class FakeClient:
    def __init__(self, loop, error=None):
        self.sent = []
        self._loop = loop
        self._error = error

    def send_app_message(self, message, participant_id=None, completion=None):
        self.sent.append((message, participant_id))
        completion(self._error)


async def test_send_app_message_to_one_participant():
    p = _bare()
    p._client = FakeClient(p._loop)
    await p.send_app_message({"hello": 1}, "bot-id")
    assert p._client.sent == [({"hello": 1}, "bot-id")]


async def test_send_app_message_raises_on_error():
    import pytest

    p = _bare()
    p._client = FakeClient(p._loop, error="not joined")
    with pytest.raises(RuntimeError, match="not joined"):
        await p.send_app_message({"hello": 1}, "bot-id")
