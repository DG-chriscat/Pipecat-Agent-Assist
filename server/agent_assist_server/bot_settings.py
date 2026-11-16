"""Bot settings (P5-5, PRD AP-20, IN-11): what the bot says and how it sounds.

One settings row: TTS model and voice, greeting, intake pack, identity read back, hold
message and interval, and the STT model for intake and for assist (P5-7, IN-14).
Defaults live here in code; no row means defaults, and Reset
deletes the row. Every save and reset is logged on `settings_events` with the login
session that made it and the fields that changed (from and to).

The server reads the settings when it hands a call to a bot (room assignment, see
bot_launcher.launch_in_background), so a call in progress keeps the settings it
started with and a change applies to the next call.

Validation (`validate`):
- tts_model is aura-2 or flux, and tts_voice is in that model's voice catalog
  (voice_catalog.py: the Deepgram models API for Aura-2, the documented list for Flux).
- The greeting keeps the IN-2 disclosure: it says the caller is talking to an AI and
  that a person will join (`disclosure_problems`).
- Text lengths and the hold interval are bounded; the hold message may use one
  placeholder, {place}, filled with the caller's place in line.
- stt_intake_model is in STT_INTAKE_MODELS and stt_assist_model in STT_ASSIST_MODELS.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import re
import string
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

PACKS_DIR = Path(__file__).resolve().parents[2] / "packs"
PACK_ENV = "INTAKE_PACK"
HOLD_INTERVAL_ENV = "HOLD_UPDATE_SECS"
PERSIST_ENV = "SETTINGS_PERSIST"
SCHEMA_ENV = "DATABASE_SCHEMA"

TTS_MODELS = ("aura-2", "flux")
# IN-11: Flux TTS is the default because it read member IDs, dates, amounts, and claim
# numbers at least as well as Aura-2: Flux Alexis 80/80 entities, Aura-2 Thalia 79/80
# (docs/spikes/tts-flux-vs-aura.md). Aura-2 stays selectable.
DEFAULT_TTS_MODEL = "flux"
DEFAULT_TTS_VOICE = "flux-alexis-en"
# IN-14: speech to text per side. Intake (the voice bot): Nova-3 general by default,
# Flux selectable (Flux owns turn detection). Nova-3 stays the default because Flux
# intake regressed identity on the hard name (docs/spikes/stt-flux-intake.md). Assist
# (listen only, per person track): Nova-3 general by default; Nova-3 Medical or Flux
# selectable. No Flux medical model. Same lists as agent_assist_bot.stt.
STT_INTAKE_MODELS = ("nova-3-general", "flux-general-en")
STT_ASSIST_MODELS = ("nova-3-general", "nova-3-medical", "flux-general-en")
DEFAULT_STT_INTAKE_MODEL = "nova-3-general"
DEFAULT_STT_ASSIST_MODEL = "nova-3-general"
DEFAULT_PACK = "claims_status"
# PRD IN-2, same text as agent_assist_bot.intake.GREETING (test_bot_settings_bot.py).
DEFAULT_GREETING = (
    "Hi, thanks for calling. You're speaking with Deepgram Vitals, an AI assistant. "
    "I'll ask a few quick questions, and then a person will join the call to help you."
)
PLACE_FIELD = "place"
# Today's hold line (agent_assist_bot.hold.hold_line) with the place as a placeholder.
DEFAULT_HOLD_TEXT = "Thanks for waiting. {place} A person will be with you shortly."
DEFAULT_HOLD_SECS = 60

MAX_GREETING = 400
MAX_HOLD_TEXT = 300
MIN_HOLD_SECS = 5
MAX_HOLD_SECS = 600

FIELDS = (
    "tts_model",
    "tts_voice",
    "greeting_text",
    "intake_pack",
    "identity_read_back",
    "hold_update_text",
    "hold_update_secs",
    "stt_intake_model",
    "stt_assist_model",
)

NEEDS_AI = "Say that the caller is talking to an AI assistant."
NEEDS_PERSON = "Say that a person will join the call."

_AI = re.compile(r"\b(ai|a\.i\.|artificial intelligence|virtual assistant)\b", re.IGNORECASE)
_PERSON = re.compile(
    r"\b(person|human|agent|representative|someone|team member)\b[^.?!]*?"
    r"\b(will|'ll|is going to|can)\b[^.?!]*?\b(join|be with you|help|connect|take)",
    re.IGNORECASE,
)


def disclosure_problems(greeting: str) -> list[str]:
    """What the greeting is missing from the IN-2 disclosure (empty when it is fine)."""
    problems = []
    if not _AI.search(greeting or ""):
        problems.append(NEEDS_AI)
    if not _PERSON.search(greeting or ""):
        problems.append(NEEDS_PERSON)
    return problems


def _env_hold_secs(env: Mapping[str, str]) -> int:
    raw = (env.get(HOLD_INTERVAL_ENV) or "").strip()
    try:
        secs = int(float(raw))
    except ValueError:
        return DEFAULT_HOLD_SECS
    return secs if secs > 0 else DEFAULT_HOLD_SECS


def defaults(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The settings a call gets when nobody has saved any. INTAKE_PACK and
    HOLD_UPDATE_SECS (the env knobs from before the Settings page) still set the
    default pack and hold interval."""
    env = os.environ if env is None else env
    return {
        "tts_model": DEFAULT_TTS_MODEL,
        "tts_voice": DEFAULT_TTS_VOICE,
        "greeting_text": DEFAULT_GREETING,
        "intake_pack": (env.get(PACK_ENV) or "").strip() or DEFAULT_PACK,
        "identity_read_back": True,
        "hold_update_text": DEFAULT_HOLD_TEXT,
        "hold_update_secs": _env_hold_secs(env),
        "stt_intake_model": DEFAULT_STT_INTAKE_MODEL,
        "stt_assist_model": DEFAULT_STT_ASSIST_MODEL,
    }


