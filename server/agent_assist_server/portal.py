"""Agent portal (P3-1): the built React app from portal/dist, served at /agent.

GET /agent and every path under it: a file that exists in the dist folder is served
as is (hashed assets under /agent/assets/); any other path gets index.html so the
SPA can route on the client. Missing asset-like paths (with a file extension) 404
instead of returning HTML. Paths never resolve outside the dist folder.

The shared login (auth.py) covers these routes like any other page: logged out HTML
loads redirect to /login?next=/agent.

PORTAL_DIST overrides the folder (tests, custom builds). Default: <repo>/portal/dist,
which is also where the Docker image copies the node build stage output.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

PORTAL_DIST_ENV = "PORTAL_DIST"
DEFAULT_DIST = Path(__file__).resolve().parents[2] / "portal" / "dist"
MOUNT = "/agent"

NOT_BUILT = (
    "<!doctype html><title>Portal not built</title>"
    "<p>The agent portal is not built. Run <code>npm ci && npm run build</code> in portal/.</p>"
)


def dist_from_env(env: Mapping[str, str]) -> Path:
    value = env.get(PORTAL_DIST_ENV, "").strip()
    return Path(value) if value else DEFAULT_DIST


def _resolve(dist: Path, rel: str) -> Path | None:
    """The file under dist for rel, or None. Rejects anything that escapes dist."""
    root = dist.resolve()
    try:
        target = (root / rel).resolve()
    except (OSError, ValueError):
        return None
    if target != root and root not in target.parents:
        return None
    return target if target.is_file() else None


def build_router(dist: Path) -> APIRouter:
    router = APIRouter()

    def serve(rel: str) -> Response:
        index = dist / "index.html"
        if not index.is_file():
            return HTMLResponse(NOT_BUILT, status_code=503)
        if rel:
            found = _resolve(dist, rel)
            if found is not None:
                return FileResponse(found)
            if "." in rel.rsplit("/", 1)[-1]:
                return JSONResponse({"detail": "Not Found"}, status_code=404)
        # index.html must not be cached: it names the current hashed assets.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    @router.get(MOUNT, include_in_schema=False)
    def portal_index() -> Response:
        return serve("")

    @router.get(MOUNT + "/{rel:path}", include_in_schema=False)
    def portal_path(rel: str) -> Response:
        return serve(rel)

    return router
