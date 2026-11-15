"""Forward a held pinless dial-in call into the bot's room (PRD IN-1).

Daily holds the PSTN call while our webhook creates the room and starts the bot. When
the room's SIP endpoint is ready, daily-python fires `dialin-ready` and we POST
/dialin/pinlessCallUpdate so Daily connects the caller to that endpoint.

Pipecat 1.15.0's DailyTransport does this itself when `dialin_settings` is set, but it
forwards the raw event payload as `sipUri`. Current daily-python sends
`{"provider": ..., "sipEndpoint": "sip:..."}` instead of a string, so Daily rejects the
update (400 `"sipUri" must be a string`) and the caller never reaches the bot. We do
the update ourselves and leave `dialin_settings` unset on the transport.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_DAILY_API_URL = "https://api.daily.co/v1"


@dataclass(frozen=True)
class DialinConfig:
    """Pinless dial-in call identifiers from Daily's webhook, plus the REST creds
    needed to forward the call into the room."""

    call_id: str
    call_domain: str
    api_key: str
    api_url: str = DEFAULT_DAILY_API_URL


class DialinForwardError(RuntimeError):
    """pinlessCallUpdate failed. Message carries status and Daily's error body only."""


def sip_uri_from_ready(data: Any) -> str | None:
    """SIP URI from a dialin-ready payload: a plain string (older daily-python) or a
    dict with `sipEndpoint` (current daily-python)."""
    if isinstance(data, str):
        return data or None
    if isinstance(data, dict):
        uri = data.get("sipEndpoint") or data.get("sip_endpoint")
        return uri if isinstance(uri, str) and uri else None
    return None


def call_update_body(dialin: DialinConfig, sip_uri: str) -> dict[str, str]:
    return {"callId": dialin.call_id, "callDomain": dialin.call_domain, "sipUri": sip_uri}


async def forward_call(
    dialin: DialinConfig,
    sip_uri: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = 10.0,
) -> None:
    """POST /dialin/pinlessCallUpdate once. Raises DialinForwardError on failure."""
    forwarder = DialinForwarder(dialin, transport=transport, timeout=timeout)
    try:
        await forwarder.forward(sip_uri)
    finally:
        await forwarder.close()


class DialinForwarder:
    """Holds one HTTP client to the Daily API for a dial-in call.

    `warm()` opens the TLS connection when the bot joins, so the pinlessCallUpdate on
    dialin-ready reuses it instead of paying DNS + TLS while the caller is on hold
    (cold POST 0.3-0.6 s vs warm 0.2 s measured from the bot host).
    """

    def __init__(
        self,
        dialin: DialinConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ):
        self.dialin = dialin
        self._client = httpx.AsyncClient(
            base_url=dialin.api_url.rstrip("/"),
            headers={"Authorization": f"Bearer {dialin.api_key}"},
            transport=transport,
            timeout=timeout,
        )

    def __repr__(self) -> str:  # never show the key
        return f"DialinForwarder(call_id={self.dialin.call_id!r})"

    async def warm(self) -> None:
        """Best effort: any response (or error) is fine, only the connection matters."""
        try:
            await self._client.get("/")
        except httpx.HTTPError:
            pass

    async def forward(self, sip_uri: str) -> None:
        body = call_update_body(self.dialin, sip_uri)
        try:
            r = await self._client.post("/dialin/pinlessCallUpdate", json=body)
        except httpx.HTTPError as e:
            raise DialinForwardError(f"pinlessCallUpdate failed: {type(e).__name__}") from None
        if r.status_code != 200:
            raise DialinForwardError(f"pinlessCallUpdate returned {r.status_code}: {r.text[:300]}")

    async def close(self) -> None:
        await self._client.aclose()
