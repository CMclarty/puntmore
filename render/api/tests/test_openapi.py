"""Every route is typed and tagged, and the schema is never public."""
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.main import app
from app.security import require_user, user_permissions

# Routes whose body is not JSON (a file, a CSV) or that are the docs themselves.
NO_MODEL = {
    ("GET", "/pnl/{category}/bets.csv"),
    ("GET", "/openapi.json"),
    ("GET", "/docs"),
}
NO_TAG = {("GET", "/openapi.json"), ("GET", "/docs")}


def _routes():
    # The app's own routes — a test elsewhere may mount a throwaway one.
    for r in app.routes:
        if isinstance(r, APIRoute) and r.endpoint.__module__.startswith("app."):
            for m in r.methods:
                yield m, r.path, r


def test_every_route_declares_a_response_model():
    missing = [
        (m, p) for m, p, r in _routes()
        if r.response_model is None and (m, p) not in NO_MODEL and r.status_code != 204
    ]
    assert missing == []


def test_every_route_is_tagged_by_tool():
    missing = [(m, p) for m, p, r in _routes() if not r.tags and (m, p) not in NO_TAG]
    assert missing == []


def test_the_schema_builds_and_names_the_value_feed_and_key_auth():
    schema = app.openapi()
    assert schema["info"]["version"].startswith("1.")
    assert "ValueSelection" in schema["components"]["schemas"]
    assert {"Bearer", "X-API-Key"} <= set(schema["components"]["securitySchemes"])
    assert "/value" in schema["paths"]


def test_docs_need_a_session_or_a_key():
    api = TestClient(app)
    assert api.get("/openapi.json").status_code == 200        # conftest: "*" session
    assert "swagger" in api.get("/docs").text.lower()
    app.dependency_overrides.pop(user_permissions, None)
    app.dependency_overrides.pop(require_user, None)
    try:
        assert api.get("/openapi.json").status_code == 401
        assert api.get("/docs").status_code == 401
    finally:
        app.dependency_overrides[user_permissions] = lambda: "*"
        app.dependency_overrides[require_user] = lambda: "testuser"
