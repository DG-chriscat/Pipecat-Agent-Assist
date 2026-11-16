"""Settings page API (P5-5, PRD AP-20, IN-11). Behind the shared login like every route.

    GET  /settings          -> {values, defaults, is_default, updated_at, packs, limits}
    PUT  /settings          {values: {...}, agent?: name}  -> same shape; 422 with
                            {detail: {errors: {field: message}}} when a field is wrong
    POST /settings/reset    {agent?: name}  -> defaults
    GET  /settings/voices   -> {"aura-2": [voice], "flux": [voice]}
    POST /settings/preview  {tts_model, tts_voice, text?} -> audio/mpeg (a sample line)

Every save and reset is logged with the login session (a hash, see auth.session_id) and
the agent name the portal sends. Without a login (local dev) the session is "local".

Calls: `CallSettingsBook.for_call` reads the settings when the server hands a call to a
bot (bot_launcher.launch_in_background) and keeps that copy per session, so the call
keeps what it started with (the wrap up reason codes come from the call's pack).
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

import yaml
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from agent_assist_server import auth
from agent_assist_server import bot_settings as bs
from agent_assist_server import voice_catalog as vc

logger = logging.getLogger(__name__)

LOCAL_SESSION = "local"
PREVIEW_LINE = (
    "Hi, thanks for calling. I have your member ID as E V M, one zero zero, two three one. "
    "Is that right?"
)
MAX_PREVIEW = bs.MAX_GREETING
# Calls whose settings are kept (oldest dropped first).
MAX_CALLS_KEPT = 500

router = APIRouter()


def get_settings_store(app: FastAPI) -> bs.SettingsStore:
    store = getattr(app.state, "settings_store", None)
    if store is None:
        store = bs.store_from_env()
        app.state.settings_store = store
    return store


def get_voice_catalog(app: FastAPI) -> bs.VoiceCatalog:
    catalog = getattr(app.state, "voice_catalog", None)
    if catalog is None:
        catalog = vc.DeepgramVoiceCatalog()
        app.state.voice_catalog = catalog
    return catalog


class CallSettingsBook:
    """The settings each call was launched with."""

    def __init__(self, store: bs.SettingsStore):
        self._store = store
        self._by_session: OrderedDict[str, dict[str, Any]] = OrderedDict()

    async def for_call(self, session_id: str) -> dict[str, Any] | None:
        """Read the current settings for a call being assigned. None when they cannot
        be read: the bot then uses its own defaults, so the call still goes ahead."""
        try:
            values = dict((await self._store.get()).values)
        except Exception as e:  # noqa: BLE001 - never block a call on settings
            logger.error("settings read failed for %s: %s", session_id, type(e).__name__)
            return None
        self._by_session[session_id] = values
        while len(self._by_session) > MAX_CALLS_KEPT:
            self._by_session.popitem(last=False)
        return values

    def for_session(self, session_id: str) -> dict[str, Any] | None:
        return self._by_session.get(session_id)


def get_call_settings(app: FastAPI) -> CallSettingsBook:
    book = getattr(app.state, "call_settings", None)
    if book is None:
        book = CallSettingsBook(get_settings_store(app))
        app.state.call_settings = book
    return book


def pack_summaries() -> list[dict[str, Any]]:
    """Pack id, name, and intake questions (read only on the page for now)."""
    out = []
    for pid in bs.pack_ids():
        data = yaml.safe_load((bs.PACKS_DIR / f"{pid}.yaml").read_text(encoding="utf-8")) or {}
        out.append(
            {
                "id": pid,
                "name": str(data.get("name") or pid),
                "questions": [
                    {"key": str(q.get("key")), "ask": str(q.get("ask"))}
                    for q in data.get("questions") or []
                ],
            }
        )
    return out


def _session(request: Request) -> str:
    config = getattr(request.app.state, "auth", None)
    if config is None:
        return LOCAL_SESSION
    return config.session_id(request.cookies.get(auth.COOKIE_NAME)) or LOCAL_SESSION


def _body(settings: bs.Settings) -> dict[str, Any]:
    return {
        **settings.to_dict(),
        "defaults": bs.defaults(),
        "packs": pack_summaries(),
        "limits": {
            "greeting_text": bs.MAX_GREETING,
            "hold_update_text": bs.MAX_HOLD_TEXT,
            "hold_update_secs": [bs.MIN_HOLD_SECS, bs.MAX_HOLD_SECS],
        },
    }


class SaveRequest(BaseModel):
    values: dict[str, Any]
    agent: str | None = Field(default=None, max_length=80)


class ResetRequest(BaseModel):
    agent: str | None = Field(default=None, max_length=80)


class PreviewRequest(BaseModel):
    tts_model: str = Field(max_length=16)
    tts_voice: str = Field(max_length=64)
    text: str | None = Field(default=None, max_length=MAX_PREVIEW)


@router.get("/settings")
async def read_settings(request: Request) -> dict[str, Any]:
    return _body(await get_settings_store(request.app).get())


@router.put("/settings")
async def save_settings(body: SaveRequest, request: Request) -> dict[str, Any]:
    errors = await bs.validate(
        body.values, catalog=get_voice_catalog(request.app), packs=bs.pack_ids()
    )
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    session = _session(request)
    saved = await get_settings_store(request.app).save(
        body.values, session_id=session, agent=body.agent
    )
    logger.info("settings saved session=%s agent=%s", session, body.agent or "-")
    return _body(saved)


@router.post("/settings/reset")
async def reset_settings(request: Request, body: ResetRequest | None = None) -> dict[str, Any]:
    session = _session(request)
    agent = body.agent if body else None
    reset = await get_settings_store(request.app).reset(session_id=session, agent=agent)
    logger.info("settings reset session=%s agent=%s", session, agent or "-")
    return _body(reset)


@router.get("/settings/voices")
async def list_voices(request: Request) -> dict[str, list[dict[str, str]]]:
    catalog = get_voice_catalog(request.app)
    return {
        model: [v.__dict__.copy() for v in await catalog.voices(model)] for model in bs.TTS_MODELS
    }


@router.post("/settings/preview")
async def preview(body: PreviewRequest, request: Request) -> Response:
    voices = await get_voice_catalog(request.app).voices(body.tts_model)
    if body.tts_voice not in {v.id for v in voices}:
        raise HTTPException(status_code=422, detail="Pick a voice from the list.")
    text = (body.text or "").strip() or PREVIEW_LINE
    try:
        audio = await vc.synthesize(body.tts_model, body.tts_voice, text)
    except vc.SpeakError as e:
        logger.error("settings preview failed: %s", e)
        raise HTTPException(status_code=502, detail="The preview could not be made.") from None
    return Response(content=audio, media_type="audio/mpeg")