def pack_ids(packs_dir: Path = PACKS_DIR) -> list[str]:
    return sorted(p.stem for p in packs_dir.glob("*.yaml"))


def place_line(position: int | None) -> str:
    if position == 1:
        return "You're next in line."
    if position:
        return f"You're number {position} in line."
    return "You're still in line."


def render_hold_line(template: str, position: int | None) -> str:
    return template.replace("{" + PLACE_FIELD + "}", place_line(position))


def _placeholders(text: str) -> list[str | None]:
    """Field names in a format string; raises ValueError on a stray brace."""
    return [name for _, name, _, _ in string.Formatter().parse(text) if name is not None]


# ---- voice catalog ------------------------------------------------------------------


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    accent: str = ""
    gender: str = ""


class VoiceCatalog(Protocol):
    async def voices(self, model: str) -> list[Voice]: ...


class StaticVoiceCatalog:
    def __init__(self, voices: Mapping[str, list[Voice]]):
        self._voices = {k: list(v) for k, v in voices.items()}

    async def voices(self, model: str) -> list[Voice]:
        return list(self._voices.get(model, []))


# ---- validation ---------------------------------------------------------------------


def _text(values: Mapping[str, Any], key: str, limit: int, errors: dict[str, str]) -> str:
    v = values.get(key)
    if not isinstance(v, str) or not v.strip():
        errors[key] = "Required."
        return ""
    if len(v) > limit:
        errors[key] = f"Keep it to {limit} characters or fewer."
    return v


