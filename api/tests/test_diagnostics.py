"""Diagnostics: capture, guarding, and the client-error intake."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import diagnostics
from app.database import Base, get_db
from app.main import app
from app.models import User
from app.security import hash_password

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=test_engine)
    app.dependency_overrides[get_db] = override_get_db
    diagnostics.clear()
    db = TestSession()
    db.add(User(username="admin", password_hash=hash_password("pw"), permissions="*"))
    db.add(User(username="limited", password_hash=hash_password("pw"), permissions="coupons"))
    db.commit()
    db.close()
    yield
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=test_engine)
    diagnostics.clear()


def test_records_are_returned_newest_first():
    diagnostics.record("server_error", path="/one")
    diagnostics.record("server_error", path="/two")
    snap = diagnostics.snapshot()
    assert [r["path"] for r in snap["records"]] == ["/two", "/one"]
    assert snap["count"] == 2 and snap["boot_id"]


def test_buffer_is_bounded():
    for i in range(diagnostics.MAX_RECORDS + 25):
        diagnostics.record("server_error", path=f"/{i}")
    assert diagnostics.snapshot()["count"] == diagnostics.MAX_RECORDS


def test_long_fields_are_clipped():
    diagnostics.record("server_error", message="x" * 99_000)
    msg = diagnostics.snapshot()["records"][0]["message"]
    assert len(msg) < 99_000 and "more chars" in msg


def test_record_returns_a_reference_that_identifies_it():
    ref = diagnostics.record("server_error", path="/boom")
    assert diagnostics.snapshot()["records"][0]["id"] == ref


def test_admin_session_can_read_errors():
    client = TestClient(app)
    assert client.post(
        "/auth/login", json={"username": "admin", "password": "pw"}
    ).status_code == 200
    assert client.get("/diagnostics/errors").status_code == 200


def test_a_non_admin_grant_cannot_read_errors():
    # A valid session is not enough — diagnostics spans every tool, so it takes
    # the "*" grant. This is the case that keeps a coupons-only account out.
    client = TestClient(app)
    client.post("/auth/login", json={"username": "limited", "password": "pw"})
    assert client.get("/diagnostics/errors").status_code == 403


def test_anonymous_cannot_read_errors():
    assert TestClient(app).get("/diagnostics/errors").status_code == 403


def test_api_key_grants_access_without_a_session(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "state_api_key", "secret-key")
    client = TestClient(app)
    assert client.get(
        "/diagnostics/errors", headers={"X-API-Key": "secret-key"}
    ).status_code == 200
    assert client.get(
        "/diagnostics/errors", headers={"X-API-Key": "nope"}
    ).status_code == 403


def test_client_errors_land_in_the_same_buffer():
    client = TestClient(app)
    client.post("/auth/login", json={"username": "limited", "password": "pw"})
    r = client.post("/diagnostics/client", json={
        "message": "The server didn't respond (502).",
        "route": "/", "request": "GET /coupons", "status": 502,
    })
    assert r.status_code == 202
    rec = diagnostics.snapshot()["records"][0]
    assert rec["kind"] == "client_error" and rec["status"] == 502
    assert rec["id"] == r.json()["id"]


def test_client_intake_rejects_an_oversized_payload():
    client = TestClient(app)
    client.post("/auth/login", json={"username": "limited", "password": "pw"})
    r = client.post("/diagnostics/client", json={"message": "x" * 5000})
    assert r.status_code == 422


def test_client_intake_refuses_anonymous_callers():
    # Open to any logged-in user (the point is catching failures on a phone),
    # but not to the internet — otherwise anyone could flood the buffer.
    r = TestClient(app).post("/diagnostics/client", json={"message": "spam"})
    assert r.status_code == 401


def test_clearing_empties_the_buffer():
    diagnostics.record("server_error", path="/x")
    client = TestClient(app)
    client.post("/auth/login", json={"username": "admin", "password": "pw"})
    assert client.delete("/diagnostics/errors").json() == {"cleared": 1}
    assert diagnostics.snapshot()["count"] == 0


def test_unhandled_exception_is_recorded_and_returns_a_reference():
    # A route that raises must not leak a traceback to the caller, but must
    # leave one behind that the reference points at.
    @app.get("/_boom_test")
    def boom():
        raise RuntimeError("kaboom in a test")

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/_boom_test")
    assert r.status_code == 500
    ref = r.json()["error_id"]
    assert "kaboom" not in r.text        # traceback never reaches the browser

    rec = next(x for x in diagnostics.snapshot()["records"] if x["id"] == ref)
    assert rec["error"] == "RuntimeError" and "kaboom" in rec["message"]
    assert "RuntimeError" in rec["traceback"]
