"""Production ASGI entrypoint (app/asgi.py).

Regression cover for a live outage: `root` mounts the API as a sub-application,
and Starlette does not run a mounted sub-app's lifespan. Nothing was
initialised — no tables, no seeded users, no providers — but /health has no
dependencies and still answered 200, so Render's health check went green while
every real request died on "no such table: users".
"""
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.asgi import root
from app.main import app as api_app


def test_root_lifespan_runs_the_mounted_apps_lifespan(monkeypatch):
    ran_against = []

    @asynccontextmanager
    async def fake_lifespan(app):
        ran_against.append(app)
        yield

    monkeypatch.setattr(api_app.router, "lifespan_context", fake_lifespan)

    with TestClient(root):
        pass

    # It must run, and run against the API app rather than the root: the
    # lifespan assigns to app.state, and inside a mount request.app is the
    # sub-app, so state set on the root would be invisible to every route.
    assert ran_against == [api_app]


def test_health_is_reachable_through_the_mount(monkeypatch):
    @asynccontextmanager
    async def fake_lifespan(app):
        yield

    monkeypatch.setattr(api_app.router, "lifespan_context", fake_lifespan)

    with TestClient(root) as client:
        assert client.get("/api/health").status_code == 200


def test_health_alone_does_not_prove_the_app_is_up(monkeypatch):
    """/health must not be treated as evidence that startup succeeded.

    It answers 200 with the lifespan stubbed out entirely, which is exactly why
    the original breakage reached production unnoticed. Pinned so nobody
    "simplifies" the health check into something that looks like a readiness
    probe without being one.
    """
    @asynccontextmanager
    async def never_initialises(app):
        yield

    monkeypatch.setattr(api_app.router, "lifespan_context", never_initialises)

    with TestClient(root) as client:
        assert client.get("/api/health").status_code == 200


# --- readiness probe ---

def test_ready_reports_ready_when_startup_ran():
    """The real lifespan runs here, so this is the genuine happy path."""
    with TestClient(root) as client:
        resp = client.get("/api/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"
        assert all(body["checks"].values())


def test_ready_fails_when_the_lifespan_did_not_run(monkeypatch):
    """The exact production failure: mounted app, lifespan skipped.

    /health is green in this state (see above); /ready must not be, or it is
    no better than the check it replaced.
    """
    @asynccontextmanager
    async def never_initialises(app):
        yield

    monkeypatch.setattr(api_app.router, "lifespan_context", never_initialises)
    for name in ("odds_provider", "ocr_provider", "notifier"):
        monkeypatch.delattr(api_app.state, name, raising=False)

    with TestClient(root) as client:
        resp = client.get("/api/ready")
        assert resp.status_code == 503
        assert resp.json()["checks"]["providers"] is False
