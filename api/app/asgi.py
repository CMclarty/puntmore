"""Production ASGI entrypoint: one service serving the API and the frontend.

Locally and in tests, `app.main:app` IS the API and nginx puts the built
frontend in front of it. On a single-container host there is no nginx, so this
module does that job: the API mounts under /api and the frontend's built assets
are served from /.

Why one service rather than a static site plus an API service: the frontend
calls relative `/api/...` paths and the session cookie is `same_site="lax"`, so
it is only sent same-origin. Splitting them across two hostnames would 404 every
API call, and pointing the frontend at an absolute API URL would stop the cookie
being sent at all — login would fail silently rather than error. Serving both
from one origin makes that class of bug impossible.

Tests import `app.main:app` directly, so mounting here costs no test churn.
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from starlette.staticfiles import StaticFiles

from .identity import render_index
from .site import site_for
from .main import app as api_app

logger = logging.getLogger("uvicorn.error")

# Where the Dockerfile's frontend build stage drops the compiled assets.
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_root: FastAPI):
    """Run the mounted API's lifespan.

    Starlette does NOT run a mounted sub-application's lifespan — only the
    top-level app's. Without this, `main.lifespan` never fires under this
    entrypoint: no `create_all`, no `ensure_schema`, no seeded users, no
    providers on `app.state`. The symptom is nasty because /health has no
    dependencies and answers 200, so the platform health check goes green and
    the service looks up while every real request dies on
    "no such table: users".

    It must run against `api_app`, not the root: the lifespan assigns to
    `app.state`, and inside a mount `request.app` is the sub-app, so state set
    on the root would be invisible to every route that reads it.
    """
    async with api_app.router.lifespan_context(api_app):
        yield


root = FastAPI(title="Puntmore", docs_url=None, redoc_url=None, lifespan=lifespan)
root.mount("/api", api_app)


if STATIC_DIR.is_dir():
    # html=True serves index.html for "/" and returns 404 for missing files,
    # which the SPA fallback below turns into index.html.
    root.mount(
        "/assets",
        StaticFiles(directory=STATIC_DIR / "assets"),
        name="assets",
    )

    @root.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str, request: Request):
        """Serve the SPA shell for any non-API, non-asset path.

        The tracker uses client-side routing, so a hard refresh on /upload must
        return index.html rather than 404. Requests under /api never reach here
        — that mount is matched first.
        """
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        # The shell's head carries THIS path's manifest, icon and title — iOS
        # reads the manifest at load, before any script runs (identity.py).
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        # Puntmore or Shalgo by the host asked for (site.py): same shell, its own name.
        return HTMLResponse(render_index(html, "/" + full_path,
                                         site_for(request.headers.get("host"))))
else:
    logger.warning(
        "No static build at %s — serving the API only. In production the "
        "Dockerfile's frontend stage populates this.",
        STATIC_DIR,
    )
