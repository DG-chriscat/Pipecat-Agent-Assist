"""Live IN-1: a call to the Daily number starts the bot and the caller hears the greeting
within 5 s of the bot joining.

Needs a deployed server that the number's pinless_dialin config points at, so it does
not run in CI. Run from a dev machine with keys in the environment:

    DIALIN_TEST_SIP_URI=<pinless sip_uri> \
        uv run --env-file .env pytest -m live server/tests/test_dialin_call_live.py -s

How the call is placed (first match wins):
- DIALIN_TEST_SIP_URI: the number's pinless SIP URI (GET /domain-dialin-config). A
  caller room dials it with SIP dial-out: the same pinless path as a phone call
  (webhook, room, bot, hold, pinlessCallUpdate) minus the carrier leg.
- DIALIN_TEST_PSTN=1: the caller room dials DAILY_PHONE_NUMBER over PSTN (needs PSTN
  dial-out enabled on the Daily domain).
- DIALIN_TEST_WAIT_SECS=N: nobody dials; a person calls the number within N seconds.
  Needs DIALIN_TEST_SERVER_LOG.

Measurements:
- Server side: the server and bot write `[timing] event=... t=<epoch>` lines
  (agent_assist_bot.timing). When DIALIN_TEST_SERVER_LOG names a file holding those
  logs (a local server's output, or `fly logs` saved to a file), the test prints the
  call timeline and asserts greeting_first_audio - bot_joined < 5 s.
- Caller side (automated caller only): the caller room records the call leg. Deepgram
  transcribes it with word timestamps to find when the greeting reached the caller, and
  Daily's meetings API gives the bot's join time (whole seconds) in the call room.
"""

from __future__ import annotations

import asyncio
import calendar
import os
import time
from pathlib import Path

import httpx
import pytest
from agent_assist_bot.timing import parse_timing

pytestmark = pytest.mark.live

GREETING_BUDGET_SECS = 5.0
CALLER_NAME = "pstn-caller"
BOT_NAMES = {"Intake Bot", "Vitals Assistant"}
GREETING_WORDS = {"thanks", "calling", "assistant"}
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
NEED = ("bot_joined", "caller_joined", "greeting_first_audio")


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


async def wait_for_timeline(log: Path, since: float, timeout: float):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for sid, events in read_timeline(log, since).items():
            if all(e in events for e in NEED):
                return sid, events
        await asyncio.sleep(0.25)
    return None, read_timeline(log, since)


def daily_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['DAILY_API_KEY']}"}


async def find_call_room(since: float) -> str | None:
    """Newest dialin-* room the server created after `since`."""
    async with httpx.AsyncClient(base_url=API, headers=daily_headers(), timeout=30) as c:
        rooms = (await c.get("/rooms", params={"limit": 20})).json().get("data", [])
    for room in rooms:
        created = calendar.timegm(time.strptime(room["created_at"][:19], "%Y-%m-%dT%H:%M:%S"))
        if room["name"].startswith("dialin-") and created >= int(since) - 1:
            return room["name"]
    return None


async def bot_join_time(room_name: str) -> int | None:
    """Bot's join_time (epoch, whole seconds) in the call room, from Daily's meetings API."""
    async with httpx.AsyncClient(base_url=API, headers=daily_headers(), timeout=30) as c:
        meetings = (await c.get("/meetings", params={"room": room_name})).json().get("data", [])
    for m in meetings:
        for p in m.get("participants", []):
            if p.get("user_name") in BOT_NAMES:
                return p.get("join_time")
    return None


async def greeting_heard_at(chunks: list[tuple[float, bytes]], sample_rate: int) -> float | None:
    """Epoch time of the first greeting word in the caller's recording (Deepgram)."""
    from harness.audio import write_wav

    if not chunks or not os.getenv("DEEPGRAM_API_KEY"):
        return None
    t0 = chunks[0][0]
    path = Path(os.getenv("DIALIN_TEST_WAV", "/tmp/dialin_call_caller.wav"))
    write_wav(path, b"".join(c for _, c in chunks), sample_rate)
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            "https://api.deepgram.com/v1/listen",
            params={"model": "nova-3", "smart_format": "false"},
            headers={
                "Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}",
                "Content-Type": "audio/wav",
            },
            content=path.read_bytes(),
        )
    r.raise_for_status()
    alt = r.json()["results"]["channels"][0]["alternatives"][0]
    print(f"caller heard: {alt['transcript'][:160]!r}")
    for w in alt.get("words", []):
        if w["word"].lower().strip(".,") in GREETING_WORDS:
            return t0 + w["start"]
    return None