async def validate(
    values: Mapping[str, Any], *, catalog: VoiceCatalog, packs: Collection[str]
) -> dict[str, str]:
    """Field name -> what is wrong with it. Empty when the settings can be saved."""
    errors: dict[str, str] = {}
    for key in values:
        if key not in FIELDS:
            errors[key] = "Unknown setting."
    for key in FIELDS:
        if key not in values:
            errors[key] = "Required."

    model = values.get("tts_model")
    if "tts_model" in values and model not in TTS_MODELS:
        errors["tts_model"] = f"Pick one of {', '.join(TTS_MODELS)}."
    voice = values.get("tts_voice")
    if "tts_voice" in values and model in TTS_MODELS:
        ids = {v.id for v in await catalog.voices(model)}
        if voice not in ids:
            errors["tts_voice"] = f"Pick a {model} voice from the list."

    if "greeting_text" in values:
        greeting = _text(values, "greeting_text", MAX_GREETING, errors)
        if greeting and "greeting_text" not in errors:
            if problems := disclosure_problems(greeting):
                errors["greeting_text"] = " ".join(problems)

    if "hold_update_text" in values:
        hold = _text(values, "hold_update_text", MAX_HOLD_TEXT, errors)
        if hold and "hold_update_text" not in errors:
            try:
                names = _placeholders(hold)
            except ValueError:
                names = [None]
            if any(n != PLACE_FIELD for n in names):
                errors["hold_update_text"] = (
                    "The only placeholder allowed is {place} (the caller's place in line)."
                )

    secs = values.get("hold_update_secs")
    if "hold_update_secs" in values and (
        not isinstance(secs, int)
        or isinstance(secs, bool)
        or not MIN_HOLD_SECS <= secs <= MAX_HOLD_SECS
    ):
        errors["hold_update_secs"] = f"Whole seconds from {MIN_HOLD_SECS} to {MAX_HOLD_SECS}."

    if "intake_pack" in values and values.get("intake_pack") not in packs:
        errors["intake_pack"] = "Pick a pack from the list."
    if "identity_read_back" in values and not isinstance(values.get("identity_read_back"), bool):
        errors["identity_read_back"] = "On or off."
    for key, allowed in (
        ("stt_intake_model", STT_INTAKE_MODELS),
        ("stt_assist_model", STT_ASSIST_MODELS),
    ):
        if key in values and values.get(key) not in allowed:
            errors[key] = f"Pick one of {', '.join(allowed)}."
    return errors


def changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        k: {"from": before.get(k), "to": after.get(k)}
        for k in FIELDS
        if before.get(k) != after.get(k)
    }


# ---- stores -------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    values: dict[str, Any]
    updated_at: dt.datetime | None = None
    updated_by: str | None = None

    @property
    def is_default(self) -> bool:
        return self.updated_at is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": dict(self.values),
            "is_default": self.is_default,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass(frozen=True)
class SettingsEvent:
    kind: str  # save | reset
    session_id: str
    agent: str | None
    changes: dict[str, dict[str, Any]]
    at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))


class SettingsStore(Protocol):
    async def get(self) -> Settings: ...

    async def save(
        self, values: Mapping[str, Any], *, session_id: str, agent: str | None
    ) -> Settings: ...

    async def reset(self, *, session_id: str, agent: str | None) -> Settings: ...


def _merged(defaults_now: Mapping[str, Any], row: Mapping[str, Any] | None) -> dict[str, Any]:
    out = dict(defaults_now)
    for k, v in (row or {}).items():
        if k in FIELDS and v is not None:
            out[k] = v
    return out


class MemorySettingsStore:
    """In process (local dev and tests, SETTINGS_PERSIST unset)."""

    def __init__(self, defaults: Callable[[], dict[str, Any]] = defaults):
        self._defaults = defaults
        self._row: dict[str, Any] | None = None
        self._updated_at: dt.datetime | None = None
        self._updated_by: str | None = None
        self.events: list[SettingsEvent] = []

    async def get(self) -> Settings:
        return Settings(_merged(self._defaults(), self._row), self._updated_at, self._updated_by)

    async def save(
        self, values: Mapping[str, Any], *, session_id: str, agent: str | None
    ) -> Settings:
        before = (await self.get()).values
        self._row = {k: values[k] for k in FIELDS}
        self._updated_at = dt.datetime.now(dt.UTC)
        self._updated_by = session_id
        after = await self.get()
        self.events.append(SettingsEvent("save", session_id, agent, changes(before, after.values)))
        return after

    async def reset(self, *, session_id: str, agent: str | None) -> Settings:
        before = (await self.get()).values
        self._row = None
        self._updated_at = self._updated_by = None
        after = await self.get()
        self.events.append(SettingsEvent("reset", session_id, agent, changes(before, after.values)))
        return after


