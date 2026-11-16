"""Browser caller (PRD IN-12, ADR 0002).

POST /calls/browser: create a private Daily room, mint an owner token for the bot and
a non-owner token for the caller, start the same bot the dial-in path starts (no
dial-in settings), and hand the caller page {session_id, room_url, caller_token}.

GET /caller: a plain static page that calls the endpoint above and joins audio only.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agent_assist_server.bot_launcher import (
    BotLaunchRequest,
    get_launcher,
    launch_in_background,
    log_timing,
)
from agent_assist_server.daily import (
    TOKEN_EXP_SECONDS,
    DailyAPIError,
    DailyConfigError,
    DailyRestClient,
)
from agent_assist_server.queue_api import get_queue
from agent_assist_server.settings_api import get_call_settings

logger = logging.getLogger(__name__)

BROWSER_CALL_PATH = "/calls/browser"
CALLER_USER_NAME = "caller"
CALLER_PAGE = Path(__file__).parent / "static" / "caller.html"

router = APIRouter()


class BrowserCallResponse(BaseModel):
    session_id: str
    room_url: str
    caller_token: str


def _daily_client(request: Request) -> DailyRestClient:
    factory = getattr(request.app.state, "daily_client_factory", None)
    return factory() if factory else DailyRestClient.from_env()


@router.post(BROWSER_CALL_PATH)
async def start_browser_call(request: Request, background: BackgroundTasks) -> BrowserCallResponse:
    try:
        daily = _daily_client(request)
    except DailyConfigError:
        raise HTTPException(status_code=503, detail="DAILY_API_KEY is not configured") from None

    session_id = str(uuid.uuid4())
    log_timing("call_requested", session=session_id)
    try:
        room = await daily.create_browser_room()
        bot_token = await daily.create_meeting_token(room.name)
        caller_token = await daily.create_agent_token(
            room.name, CALLER_USER_NAME, int(time.time()) + TOKEN_EXP_SECONDS
        )
    except DailyAPIError as e:
        logger.error("browser call room setup failed: %s", e)
        raise HTTPException(status_code=502, detail="Failed to create Daily room") from None

    get_queue(request.app).register_session(session_id, room.url)
    # P5-2: after the response; a warm bot from the pool when one is ready.
    background.add_task(
        launch_in_background,
        get_launcher(request.app),
        BotLaunchRequest(room_url=room.url, token=bot_token, session_id=session_id),
        get_call_settings(request.app),
    )
    logger.info("browser call started session=%s room=%s", session_id, room.url)
    return BrowserCallResponse(session_id=session_id, room_url=room.url, caller_token=caller_token)


@router.get("/caller", response_class=HTMLResponse)
def caller_page() -> HTMLResponse:
    return HTMLResponse(CALLER_PAGE.read_text(encoding="utf-8"))