class CallerRoom:
    """A throwaway Daily room whose owner dials the number and records the call leg."""

    def __init__(self):
        self.room: dict | None = None
        self.participant = None

    async def __aenter__(self):
        from harness.participant import HarnessParticipant

        async with httpx.AsyncClient(base_url=API, headers=daily_headers(), timeout=30) as c:
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

    def chunks(self) -> list[tuple[float, bytes]]:
        return list(self.participant.recording_for(CALLER_NAME).chunks)

    async def __aexit__(self, *exc):
        if self.participant is not None:
            await self.participant.leave()  # hangs up the dial-out leg
        if self.room:
            async with httpx.AsyncClient(base_url=API, headers=daily_headers(), timeout=30) as c:
                await c.delete(f"/rooms/{self.room['name']}")


@pytest.fixture
def keys_present():
    missing = [k for k in ("DAILY_API_KEY", "DAILY_PHONE_NUMBER") if not os.getenv(k)]
    if missing:
        pytest.skip(f"missing env: {', '.join(missing)}")


async def test_live_phone_call_hears_greeting_within_5s_of_bot_join(keys_present):
    sip_uri = os.getenv("DIALIN_TEST_SIP_URI")
    pstn = os.getenv("DIALIN_TEST_PSTN") == "1"
    wait_secs = float(os.getenv("DIALIN_TEST_WAIT_SECS", "0"))
    log_path = os.getenv("DIALIN_TEST_SERVER_LOG")
    log = Path(log_path) if log_path else None
    listen_secs = float(os.getenv("DIALIN_TEST_LISTEN_SECS", "45"))
    since = time.time()
    sid, events, heard_at, room_name = None, {}, None, None

    if not (sip_uri or pstn):
        if not (wait_secs and log):
            pytest.skip("set DIALIN_TEST_SIP_URI, DIALIN_TEST_PSTN=1, or WAIT_SECS + SERVER_LOG")
        print(f"\ncall the number ending {os.environ['DAILY_PHONE_NUMBER'][-4:]} now")
        sid, events = await wait_for_timeline(log, since, wait_secs)
        assert sid, f"no complete call timeline in the server log: {events}"
    else:
        async with CallerRoom() as caller:
            if sip_uri:
                target = {"sipUri": sip_uri}
            else:
                target = {"phoneNumber": os.environ["DAILY_PHONE_NUMBER"]}
            t_dial = await caller.dial(target)
            print(f"\ndialed via {'SIP' if sip_uri else 'PSTN'} at {t_dial:.3f}")
            info = await caller.participant.wait_for_participant(CALLER_NAME, timeout=60)
            print(f"call leg answered (hold) after {info.joined_at - t_dial:.2f}s")
            if log:
                sid, events = await wait_for_timeline(log, since, listen_secs)
                await asyncio.sleep(4)  # let the greeting play
            else:
                await asyncio.sleep(listen_secs)
            heard_at = await greeting_heard_at(caller.chunks(), caller.participant.sample_rate)
        room_name = await find_call_room(since)

    if room_name:
        joined = await bot_join_time(room_name)
        print(f"call room {room_name}; bot join_time (Daily meetings API) = {joined}")
        if heard_at is not None:
            print(
                f"caller heard the greeting at {heard_at:.3f} ({heard_at - since:.2f}s after dial)"
            )
            if joined:
                print(
                    f"greeting heard - bot join_time = {heard_at - joined:.2f}s "
                    "(join_time is whole seconds)"
                )
    if heard_at is None and not events:
        pytest.fail("the caller never heard the greeting")

    if not log:
        pytest.skip("caller-side only: pass DIALIN_TEST_SERVER_LOG to assert the 2 s budget")
    assert sid, f"no complete call timeline in the server log: {events}"
    print(f"session {sid}\n{format_timeline(events)}")
    latency = events["greeting_first_audio"] - events["bot_joined"]
    print(f"greeting first audio - bot joined    = {latency:.3f}s (budget {GREETING_BUDGET_SECS}s)")
    caller_wait = events["greeting_first_audio"] - events["caller_joined"]
    print(f"greeting first audio - caller joined = {caller_wait:.3f}s")
    if heard_at is not None:
        print(f"caller heard greeting - bot joined   = {heard_at - events['bot_joined']:.3f}s")
    assert latency < GREETING_BUDGET_SECS
