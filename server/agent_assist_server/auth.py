"""Shared password login for the whole app (P2-6).

APP_PASSWORD set: every route needs a signed session cookie, except GET /healthz,
POST /daily/dialin (own HMAC), GET/POST /login, and the login page assets.
Unauthenticated HTML page loads redirect to /login?next=...; other HTTP requests get
401; websockets are accepted and closed with code 4401 so the browser sees the reason.

The cookie is an itsdangerous-signed {"iat": issued_at} payload keyed by
SESSION_SECRET, valid for 12 hours. Sessions are per browser; nothing is global.

The bot runs as a child process and posts its handoff to /queue/calls. It gets a bot
key (HMAC of SESSION_SECRET) in its environment and sends it as a Bearer token; that
key opens only the bot endpoints (enqueue, end, agent-left, wrap-up-draft).

APP_PASSWORD unset: auth is off (local dev, tests) and one warning is logged.
APP_PASSWORD without SESSION_SECRET: refuse to start. Neither value is ever logged.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeSerializer
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

PASSWORD_ENV = "APP_PASSWORD"
SECRET_ENV = "SESSION_SECRET"
BOT_KEY_ENV = "AGENT_ASSIST_BOT_KEY"
CLIENT_IP_HEADER_ENV = "TRUSTED_CLIENT_IP_HEADER"

COOKIE_NAME = "aa_session"
SESSION_MAX_AGE = 12 * 3600
DEFAULT_NEXT = "/caller"
WS_UNAUTHORIZED = 4401
MAX_FAILURES = 10
FAILURE_WINDOW_S = 600

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
STATIC_DIR = Path(__file__).parent / "static" / "login"
LOGIN_PAGE = STATIC_DIR / "login.html"
ASSETS = {"d-tile.png": STATIC_DIR / "d-tile.png", "wordmark.png": STATIC_DIR / "wordmark.png"}

PUBLIC_ROUTES = {
    ("GET", "/healthz"),
    ("HEAD", "/healthz"),
    ("POST", "/daily/dialin"),
    ("GET", "/login"),
    ("POST", "/login"),
}
ASSET_PREFIX = "/login/assets/"
BOT_ROUTES = re.compile(r"^/queue/calls(/[^/]+/(end|agent-left|wrap-up-draft))?$")
# QH-5b: the bot's own websocket (queue_api.bot_ws). Bot key only.
BOT_SOCKET = re.compile(r"^/ws/bot/[^/]+$")
_UNSAFE_NEXT = re.compile(r"[\x00-\x20\x7f\\]")


def bot_key(secret: str) -> str:
    """Key the server hands its bot processes for the queue handoff endpoints."""
    return hmac.new(secret.encode(), b"agent-assist-bot-key", hashlib.sha256).hexdigest()


def safe_next(target: str | None) -> str:
    """Only same site relative paths; anything else goes to DEFAULT_NEXT."""
    if not target:
        return DEFAULT_NEXT
    decoded = unquote(target)
    for t in (target, decoded):
        if not t.startswith("/") or t.startswith("//") or _UNSAFE_NEXT.search(t):
            return DEFAULT_NEXT
    return target


class RateLimiter:
    """Failed logins per key (client IP) in a sliding window. In memory, one process."""

    def __init__(
        self,
        max_failures: int = MAX_FAILURES,
        window_s: float = FAILURE_WINDOW_S,
        clock: Callable[[], float] = time.time,
    ):
        self.max_failures = max_failures
        self.window_s = window_s
        self._clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str) -> deque[float]:
        q = self._failures[key]
        cutoff = self._clock() - self.window_s
        while q and q[0] <= cutoff:
            q.popleft()
        if not q:
            del self._failures[key]
        return q

    def blocked(self, key: str) -> bool:
        return len(self._prune(key)) >= self.max_failures

    def record_failure(self, key: str) -> None:
        self._prune(key)
        self._failures[key].append(self._clock())


@dataclass
class AuthConfig:
    password: str
    secret: str
    clock: Callable[[], float] = time.time
    client_ip_header: str | None = None
    limiter: RateLimiter = field(init=False)

    def __post_init__(self) -> None:
        self.limiter = RateLimiter(clock=self.clock)
        self._serializer = URLSafeSerializer(self.secret, salt="agent-assist-session")
        self._bot_key = bot_key(self.secret)

    def check_password(self, candidate: str) -> bool:
        return hmac.compare_digest(candidate.encode(), self.password.encode())

    def issue_cookie(self) -> str:
        return self._serializer.dumps({"iat": int(self.clock()), "n": secrets.token_hex(8)})

    def valid_cookie(self, value: str | None) -> bool:
        if not value:
            return False
        try:
            data = self._serializer.loads(value)
        except BadSignature:
            return False
        iat = data.get("iat") if isinstance(data, dict) else None
        if not isinstance(iat, int):
            return False
        return iat <= self.clock() < iat + SESSION_MAX_AGE

    def valid_bot_key(self, authorization: str | None) -> bool:
        if not authorization or not authorization.startswith("Bearer "):
            return False
        return hmac.compare_digest(authorization[7:].encode(), self._bot_key.encode())

    def client_ip(self, conn: HTTPConnection) -> str:
        if self.client_ip_header:
            forwarded = conn.headers.get(self.client_ip_header)
            if forwarded:
                return forwarded.strip()
        return conn.client.host if conn.client else "unknown"


def config_from_env(
    env: Mapping[str, str], clock: Callable[[], float] = time.time
) -> AuthConfig | None:
    password = env.get(PASSWORD_ENV, "")
    if not password:
        logger.warning("%s is not set: login is disabled, every route is open", PASSWORD_ENV)
        return None
    secret = env.get(SECRET_ENV, "")
    if not secret:
        raise RuntimeError(f"{PASSWORD_ENV} is set but {SECRET_ENV} is not; refusing to start")
    header = env.get(CLIENT_IP_HEADER_ENV, "").strip() or None
    return AuthConfig(password=password, secret=secret, clock=clock, client_ip_header=header)


# ---- middleware ---------------------------------------------------------------------


def _wants_html(conn: HTTPConnection, method: str) -> bool:
    return method in ("GET", "HEAD") and "text/html" in conn.headers.get("accept", "")


class AuthMiddleware:
    def __init__(self, app: ASGIApp, config: AuthConfig):
        self.app = app
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        method = scope.get("method", "GET")
        conn = HTTPConnection(scope)
        if scope["type"] == "websocket" and BOT_SOCKET.match(path):
            # QH-5b: bot only. The bot key opens it; a portal cookie does not.
            if self.config.valid_bot_key(conn.headers.get("authorization")):
                await self.app(scope, receive, send)
            else:
                await self._ws_unauthorized(receive, send)
            return
        if scope["type"] == "http" and (
            (method, path) in PUBLIC_ROUTES
            or (method in ("GET", "HEAD") and path.startswith(ASSET_PREFIX))
        ):
            await self.app(scope, receive, send)
            return
        if self.config.valid_cookie(conn.cookies.get(COOKIE_NAME)):
            await self.app(scope, receive, send)
            return
        if (
            scope["type"] == "http"
            and method == "POST"
            and BOT_ROUTES.match(path)
            and self.config.valid_bot_key(conn.headers.get("authorization"))
        ):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            await self._ws_unauthorized(receive, send)
            return
        if _wants_html(conn, method):
            target = path + (f"?{conn.url.query}" if conn.url.query else "")
            response = RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)
        else:
            response = JSONResponse({"detail": "Not authenticated"}, status_code=401)
        await response(scope, receive, send)

    @staticmethod
    async def _ws_unauthorized(receive: Receive, send: Send) -> None:
        # Accept, then close with 4401: a close before accept reaches the browser
        # as a bare 1006 with no reason.
        await receive()
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.close", "code": WS_UNAUTHORIZED})


# ---- login routes -------------------------------------------------------------------

ERROR_BAD_PASSWORD = "That password did not work. Try again."
ERROR_RATE_LIMITED = "Too many tries. Wait a few minutes, then try again."


def render_login(next_target: str | None, error: str | None = None) -> str:
    action = "/login"
    if next_target:
        action += f"?next={quote(safe_next(next_target), safe='')}"
    error_html = f'<p class="error" role="alert">{escape(error)}</p>' if error else ""
    page = LOGIN_PAGE.read_text(encoding="utf-8")
    return page.replace("{{action}}", escape(action)).replace("{{error}}", error_html)


def _secure_cookie(request: Request) -> bool:
    return (request.url.hostname or "") not in LOCAL_HOSTS


def build_router(config: AuthConfig) -> APIRouter:
    router = APIRouter()

    @router.get("/login", response_class=HTMLResponse)
    def login_page(next: str | None = None) -> HTMLResponse:  # noqa: A002
        return HTMLResponse(render_login(next))

    @router.post("/login")
    async def login_submit(request: Request, next: str | None = None):  # noqa: A002
        ip = config.client_ip(request)
        if config.limiter.blocked(ip):
            logger.warning("login rate limited for %s", ip)
            return HTMLResponse(render_login(next, ERROR_RATE_LIMITED), status_code=429)
        form = parse_qs((await request.body())[:4096].decode("utf-8", "replace"))
        candidate = (form.get("password") or [""])[0]
        if not config.check_password(candidate):
            config.limiter.record_failure(ip)
            logger.info("login failed from %s", ip)
            return HTMLResponse(render_login(next, ERROR_BAD_PASSWORD), status_code=401)
        response = RedirectResponse(safe_next(next), status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            config.issue_cookie(),
            max_age=SESSION_MAX_AGE,
            httponly=True,
            secure=_secure_cookie(request),
            samesite="lax",
            path="/",
        )
        logger.info("login ok from %s", ip)
        return response

    @router.get("/logout")
    def logout(request: Request) -> RedirectResponse:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(
            COOKIE_NAME, path="/", httponly=True, secure=_secure_cookie(request), samesite="lax"
        )
        return response

    @router.get(ASSET_PREFIX + "{name}")
    def login_asset(name: str):
        path = ASSETS.get(name)
        if path is None:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(path, media_type="image/png")

    return router


def install(app: FastAPI, config: AuthConfig | None) -> None:
    """Wire login routes and the middleware into app. No-op when auth is disabled."""
    app.state.auth = config
    if config is None:
        return
    app.include_router(build_router(config))
    app.add_middleware(AuthMiddleware, config=config)
