"""Wrap up (P3-5) and agent drop (QH-9).

    GET  /calls/{session_id}/wrap-up?agent_id=   -> what the wrap up view shows
    POST /agents/{agent_id}/wrap-up               {session_id, reason_code, notes}
                                                  -> the call, saved; agent available
    POST /queue/calls/{session_id}/agent-left     (bot: the agent left the room)

Reason codes come from the active use case pack (`wrap_up.reason_codes` in
`packs/<INTAKE_PACK>.yaml`, the same pack the bot runs). The server reads the YAML
itself and does not import the bot package. The call summary draft (AP-9) lands in
Phase 4; until then `summary_draft` is null and the portal shows a placeholder.
"""

from __future__ import annotations

import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from agent_assist_server.queue_api import get_queue
from agent_assist_server.queue_service import MAX_NOTES, QueueError

# server/agent_assist_server/wrap_up_api.py -> repo root / packs
PACKS_DIR = Path(__file__).resolve().parents[2] / "packs"
PACK_ENV = "INTAKE_PACK"
DEFAULT_PACK = "claims_status"
DEFAULT_CODES = [{"code": "other", "label": "Other"}]

router = APIRouter()


@lru_cache(maxsize=8)
def _load_codes(path: Path, mtime: float) -> tuple[tuple[str, str], ...]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    codes = ((data.get("wrap_up") or {}).get("reason_codes")) or DEFAULT_CODES
    return tuple((str(c["code"]), str(c["label"])) for c in codes)


def reason_codes(pack: str | None = None) -> list[dict[str, str]]:
    """The active pack's wrap up reason codes, in pack order."""
    name = pack or os.environ.get(PACK_ENV, "").strip() or DEFAULT_PACK
    path = PACKS_DIR / f"{name}.yaml"
    if not path.is_file():
        return list(DEFAULT_CODES)
    return [{"code": c, "label": label} for c, label in _load_codes(path, path.stat().st_mtime)]


def _text(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


class WrapUpRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    reason_code: str = Field(min_length=1, max_length=64)
    notes: str = Field(default="", max_length=MAX_NOTES + 200)


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
    end = call.ended_at if call.ended_at is not None else time.time()
    start = call.answered_at if call.answered_at is not None else end
    return {
        "session_id": call.session_id,
        "status": call.status.value,
        "ended_by": call.ended_by,
        "caller_name": _text(call.summary.get("full_name")) or "Unknown caller",
        "reason": _text(call.summary.get("reason")) or _text(call.summary.get("summary")),
        "identity_result": call.identity_result,
        "duration_secs": round(max(0.0, end - start), 1),
        "summary_draft": None,
        "reason_codes": reason_codes(),
        "wrap_up": call.wrap_up,
    }


@router.post("/agents/{agent_id}/wrap-up")
async def save_wrap_up(agent_id: str, body: WrapUpRequest, request: Request) -> dict[str, Any]:
    if body.reason_code not in {c["code"] for c in reason_codes()}:
        raise HTTPException(status_code=422, detail="pick a reason code from the list")
    if len(body.notes.strip()) > MAX_NOTES:
        raise HTTPException(status_code=422, detail=f"notes must be at most {MAX_NOTES} characters")
    try:
        call = get_queue(request.app).save_wrap_up(
            agent_id, body.session_id, body.reason_code, body.notes
        )
    except QueueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return call.to_dict()


@router.post("/queue/calls/{session_id}/agent-left")
async def agent_left(session_id: str, request: Request) -> dict[str, str]:
    """Bot: the agent left the room. A drop unless End lands within the grace period."""
    get_queue(request.app).report_agent_left(session_id)
    return {"status": "ok"}
