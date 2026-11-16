"""Wrap up (P3-5, P5-1) and agent drop (QH-9).

    GET  /calls/{session_id}/wrap-up?agent_id=   -> what the wrap up view shows
    POST /agents/{agent_id}/wrap-up               {session_id, reason_code, notes, summary}
                                                  -> the call, saved; agent available
    POST /queue/calls/{session_id}/wrap-up-draft  (bot: summary + reason code draft, AP-9)
    POST /queue/calls/{session_id}/agent-left     (bot: the agent left the room)

Reason codes come from a use case pack (`wrap_up.reason_codes` in
`packs/<pack>.yaml`). The server reads the YAML itself and does not import the bot
package. The pack is the one the bot's draft names (the assist may have switched
packs during the call), else the active intake pack (`INTAKE_PACK`).

The call summary draft (P5-1, AP-9) is written by the bot when the call ends
(`agent_assist_bot/wrap_up.py`) and posted here. Until it arrives the view says
`draft_pending` for up to DRAFT_WAIT_SECS after the call ended, so the portal can
poll; after that the agent writes the summary by hand.
"""

from __future__ import annotations

import os
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from agent_assist_server.queue_api import get_queue
from agent_assist_server.queue_service import MAX_NOTES, MAX_SUMMARY, QueueError

# server/agent_assist_server/wrap_up_api.py -> repo root / packs
PACKS_DIR = Path(__file__).resolve().parents[2] / "packs"
PACK_ENV = "INTAKE_PACK"
DEFAULT_PACK = "claims_status"
DEFAULT_CODES = [{"code": "other", "label": "Other"}]
_PACK_ID = re.compile(r"^[a-z][a-z0-9_]*$")
# How long after the call ends the view reports the bot's draft as still coming.
DRAFT_WAIT_SECS = 30.0

router = APIRouter()


@lru_cache(maxsize=8)
def _load_codes(path: Path, mtime: float) -> tuple[tuple[str, str], ...]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    codes = ((data.get("wrap_up") or {}).get("reason_codes")) or DEFAULT_CODES
    return tuple((str(c["code"]), str(c["label"])) for c in codes)


def active_pack() -> str:
    return os.environ.get(PACK_ENV, "").strip() or DEFAULT_PACK


def pack_exists(pack: str) -> bool:
    return bool(_PACK_ID.match(pack)) and (PACKS_DIR / f"{pack}.yaml").is_file()


def reason_codes(pack: str | None = None) -> list[dict[str, str]]:
    """A pack's wrap up reason codes, in pack order (default: the active pack)."""
    name = pack or active_pack()
    if not pack_exists(name):
        return list(DEFAULT_CODES)
    path = PACKS_DIR / f"{name}.yaml"
    return [{"code": c, "label": label} for c, label in _load_codes(path, path.stat().st_mtime)]


def _call_pack(app: Any, call: Any) -> str:
    """The pack whose codes this call's wrap up uses: the draft's (the bot names the pack
    it ran), else the pack in the settings the call was assigned with (P5-5), else the
    active one."""
    draft = call.wrap_up_draft or {}
    pack = draft.get("pack_id")
    if isinstance(pack, str) and pack_exists(pack):
        return pack
    return _settings_pack(app, call.session_id)


def _settings_pack(app: Any, session_id: str) -> str:
    from agent_assist_server.settings_api import get_call_settings

    values = get_call_settings(app).for_session(session_id)
    pack = values.get("intake_pack") if values else None
    return pack if isinstance(pack, str) and pack_exists(pack) else active_pack()


def _text(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


class WrapUpRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    reason_code: str = Field(min_length=1, max_length=64)
    notes: str = Field(default="", max_length=MAX_NOTES + 200)
    summary: str = Field(default="", max_length=MAX_SUMMARY + 200)


class DraftRequest(BaseModel):
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY)
    reason_code: str = Field(min_length=1, max_length=64)
    pack_id: str = Field(min_length=1, max_length=64)
    source: Literal["llm", "fallback"]


@router.get("/calls/{session_id}/wrap-up")
async def wrap_up_view(
    session_id: str, request: Request, agent_id: str = Query(min_length=1, max_length=64)
) -> dict[str, Any]:
    svc = get_queue(request.app)
    try:
        call = svc.get_call(session_id)
    except QueueError:
        raise HTTPException(status_code=404, detail="unknown call") from None
    if call.agent_id != agent_id:
        raise HTTPException(status_code=403, detail="only the agent on this call can read it")
    now = time.time()
    end = call.ended_at if call.ended_at is not None else now
    start = call.answered_at if call.answered_at is not None else end
    draft = call.wrap_up_draft or {}
    pack = _call_pack(request.app, call)
    return {
        "session_id": call.session_id,
        "status": call.status.value,
        "ended_by": call.ended_by,
        "caller_name": _text(call.summary.get("full_name")) or "Unknown caller",
        "reason": _text(call.summary.get("reason")) or _text(call.summary.get("summary")),
        "identity_result": call.identity_result,
        "duration_secs": round(max(0.0, end - start), 1),
        "summary_draft": draft.get("summary"),
        "suggested_reason_code": draft.get("reason_code"),
        "draft_pending": not draft and now - end < DRAFT_WAIT_SECS,
        "pack_id": pack,
        "reason_codes": reason_codes(pack),
        "wrap_up": call.wrap_up,
    }


@router.post("/agents/{agent_id}/wrap-up")
async def save_wrap_up(agent_id: str, body: WrapUpRequest, request: Request) -> dict[str, Any]:
    svc = get_queue(request.app)
    try:
        pack = _call_pack(request.app, svc.get_call(body.session_id))
    except QueueError:
        pack = _settings_pack(request.app, body.session_id)
    if body.reason_code not in {c["code"] for c in reason_codes(pack)}:
        raise HTTPException(status_code=422, detail="pick a reason code from the list")
    if len(body.notes.strip()) > MAX_NOTES:
        raise HTTPException(status_code=422, detail=f"notes must be at most {MAX_NOTES} characters")
    if len(body.summary.strip()) > MAX_SUMMARY:
        raise HTTPException(
            status_code=422, detail=f"the summary must be at most {MAX_SUMMARY} characters"
        )
    try:
        call = svc.save_wrap_up(
            agent_id, body.session_id, body.reason_code, body.notes, summary=body.summary
        )
    except QueueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return call.to_dict()


@router.post("/queue/calls/{session_id}/wrap-up-draft")
async def wrap_up_draft(session_id: str, body: DraftRequest, request: Request) -> dict[str, str]:
    """Bot (P5-1): the summary and reason code it drafted when the call ended."""
    if not pack_exists(body.pack_id):
        raise HTTPException(status_code=422, detail="unknown pack")
    if body.reason_code not in {c["code"] for c in reason_codes(body.pack_id)}:
        raise HTTPException(status_code=422, detail="reason code is not in the pack")
    svc = get_queue(request.app)
    try:
        svc.get_call(session_id)
    except QueueError:
        raise HTTPException(status_code=404, detail="unknown call") from None
    try:
        svc.set_wrap_up_draft(session_id, body.model_dump())
    except QueueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return {"status": "ok"}


@router.post("/queue/calls/{session_id}/agent-left")
async def agent_left(session_id: str, request: Request) -> dict[str, str]:
    """Bot: the agent left the room. A drop unless End lands within the grace period."""
    get_queue(request.app).report_agent_left(session_id)
    return {"status": "ok"}
