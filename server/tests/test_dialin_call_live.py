"""Live IN-1: a call to the Daily number starts the bot and the caller hears the greeting
within 2 s of the bot joining.

This test needs a running server that Daily's pinless_dialin config points at, so it
can not run in CI. Setup (see README "Live phone dial-in test"):

    uv run --env-file .env uvicorn agent_assist_server.app:app --port 8000 \
        > /tmp/server.log 2>&1 &
    cloudflared tunnel --url http://127.0.0.1:8000     # public URL for Daily
    # point the number's room_creation_api at https://<tunnel>/daily/dialin
    DIALIN_TEST_SERVER_LOG=/tmp/server.log \
        uv run --env-file .env pytest -m live server/tests/test_dialin_call_live.py -s

How the call is placed (first match wins):
- DIALIN_TEST_SIP_URI: the number's pinless SIP URI (GET /domain-dialin-config). A
  caller room dials it with SIP dial-out, which hits the same pinless path as a phone
  call minus the carrier leg.
- DIALIN_TEST_PSTN=1: the caller room dials DAILY_PHONE_NUMBER over PSTN (needs PSTN
  dial-out enabled on the Daily domain).
- DIALIN_TEST_WAIT_SECS=N: nobody dials; a person calls the number within N seconds.

The bot and server write `[timing] event=... t=<epoch>` lines to the server log
(agent_assist_bot.timing). The test reads them for this call and asserts
greeting_first_audio - bot_joined < 2 s. With an automated caller it also records what
the caller hears and reports when the greeting reached them.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import httpx
import pytest
from agent_assist_bot.timing import parse_timing

pytestmark = pytest.mark.live

GREETING_BUDGET_SECS = 2.0
SPEECH_RMS = 300.0
CALLER_NAME = "pstn-caller"
API = os.getenv("DAILY_API_URL", "https://api.daily.co/v1")
ORDER = (
    "webhook_received",
    "room_created",
    "bot_launched",
    "bot_joined",
    "dialin_ready",
    "call_forwarded",
    "dialin_connected",
    "caller_joined",
    "greeting_tts_audio",
    "greeting_first_audio",
)


def read_timeline(log: Path, since: float) -> dict[str, dict[str, float]]:
    """{session: {event: t}} for timing lines at or after `since` (first of each)."""
    sessions: dict[str, dict[str, float]] = {}
    for line in log.read_text(errors="replace").splitlines():
        parsed = parse_timing(line)
        if not parsed:
            continue
        event, t, fields = parsed
        if t < since or "session" not in fields:
            continue
        sessions.setdefault(fields["session"], {}).setdefault(event, t)
    return sessions


def format_timeline(events: dict[str, float]) -> str:
    t0 = events.get("webhook_received", min(events.values()))
    rows = [f"  {'event':<22} {'epoch':>15}  {'+s from webhook':>15}"]
    for name in ORDER:
        if name in events:
            rows.append(f"  {name:<22} {events[name]:>15.3f}  {events[name] - t0:>15.3f}")
    return "\n".join(rows)


async def wait_for_events(log: Path, since: float, need: tuple[str, ...], timeout: float):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for sid, events in read_timeline(log, since).items():
            if all(e in events for e in need):
                return sid, events
        await asyncio.sleep(0.25)
    return None, read_timeline(log, since)


@pytest.fixture
def server_log():
    path = os.getenv("DIALIN_TEST_SERVER_LOG")
    missing = [k for k in ("DAILY_API_KEY", "DAILY_PHONE_NUMBER") if not os.getenv(k)]
    if not path or missing:
        pytest.skip(f"needs DIALIN_TEST_SERVER_LOG and {', '.join(missing) or 'keys'}")
    return Path(path)


class CallerRoom:
    """A throwaway Daily room whose owner dials the number and records the call leg."""

    def __init__(self):
        self.headers = {"Authorization": f"Bearer {os.environ['DAILY_API_KEY']}"}
        self.room: dict | None = None
        self.participant = None

    async def __aenter__(self):
        from harness.participant import HarnessParticipant

        async with httpx.AsyncClient(base_url=API, headers=self.headers, timeout=30) as c:
            r = await c.post(
                "/rooms",
                json={
                    "name": f"dialin-test-caller-{int(time.time())}",
                    "privacy": "private",
                    "properties": {"enable_dialout": True, "exp": int(time.time()) + 900},
                },
            )
            assert r.status_code == 200, r.text
            self.room = r.json()
            r = await c.post(
                "/meeting-tokens",
                json={
                    "properties": {
                        "room_name": self.room["name"],
                        "is_owner": True,
                        "user_name": "dialin-test",
                    }
                },
            )
            assert r.status_code == 200, r.text
        self.participant = HarnessParticipant("dialin-test")
        await self.participant.join(self.room["url"], r.json()["token"])
        return self

    async def dial(self, settings: dict) -> float:
        p = self.participant
        fut = p._loop.create_future()
        t = time.time()
        p._client.start_dialout(
            {**settings, "displayName": CALLER_NAME}, completion=p._completion(fut)
        )
        _session, error = await fut
        assert not error, f"dial-out failed: {error}"
        return t

    async def __aexit__(self, *exc):
        if self.participant is not None:
            await self.participant.leave()  # hangs up the dial-out leg
        if self.room:
            async with httpx.AsyncClient(base_url=API, headers=self.headers, timeout=30) as c:
                await c.delete(f"/rooms/{self.room['name']}")


async def test_live_phone_call_hears_greeting_within_2s_of_bot_join(server_log):
    sip_uri = os.getenv("DIALIN_TEST_SIP_URI")
    pstn = os.getenv("DIALIN_TEST_PSTN") == "1"
    wait_secs = float(os.getenv("DIALIN_TEST_WAIT_SECS", "0"))
    need = ("bot_joined", "caller_joined", "greeting_first_audio")
    since = time.time()

    if not (sip_uri or pstn):
        if not wait_secs:
            pytest.skip("set DIALIN_TEST_SIP_URI, DIALIN_TEST_PSTN=1 or DIALIN_TEST_WAIT_SECS")
        print(f"\ncall {os.environ['DAILY_PHONE_NUMBER'][-4:]} now (waiting {wait_secs:.0f}s)")
        sid, events = await wait_for_events(server_log, since, need, wait_secs)
        assert sid, f"no complete call timeline in the server log: {events}"
        heard_at = None
    else:
        async with CallerRoom() as caller:
            if sip_uri:
                target = {"sipUri": sip_uri}
            else:
                target = {"phoneNumber": os.environ["DAILY_PHONE_NUMBER"]}
            t_dial = await caller.dial(target)
            print(f"\ndialed via {'SIP' if sip_uri else 'PSTN'} at {t_dial:.3f}")
            sid, events = await wait_for_events(server_log, since, need, 90)
            assert sid, f"no complete call timeline in the server log: {events}"
            # Let the greeting play, then find when the caller started hearing it.
            await asyncio.sleep(4)
            rec = caller.participant.recording_for(CALLER_NAME)
            heard_at = rec.first_speech_after(events["caller_joined"], threshold=SPEECH_RMS)

    print(f"session {sid}\n{format_timeline(events)}")
    latency = events["greeting_first_audio"] - events["bot_joined"]
    print(f"greeting first audio - bot joined   = {latency:.3f}s (budget {GREETING_BUDGET_SECS}s)")
    caller_wait = events["greeting_first_audio"] - events["caller_joined"]
    print(f"greeting first audio - caller joined = {caller_wait:.3f}s")
    if heard_at is not None:
        print(f"caller heard greeting at {heard_at:.3f}: {heard_at - events['bot_joined']:.3f}s "
              "after bot joined")
    assert latency < GREETING_BUDGET_SECS
