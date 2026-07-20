"""Auth flow tests: login, session guard, seeding, attribution."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import User
from app.security import (
    require_user,
    hash_password,
    verify_password,
    parse_auth_users,
    seed_users,
)

# --- In-memory test DB ---

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def _fk(dbapi_conn, _):
    dbapi_conn.execute("PRAGMA foreign_keys = ON")


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
    yield
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=test_engine)


def _seed_user(username="alice", password="secret"):
    db = TestSession()
    try:
        db.add(User(username=username, password_hash=hash_password(password)))
        db.commit()
    finally:
        db.close()


# --- Unit: password hashing ---

def test_hash_and_verify_roundtrip():
    h = hash_password("hunter2")
    assert h != "hunter2"
    assert verify_password("hunter2", h)
    assert not verify_password("wrong", h)


def test_verify_handles_garbage_hash():
    assert not verify_password("x", "not-a-bcrypt-hash")


# --- Unit: parse_auth_users / seed_users ---

def test_parse_auth_users_tolerant():
    assert parse_auth_users("a:1, b:2 ,, bad, c:3") == [("a", "1"), ("b", "2"), ("c", "3")]


def test_parse_auth_users_empty():
    assert parse_auth_users("") == []


def test_seed_users_idempotent():
    db = TestSession()
    try:
        assert seed_users(db, "bob:pw,carol:pw") == 2
        assert seed_users(db, "bob:pw,carol:pw") == 0  # already exist
        assert db.query(User).count() == 2
    finally:
        db.close()


def test_seed_users_never_modifies_existing():
    db = TestSession()
    try:
        seed_users(db, "dave:original")
        original = db.query(User).filter(User.username == "dave").first().password_hash
        seed_users(db, "dave:changed")  # should be a no-op
        after = db.query(User).filter(User.username == "dave").first().password_hash
        assert original == after
        assert verify_password("original", after)
    finally:
        db.close()


# --- Integration: login flow ---

def test_login_bad_credentials_401():
    _seed_user()
    client = TestClient(app)
    app.dependency_overrides.pop(require_user, None)  # use real auth
    resp = client.post("/auth/login", json={"username": "alice", "password": "wrong"})
    assert resp.status_code == 401


def test_login_unknown_user_401():
    client = TestClient(app)
    app.dependency_overrides.pop(require_user, None)
    resp = client.post("/auth/login", json={"username": "ghost", "password": "x"})
    assert resp.status_code == 401


def test_login_success_then_me():
    _seed_user()
    client = TestClient(app)
    app.dependency_overrides.pop(require_user, None)
    resp = client.post("/auth/login", json={"username": "alice", "password": "secret"})
    assert resp.status_code == 200
    assert resp.json() == {"username": "alice"}
    # session cookie now set on the client → /auth/me works
    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json() == {"username": "alice"}


def test_me_401_without_session():
    client = TestClient(app)
    app.dependency_overrides.pop(require_user, None)
    assert client.get("/auth/me").status_code == 401


def test_logout_clears_session():
    _seed_user()
    client = TestClient(app)
    app.dependency_overrides.pop(require_user, None)
    client.post("/auth/login", json={"username": "alice", "password": "secret"})
    assert client.get("/auth/me").status_code == 200
    client.post("/auth/logout")
    assert client.get("/auth/me").status_code == 401


# --- Integration: data-router guard ---

def test_data_router_401_without_session():
    client = TestClient(app)
    app.dependency_overrides.pop(require_user, None)  # real guard
    assert client.get("/coupons").status_code == 401


def test_data_router_200_with_session():
    _seed_user()
    client = TestClient(app)
    app.dependency_overrides.pop(require_user, None)
    client.post("/auth/login", json={"username": "alice", "password": "secret"})
    assert client.get("/coupons").status_code == 200


# --- brute-force throttle + constant-time login ---

def test_repeated_failures_lock_out_then_429():
    from app.routers.auth import _throttle
    _throttle._failures.clear()
    _seed_user("bob", "goodpass")
    client = TestClient(app)

    for _ in range(_throttle._max):
        r = client.post("/auth/login", json={"username": "bob", "password": "bad"})
        assert r.status_code == 401

    # Once locked, the response no longer distinguishes a wrong password from a
    # right one — the correct password is refused too.
    assert client.post(
        "/auth/login", json={"username": "bob", "password": "bad"}
    ).status_code == 429
    assert client.post(
        "/auth/login", json={"username": "bob", "password": "goodpass"}
    ).status_code == 429
    _throttle._failures.clear()


def test_successful_login_clears_the_counter():
    from app.routers.auth import _throttle
    _throttle._failures.clear()
    _seed_user("carol", "goodpass")
    client = TestClient(app)

    client.post("/auth/login", json={"username": "carol", "password": "bad"})
    assert client.post(
        "/auth/login", json={"username": "carol", "password": "goodpass"}
    ).status_code == 200
    assert _throttle._failures == {}


def test_unknown_user_still_runs_a_hash_check():
    # Timing-oracle guard: a missing user must not short-circuit past bcrypt,
    # or response time reveals which usernames exist.
    import time as _t

    from app.security import verify_login

    start = _t.monotonic()
    assert verify_login("anything", None) is False
    assert _t.monotonic() - start > 0.01


def test_weak_seed_passwords_flags_the_obvious_ones():
    from app.security import weak_seed_passwords

    assert weak_seed_passwords("admin:admin") == ["admin"]        # common
    assert weak_seed_passwords("bob:bob") == ["bob"]              # equals username
    assert weak_seed_passwords("carol:short") == ["carol"]        # too short
    assert weak_seed_passwords("dave:cqR7-vv2N!ktPz") == []       # fine
    # Reports usernames only — this gets logged, so it must never leak secrets.
    assert "admin" not in str(weak_seed_passwords("x:admin"))
