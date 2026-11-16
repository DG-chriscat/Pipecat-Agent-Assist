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
