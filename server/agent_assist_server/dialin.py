"""Daily pinless dial-in webhook (PRD IN-1).

Flow, modeled on Pipecat's Daily dial-in example (room_creation_api):
  Daily POSTs {callId, callDomain, To, From, ...} to this endpoint
  -> create a SIP dial-in room via the Daily REST API
  -> mint an owner meeting token for the bot
  -> start the bot with call_id/call_domain; when the room's SIP endpoint is
     ready the bot calls /dialin/pinlessCallUpdate so Daily forwards the call.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_assist_server.bot_launcher import BotLauncher, BotLaunchRequest
from agent_assist_server.daily import DailyAPIError, DailyConfigError, DailyRestClient

logger = logging.getLogger(__name__)

DIALIN_WEBHOOK_PATH = "/daily/dialin"
HMAC_ENV = "DAILY_DIALIN_HMAC"

router = APIRouter()


class DialinWebhook(BaseModel):
    """Payload Daily sends to room_creation_api when a call arrives."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    call_id: str = Field(alias="callId", min_length=1)
    call_domain: str = Field(alias="callDomain", min_length=1)
    to: str | None = Field(default=None, alias="To")
    from_: str | None = Field(default=None, alias="From")
    sip_headers: dict[str, Any] | None = Field(default=None, alias="sipHeaders")


def is_test_ping(data: Any) -> bool:
    """Daily sends {"test": "test"} when pinless_dialin is configured."""
    return isinstance(data, dict) and bool(data.get("test") or data.get("Test"))


def compute_signature(secret_b64: str, timestamp: str, body: str) -> str:
    key = base64.b64decode(secret_b64)
    digest = hmac.new(key, f"{timestamp}.{body}".encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def verify_signature(
    secret_b64: str, timestamp: str | None, signature: str | None, raw: bytes, data: Any
) -> bool:
    """Check X-Pinless-Signature. Daily signs `timestamp + "." + JSON.stringify(body)`."""
    if not timestamp or not signature:
        return False
    candidates = {
        raw.decode("utf-8", errors="replace"),
        json.dumps(data, separators=(",", ":"), ensure_ascii=False),
    }
    return any(
        hmac.compare_digest(compute_signature(secret_b64, timestamp, c), signature)
        for c in candidates
    )


def _daily_client(request: Request) -> DailyRestClient:
    factory = getattr(request.app.state, "daily_client_factory", None)
    return factory() if factory else DailyRestClient.from_env()


def _bot_launcher(request: Request) -> BotLauncher:
    launcher = getattr(request.app.state, "bot_launcher", None)
    if launcher is None:
        launcher = BotLauncher()
        request.app.state.bot_launcher = launcher
    return launcher


@router.post(DIALIN_WEBHOOK_PATH)
async def daily_dialin_webhook(request: Request) -> dict[str, Any]:
    raw = await request.body()
    try:
        data = json.loads(raw or b"null")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from None

    secret = os.getenv(HMAC_ENV)
    if secret and not verify_signature(
        secret,
        request.headers.get("x-pinless-timestamp"),
        request.headers.get("x-pinless-signature"),
        raw,
        data,
    ):
        logger.warning("dial-in webhook rejected: bad or missing signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    if is_test_ping(data):
        logger.info("dial-in webhook verification ping received")
        return {"status": "OK"}

    try:
        call = DialinWebhook.model_validate(data)
    except ValidationError:
        raise HTTPException(
            status_code=400, detail="Missing required fields: callId, callDomain"
        ) from None

    session_id = str(uuid.uuid4())
    logger.info("dial-in call received session=%s call_id=%s", session_id, call.call_id)

    try:
        daily = _daily_client(request)
        room = await daily.create_dialin_room(caller=call.from_)
        token = await daily.create_meeting_token(room.name)
    except DailyConfigError:
        logger.error("DAILY_API_KEY is not configured")
        raise HTTPException(status_code=500, detail="Daily is not configured") from None
    except DailyAPIError as e:
        logger.error("Daily room setup failed: %s", e)
        raise HTTPException(status_code=502, detail="Failed to create Daily room") from None

    await _bot_launcher(request).launch(
        BotLaunchRequest(
            room_url=room.url,
            token=token,
            call_id=call.call_id,
            call_domain=call.call_domain,
            session_id=session_id,
        )
    )

    return {"status": "OK", "sessionId": session_id, "dailyRoom": room.url}
