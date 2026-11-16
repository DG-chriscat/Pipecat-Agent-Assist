"""Call records and identity checks for the intake bot (PRD IN-5, IN-6).

The bot writes its own `calls` row (schema in db/migrations/0001_init.sql):
- `create_call` inserts the row for this session (status `intake`).
- `save_intake` merges answers into `calls.intake` jsonb the moment they are captured.
- `set_identity` stores `identity_result` and the matched `member_id`.
- `add_flag` appends to `calls.flags` (no duplicates).
- `mark_ready_for_queue` sets status `ready_for_queue` and stores the intake summary.
- `add_transcript_line` saves one final transcript line (P3-4) for wrap up; caller
  lines carry their tone reading (P4-5, IN-10): `tone` is the reader's emotion label,
  `tone_reading` the whole reading (labels, sarcasm, mood step).
- `add_assist_event` logs what the assist showed and what the agent did (P4-3, DC-9).

`verify_identity` matches name + date of birth + member ID against `members`.
Two seed members share a name, so the name alone never identifies anyone.

PostgresCallStore talks plain Postgres through DATABASE_URL (ADR 0002). The URL is a
secret: it is never logged. MemoryCallStore is the same contract in memory, for unit
tests and for running the bot without a database.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from loguru import logger

DATABASE_URL_ENV = "DATABASE_URL"
# Optional: run against another schema than the server default (live tests use a
# throwaway schema so they never write to public).
DATABASE_SCHEMA_ENV = "DATABASE_SCHEMA"

# close_match (IN-6, Chris Nov 16): member ID and DOB exact, name close.
# incomplete: a detail was still missing after the one correction (IN-6 cap).
IdentityResult = Literal["verified", "close_match", "not_verified", "not_found", "incomplete"]

FLAG_NOT_VERIFIED = "identity_not_verified"
FLAG_NOT_FOUND = "identity_not_found"
FLAG_CLOSE_MATCH = "identity_close_match"
FLAG_INCOMPLETE = "identity_incomplete"
FLAG_ASKED_FOR_PERSON = "asked_for_person"
FLAG_EMERGENCY = "emergency"
# Intake made no progress (watchdog in intake.py); handed off with what was captured.
FLAG_INTAKE_STALLED = "intake_stalled"


@dataclass(frozen=True)
class Member:
    member_id: str
    first_name: str
    last_name: str
    dob: dt.date


IdentityDetail = Literal["full_name", "dob", "member_id"]


@dataclass(frozen=True)
class IdentityCheck:
    result: IdentityResult
    member_id: str | None = None
    # Which of the three details matched that member (AP-18 "Identity n of 3").
    checks: tuple[IdentityDetail, ...] = ()
    # The member's name on file, when a member was matched by ID (the agent's
    # verification step shows heard vs on file, IN-13). Never the DOB.
    on_file_name: str | None = None


# Close name (IN-6 v2). One part (first or last name) must match exactly and the other
# must be close: normalized Levenshtein similarity >= 0.5 and at most 3 edits. Examples
# from the identity spike (docs/spikes/identity.md): Nguyen/Yuen 0.50 (close),
# Nguyen/Hengguyen 0.67, Aisha/Ayesha 0.67, Okafor/Lokafor 0.86 (close);
# Okafor/Ford 0.33, Nguyen/Wayne 0.17, Nguyen/Win 0.17 (too far). Only used when the
# member ID and DOB already match exactly, and the agent still confirms (IN-13).
CLOSE_NAME_MIN_SIMILARITY = 0.5
CLOSE_NAME_MAX_EDITS = 3


def _edits(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def name_part_close(on_file: str, heard: str) -> bool:
    """True when `heard` is close to `on_file` (one name part, lower case letters)."""
    a, b = on_file.lower(), heard.lower()
    if not a or not b:
        return False
    d = _edits(a, b)
    return d <= CLOSE_NAME_MAX_EDITS and 1 - d / max(len(a), len(b)) >= CLOSE_NAME_MIN_SIMILARITY


def _name_close(member: Member, full_name: str) -> bool:
    tokens = _name_tokens(full_name)
    if len(tokens) < 2:
        return False
    first, last = member.first_name.lower(), member.last_name.lower()
    hf, hl = tokens[0], tokens[-1]
    if hf == first:
        return name_part_close(last, hl)
    if hl == last:
        return name_part_close(first, hf)
    return False


def normalize_member_id(raw: str) -> str:
    """Upper case, drop spaces, dashes, and dots ("evm 100-231" -> "EVM100231")."""
    return re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()


def _name_tokens(raw: str) -> list[str]:
    return re.sub(r"[^a-z\s]", " ", (raw or "").lower()).split()


def _name_matches(member: Member, full_name: str) -> bool:
    tokens = _name_tokens(full_name)
    if len(tokens) < 2:
        return False
    first, last = member.first_name.lower(), member.last_name.lower()
    return tokens[0] == first and tokens[-1] == last


def _parse_dob(raw: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat((raw or "").strip())
    except ValueError:
        return None


def match_identity(
    candidates: Iterable[Member],
    by_id: Member | None,
    full_name: str,
    dob: str,
    member_id: str,
) -> IdentityCheck:
    """Decide verified / close_match / not_verified / not_found.

    - Member ID found and name + DOB match it: verified.
    - Member ID found, DOB exact, name close (`_name_close`): close_match.
    - Member ID found but name or DOB differ: not_verified (that member, flagged).
    - Member ID unknown, but name + DOB match exactly one member: not_verified.
    - Otherwise: not_found.

    `candidates` are members that might match by name + DOB; `by_id` is the member
    whose ID the caller gave, if any.
    """
    birth = _parse_dob(dob)
    if by_id is not None:
        name_ok, dob_ok = _name_matches(by_id, full_name), birth == by_id.dob
        checks: list[IdentityDetail] = []
        if name_ok:
            checks.append("full_name")
        if dob_ok:
            checks.append("dob")
        checks.append("member_id")
        if name_ok and dob_ok:
            result = "verified"
        elif dob_ok and _name_close(by_id, full_name):
            result = "close_match"
        else:
            result = "not_verified"
        on_file = f"{by_id.first_name} {by_id.last_name}"
        return IdentityCheck(result, by_id.member_id, tuple(checks), on_file)
    hits = [m for m in candidates if birth == m.dob and _name_matches(m, full_name)]
    if len(hits) == 1:
        return IdentityCheck("not_verified", hits[0].member_id, ("full_name", "dob"))
    return IdentityCheck("not_found", None)


class CallStore(Protocol):
    async def create_call(self, session_id: str) -> str: ...
    async def save_intake(self, call_id: str, fields: dict[str, Any]) -> None: ...
    async def set_identity(
        self, call_id: str, result: IdentityResult, member_id: str | None
    ) -> None: ...
    async def add_flag(self, call_id: str, flag: str) -> None: ...
    async def mark_ready_for_queue(self, call_id: str, summary: str) -> None: ...
    async def add_transcript_line(
        self,
        call_id: str,
        speaker_role: str,
        text: str,
        ts: float,
        tone: dict[str, Any] | None = None,
    ) -> None: ...
    async def add_assist_event(
        self, call_id: str, kind: str, card_id: str | None, data: dict[str, Any]
    ) -> None: ...
    async def verify_identity(self, full_name: str, dob: str, member_id: str) -> IdentityCheck: ...
    async def close(self) -> None: ...


@dataclass
class CallRecord:
    call_id: str
    session_id: str
    status: str = "intake"
    member_id: str | None = None
    intake: dict[str, Any] = field(default_factory=dict)
    identity_result: str | None = None
    flags: list[str] = field(default_factory=list)
    transcript: list[tuple[str, str, float]] = field(default_factory=list)
    # Tone reading per transcript line, same order (P4-5, IN-10); None when unread.
    tones: list[dict[str, Any] | None] = field(default_factory=list)
    assist_events: list[tuple[str, str | None, dict[str, Any]]] = field(default_factory=list)


class MemoryCallStore:
    """In-memory CallStore: unit tests, and the bot when DATABASE_URL is unset."""

    def __init__(self, members: Iterable[Member] = ()):
        self.members = {m.member_id: m for m in members}
        self.calls: dict[str, CallRecord] = {}
        self.writes: list[tuple[str, Any]] = []

    async def create_call(self, session_id: str) -> str:
        for rec in self.calls.values():
            if rec.session_id == session_id:
                return rec.call_id
        call_id = str(uuid.uuid4())
        self.calls[call_id] = CallRecord(call_id, session_id)
        return call_id

    async def save_intake(self, call_id: str, fields: dict[str, Any]) -> None:
        self.calls[call_id].intake.update(fields)
        self.writes.append(("intake", dict(fields)))

    async def set_identity(
        self, call_id: str, result: IdentityResult, member_id: str | None
    ) -> None:
        rec = self.calls[call_id]
        rec.identity_result, rec.member_id = result, member_id
        self.writes.append(("identity", result))

    async def add_flag(self, call_id: str, flag: str) -> None:
        rec = self.calls[call_id]
        if flag not in rec.flags:
            rec.flags.append(flag)
        self.writes.append(("flag", flag))

    async def mark_ready_for_queue(self, call_id: str, summary: str) -> None:
        rec = self.calls[call_id]
        rec.status = "ready_for_queue"
        rec.intake["summary"] = summary
        self.writes.append(("ready", summary))

    async def add_transcript_line(
        self,
        call_id: str,
        speaker_role: str,
        text: str,
        ts: float,
        tone: dict[str, Any] | None = None,
    ) -> None:
        self.calls[call_id].transcript.append((speaker_role, text, ts))
        self.calls[call_id].tones.append(dict(tone) if tone else None)
        self.writes.append(("transcript", speaker_role))

    async def add_assist_event(
        self, call_id: str, kind: str, card_id: str | None, data: dict[str, Any]
    ) -> None:
        self.calls[call_id].assist_events.append((kind, card_id, dict(data)))
        self.writes.append(("assist", kind))

    async def verify_identity(self, full_name: str, dob: str, member_id: str) -> IdentityCheck:
        by_id = self.members.get(normalize_member_id(member_id))
        return match_identity(self.members.values(), by_id, full_name, dob, member_id)

    async def close(self) -> None:
        pass


class PostgresCallStore:
    """CallStore on the mock Postgres DB. One connection per call, reopened if dropped."""

    def __init__(self, url: str, *, schema: str | None = None):
        self._url = url
        self._schema = schema
        self._conn = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> PostgresCallStore | None:
        env = dict(os.environ) if env is None else env
        url = env.get(DATABASE_URL_ENV, "").strip()
        schema = env.get(DATABASE_SCHEMA_ENV, "").strip() or None
        return cls(url, schema=schema) if url else None

    async def _connection(self):
        import psycopg
        from psycopg import sql

        if self._conn is None or self._conn.closed:
            self._conn = await psycopg.AsyncConnection.connect(self._url, autocommit=True)
            if self._schema:
                await self._conn.execute(
                    sql.SQL("SET search_path TO {}").format(sql.Identifier(self._schema))
                )
        return self._conn

    async def _execute(self, query: str, params: tuple = ()):
        import psycopg

        for attempt in (1, 2):
            conn = await self._connection()
            try:
                return await conn.execute(query, params)
            except psycopg.OperationalError:
                # Pooler dropped an idle connection: reconnect once.
                await self._drop()
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")

    async def _drop(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:  # noqa: BLE001
                pass
        self._conn = None

    async def create_call(self, session_id: str) -> str:
        cur = await self._execute(
            "INSERT INTO calls (session_id) VALUES (%s) "
            "ON CONFLICT (session_id) DO UPDATE SET updated_at = now() "
            "RETURNING call_id",
            (session_id,),
        )
        return str((await cur.fetchone())[0])

    async def save_intake(self, call_id: str, fields: dict[str, Any]) -> None:
        from psycopg.types.json import Jsonb

        await self._execute(
            "UPDATE calls SET intake = intake || %s, updated_at = now() WHERE call_id = %s",
            (Jsonb(fields), call_id),
        )

    async def set_identity(
        self, call_id: str, result: IdentityResult, member_id: str | None
    ) -> None:
        await self._execute(
            "UPDATE calls SET identity_result = %s, member_id = %s, updated_at = now() "
            "WHERE call_id = %s",
            (result, member_id, call_id),
        )

    async def add_flag(self, call_id: str, flag: str) -> None:
        await self._execute(
            "UPDATE calls SET flags = array_append(flags, %s), updated_at = now() "
            "WHERE call_id = %s AND NOT (%s = ANY(flags))",
            (flag, call_id, flag),
        )

    async def mark_ready_for_queue(self, call_id: str, summary: str) -> None:
        from psycopg.types.json import Jsonb

        await self._execute(
            "UPDATE calls SET status = 'ready_for_queue', intake = intake || %s, "
            "updated_at = now() WHERE call_id = %s",
            (Jsonb({"summary": summary}), call_id),
        )

    async def add_transcript_line(
        self,
        call_id: str,
        speaker_role: str,
        text: str,
        ts: float,
        tone: dict[str, Any] | None = None,
    ) -> None:
        from psycopg.types.json import Jsonb

        await self._execute(
            "INSERT INTO transcript_lines (call_id, speaker_role, text, ts, tone, tone_reading) "
            "VALUES (%s, %s, %s, to_timestamp(%s), %s, %s)",
            (
                call_id,
                speaker_role,
                text,
                ts,
                tone.get("emotion") if tone else None,
                Jsonb(tone) if tone else None,
            ),
        )

    async def add_assist_event(
        self, call_id: str, kind: str, card_id: str | None, data: dict[str, Any]
    ) -> None:
        from psycopg.types.json import Jsonb

        await self._execute(
            "INSERT INTO assist_events (call_id, kind, card_id, data) VALUES (%s, %s, %s, %s)",
            (call_id, kind, card_id, Jsonb(data)),
        )

    async def verify_identity(self, full_name: str, dob: str, member_id: str) -> IdentityCheck:
        cols = "member_id, first_name, last_name, dob"
        mid = normalize_member_id(member_id)
        cur = await self._execute(f"SELECT {cols} FROM members WHERE member_id = %s", (mid,))
        row = await cur.fetchone()
        by_id = Member(*row) if row else None
        candidates: list[Member] = []
        if by_id is None:
            tokens = _name_tokens(full_name)
            if len(tokens) >= 2:
                cur = await self._execute(
                    f"SELECT {cols} FROM members "
                    "WHERE lower(last_name) = %s AND lower(first_name) = %s",
                    (tokens[-1], tokens[0]),
                )
                candidates = [Member(*r) for r in await cur.fetchall()]
        check = match_identity(candidates, by_id, full_name, dob, member_id)
        logger.info(f"[intake] identity check: {check.result}")
        return check

    async def close(self) -> None:
        await self._drop()
