"""Agent meeting token endpoint and spike S4 browser join page.

POST /rooms/{room_name}/agent-token mints a short lived, non-owner Daily
meeting token so a human agent's browser can join the call room.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from agent_assist_server.daily import DailyAPIError, DailyConfigError, DailyRestClient

logger = logging.getLogger(__name__)

AGENT_TOKEN_TTL_SECONDS = 3600
SPIKE_AGENT_PAGE = Path(__file__).parent / "static" / "spike_agent.html"

router = APIRouter()


class AgentTokenRequest(BaseModel):
    user_name: str = Field(min_length=1, max_length=100)


class AgentTokenResponse(BaseModel):
    room_name: str
    token: str
    expires_at: int


def _daily_client(request: Request) -> DailyRestClient:
    factory = getattr(request.app.state, "daily_client_factory", None)
    return factory() if factory else DailyRestClient.from_env()


async def mint_agent_token(
    daily: DailyRestClient, room_name: str, user_name: str
) -> tuple[str, int]:
    """Non-owner token for one agent in one room. Shared by this endpoint and queue accept."""
    expires_at = int(time.time()) + AGENT_TOKEN_TTL_SECONDS
    token = await daily.create_agent_token(room_name, user_name, expires_at)
    return token, expires_at


@router.post("/rooms/{room_name}/agent-token")
async def create_agent_token(
    room_name: str, body: AgentTokenRequest, request: Request
) -> AgentTokenResponse:
    """Create a short lived, non-owner Daily meeting token for an agent's browser."""
    try:
        daily = _daily_client(request)
    except DailyConfigError:
        raise HTTPException(status_code=503, detail="DAILY_API_KEY is not configured") from None

    try:
        token, expires_at = await mint_agent_token(daily, room_name, body.user_name)
    except DailyAPIError as e:
        logger.error("agent token creation failed: %s", e)
        raise HTTPException(status_code=502, detail="Daily API error creating token") from None
    return AgentTokenResponse(room_name=room_name, token=token, expires_at=expires_at)


@router.get("/spike/agent", response_class=HTMLResponse)
def spike_agent_page() -> HTMLResponse:
    """Spike S4 test page: join a Daily room in the browser with an agent token."""
    return HTMLResponse(SPIKE_AGENT_PAGE.read_text(encoding="utf-8"))