COLUMNS = ", ".join(FIELDS)
SELECT_SQL = f"SELECT {COLUMNS}, updated_at, updated_by FROM bot_settings WHERE id = 1"
UPSERT_SQL = f"""
INSERT INTO bot_settings (id, {COLUMNS}, updated_at, updated_by)
VALUES (1, {", ".join(f"%({k})s" for k in FIELDS)}, now(), %(session_id)s)
ON CONFLICT (id) DO UPDATE SET
    {", ".join(f"{k} = EXCLUDED.{k}" for k in FIELDS)},
    updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by
RETURNING updated_at
"""
DELETE_SQL = "DELETE FROM bot_settings WHERE id = 1"
EVENT_SQL = """
INSERT INTO settings_events (kind, session_id, agent, changes)
VALUES (%(kind)s, %(session_id)s, %(agent)s, %(changes)s)
"""


class PostgresSettingsStore:
    """`bot_settings` (one row, id 1) and `settings_events` (db/migrations/0007).

    The server is one process (fly.toml), so the row is cached after the first read and
    every save goes through here: launching a call does not wait on the database.
    """

    def __init__(
        self,
        *,
        schema: str | None = None,
        url: str | None = None,
        defaults: Callable[[], dict[str, Any]] = defaults,
    ):
        self._schema = schema
        self._url = url
        self._defaults = defaults
        self._cached: tuple[dict[str, Any] | None, dt.datetime | None, str | None] | None = None
        self._lock = asyncio.Lock()

    async def _connect(self):
        from agent_assist_server.db import connect

        return await connect(schema=self._schema, url=self._url)

    def _settings(self) -> Settings:
        assert self._cached is not None
        row, at, by = self._cached
        return Settings(_merged(self._defaults(), row), at, by)

    async def get(self) -> Settings:
        if self._cached is None:
            async with self._lock:
                if self._cached is None:
                    async with await self._connect() as conn:
                        cur = await conn.execute(SELECT_SQL)
                        r = await cur.fetchone()
                    if r is None:
                        self._cached = (None, None, None)
                    else:
                        n = len(FIELDS)
                        self._cached = (dict(zip(FIELDS, r[:n], strict=True)), r[n], r[n + 1])
        return self._settings()

    async def _write(self, kind: str, values, session_id: str, agent: str | None) -> Settings:
        from psycopg.types.json import Jsonb

        before = (await self.get()).values
        async with self._lock:
            async with await self._connect() as conn, conn.transaction():
                if values is None:
                    await conn.execute(DELETE_SQL)
                    cached = (None, None, None)
                else:
                    row = {k: values[k] for k in FIELDS}
                    cur = await conn.execute(UPSERT_SQL, {**row, "session_id": session_id})
                    (at,) = await cur.fetchone()
                    cached = (row, at, session_id)
                after = Settings(_merged(self._defaults(), cached[0]), cached[1], cached[2])
                await conn.execute(
                    EVENT_SQL,
                    {
                        "kind": kind,
                        "session_id": session_id,
                        "agent": agent,
                        "changes": Jsonb(changes(before, after.values)),
                    },
                )
            self._cached = cached
        return self._settings()

    async def save(
        self, values: Mapping[str, Any], *, session_id: str, agent: str | None
    ) -> Settings:
        return await self._write("save", values, session_id, agent)

    async def reset(self, *, session_id: str, agent: str | None) -> Settings:
        return await self._write("reset", None, session_id, agent)

    async def events(self, limit: int = 50) -> list[SettingsEvent]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT kind, session_id, agent, changes, created_at FROM settings_events "
                "ORDER BY event_id DESC LIMIT %s",
                (limit,),
            )
            rows = await cur.fetchall()
        return [SettingsEvent(k, s, a, c, at) for k, s, a, c, at in rows]


def store_from_env(env: Mapping[str, str] | None = None) -> SettingsStore:
    env = os.environ if env is None else env
    if env.get(PERSIST_ENV) == "1":
        return PostgresSettingsStore(schema=(env.get(SCHEMA_ENV) or "").strip() or None)
    return MemorySettingsStore()
