import logging
import os
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from agent_assist_server import auth, portal
from agent_assist_server.agent_token import router as agent_token_router
from agent_assist_server.bot_launcher import BotLauncher
from agent_assist_server.browser_call import router as browser_call_router
from agent_assist_server.caller_api import router as caller_router
from agent_assist_server.dialin import router as dialin_router
from agent_assist_server.queue_api import router as queue_router
from agent_assist_server.settings_api import router as settings_router
from agent_assist_server.wrap_up_api import router as wrap_up_router


def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "agent-assist-server"}


def configure_logging() -> None:
    """Show the server's own INFO logs (dial-in calls, `[timing]` lines). Uvicorn only
    configures its own loggers, so without this they are dropped. Idempotent."""
    log = logging.getLogger("agent_assist_server")
    if log.handlers or logging.getLogger().handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def root() -> RedirectResponse:
    """The site root opens the agent portal. The caller demo page stays at /caller."""
    return RedirectResponse(portal.MOUNT, status_code=307)


def warm_pool_lifespan(env: Mapping[str, str]):
    """P5-2: the warm bot pool starts with the server (on Fly, when the machine wakes)
    and its idle bots stop with it. A launcher already set (tests) is left alone."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        launcher = None
        if getattr(app.state, "bot_launcher", None) is None:
            launcher = BotLauncher.from_env(env)
            app.state.bot_launcher = launcher
            await launcher.start()
        try:
            yield
        finally:
            if launcher is not None:
                await launcher.close()
                if getattr(app.state, "bot_launcher", None) is launcher:
                    del app.state.bot_launcher

    return lifespan


def create_app(
    env: Mapping[str, str] | None = None, clock: Callable[[], float] | None = None
) -> FastAPI:
    """Build the server. Login (P2-6) is on when APP_PASSWORD is set in env."""
    env = os.environ if env is None else env
    config = auth.config_from_env(env, clock=clock) if clock else auth.config_from_env(env)
    app = FastAPI(title="Deepgram Vitals", lifespan=warm_pool_lifespan(env))
    app.include_router(dialin_router)
    app.include_router(agent_token_router)
    app.include_router(browser_call_router)
    app.include_router(queue_router)
    app.include_router(caller_router)
    app.include_router(wrap_up_router)
    app.include_router(settings_router)
    app.include_router(portal.build_router(portal.dist_from_env(env)))
    app.add_api_route("/healthz", healthz, methods=["GET"])
    app.add_api_route("/", root, methods=["GET"], include_in_schema=False)
    auth.install(app, config)
    return app


configure_logging()
app = create_app()
