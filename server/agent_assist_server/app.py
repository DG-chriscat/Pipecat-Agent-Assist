import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

DAILY_API_URL = "https://api.daily.co/v1"
AGENT_TOKEN_TTL_SECONDS = 3600
SPIKE_AGENT_PAGE = Path(__file__).parent / "static" / "spike_agent.html"

app = FastAPI(title="Agent Assist Server")


async def get_daily_http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        yield client


class AgentTokenRequest(BaseModel):
    user_name: str = Field(min_length=1, max_length=100)


class AgentTokenResponse(BaseModel):
    room_name: str
    token: str
    expires_at: int


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "agent-assist-server"}


@app.post("/rooms/{room_name}/agent-token")
async def create_agent_token(
    room_name: str,
    body: AgentTokenRequest,
    http: Annotated[httpx.AsyncClient, Depends(get_daily_http_client)],
) -> AgentTokenResponse:
    """Create a short lived, non-owner Daily meeting token for an agent's browser."""
    api_key = os.environ.get("DAILY_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="DAILY_API_KEY is not configured")

    expires_at = int(time.time()) + AGENT_TOKEN_TTL_SECONDS
    payload = {
        "properties": {
            "room_name": room_name,
            "user_name": body.user_name,
            "is_owner": False,
            "exp": expires_at,
        }
    }
    try:
        resp = await http.post(
            f"{DAILY_API_URL}/meeting-tokens",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Daily API unreachable") from exc

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502, detail=f"Daily API error creating token: HTTP {resp.status_code}"
        )
    token = resp.json().get("token")
    if not token:
        raise HTTPException(status_code=502, detail="Daily API returned no token")
    return AgentTokenResponse(room_name=room_name, token=token, expires_at=expires_at)


@app.get("/spike/agent", response_class=HTMLResponse)
def spike_agent_page() -> HTMLResponse:
    """Spike S4 test page: join a Daily room in the browser with an agent token."""
    return HTMLResponse(SPIKE_AGENT_PAGE.read_text(encoding="utf-8"))
