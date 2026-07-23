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
    user_permissions,
    hash_password,
    verify_password,
    parse_auth_users,
    seed_users,
    has_permission,
    granted_tools,
)


def _use_real_auth():
    """Drop the conftest bypass so a test exercises the real session guard and
    the real per-tool authorization (both seams are overridden by default)."""
    app.dependency_overrides.pop(require_user, None)
    app.dependency_overrides.pop(user_permissions, None)

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


def _seed_user(username="alice", password="secret", permissions="*"):
    db = TestSession()
    try:
        db.add(User(
            username=username,
            password_hash=hash_password(password),
            permissions=permissions,
        ))
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
    # Two-field entries default to the "*" (all tools) grant.
    assert parse_auth_users("a:1, b:2 ,, bad, c:3") == [
        ("a", "1", "*"), ("b", "2", "*"), ("c", "3", "*"),
    ]


def test_parse_auth_users_with_grants():
    assert parse_auth_users("admin:pw:*, dave:pw:coupons, sam:pw:coupons+accabonus") == [
        ("admin", "pw", "*"),
        ("dave", "pw", "coupons"),
        ("sam", "pw", "coupons+accabonus"),
    ]


def test_parse_auth_users_empty():
    assert parse_auth_users("") == []


def test_seed_users_idempotent():
    db = TestSession()
    try:
        assert seed_users(db, "bob:pw,carol:pw") == (2, [])
        assert seed_users(db, "bob:pw,carol:pw") == (0, [])  # already exist
        assert db.query(User).count() == 2
    finally:
        db.close()


def test_seed_users_never_modifies_an_existing_password():
    db = TestSession()
    try:
        seed_users(db, "dave:original")
        original = db.query(User).filter(User.username == "dave").first().password_hash
        seed_users(db, "dave:changed")  # password change must be a no-op
        after = db.query(User).filter(User.username == "dave").first().password_hash
        assert original == after
        assert verify_password("original", after)
    finally:
        db.close()


def test_seed_users_reconciles_permissions_of_existing_users():
    # AUTH_USERS is the only lever in production — the database sits on a disk
    # with no SQL shell — so a grant edit must take effect on restart.
    db = TestSession()
    try:
        seed_users(db, "erin:pw:coupons")
        created, changed = seed_users(db, "erin:pw:coupons+pnl")
        assert created == 0
        assert changed == [("erin", "coupons", "coupons+pnl")]
        assert db.query(User).filter(User.username == "erin").first().permissions == "coupons+pnl"
    finally:
        db.close()


def test_seed_users_reports_no_change_when_the_grant_matches():
    db = TestSession()
    try:
        seed_users(db, "frank:pw:coupons")
        assert seed_users(db, "frank:pw:coupons") == (0, [])
    finally:
        db.close()


def test_seed_users_can_narrow_a_grant_too():
    # Reconciliation is not "widen only" — removing a tool from AUTH_USERS
    # revokes it, so the env var is genuinely the source of truth.
    db = TestSession()
    try:
        seed_users(db, "gina:pw:*")
        _, changed = seed_users(db, "gina:pw:coupons")
        assert changed == [("gina", "*", "coupons")]
        assert db.query(User).filter(User.username == "gina").first().permissions == "coupons"
    finally:
        db.close()


def test_seed_users_leaves_users_absent_from_auth_users_alone():
    # A user not named in AUTH_USERS is untouched, not deleted or reset.
    db = TestSession()
    try:
        seed_users(db, "hank:pw:coupons")
        seed_users(db, "iris:pw:pnl")
        assert db.query(User).filter(User.username == "hank").first().permissions == "coupons"
        assert db.query(User).count() == 2
    finally:
        db.close()


# --- Integration: login flow ---

def test_login_bad_credentials_401():
    _seed_user()
    client = TestClient(app)
    _use_real_auth()
    resp = client.post("/auth/login", json={"username": "alice", "password": "wrong"})
    assert resp.status_code == 401


def test_login_unknown_user_401():
    client = TestClient(app)
    _use_real_auth()
    resp = client.post("/auth/login", json={"username": "ghost", "password": "x"})
    assert resp.status_code == 401


def test_login_success_then_me():
    _seed_user()
    client = TestClient(app)
    _use_real_auth()
    resp = client.post("/auth/login", json={"username": "alice", "password": "secret"})
    assert resp.status_code == 200
    # alice was seeded with the "*" grant; both tool flags are on in tests, so
    # she resolves to every tool.
    expected = {
        "username": "alice",
        "permissions": "*",
        "tools": ["coupons", "accabonus", "cards", "goalscorers", "pnl"],
    }
    assert resp.json() == expected
    # session cookie now set on the client → /auth/me works
    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json() == expected


def test_me_401_without_session():
    client = TestClient(app)
    _use_real_auth()
    assert client.get("/auth/me").status_code == 401


def test_logout_clears_session():
    _seed_user()
    client = TestClient(app)
    _use_real_auth()
    client.post("/auth/login", json={"username": "alice", "password": "secret"})
    assert client.get("/auth/me").status_code == 200
    client.post("/auth/logout")
    assert client.get("/auth/me").status_code == 401


# --- Integration: data-router guard ---

def test_data_router_401_without_session():
    client = TestClient(app)
    _use_real_auth()
    assert client.get("/coupons").status_code == 401


def test_data_router_200_with_session():
    _seed_user()
    client = TestClient(app)
    _use_real_auth()
    client.post("/auth/login", json={"username": "alice", "password": "secret"})
    assert client.get("/coupons").status_code == 200


# --- Split auth: per-tool authorization ---

def test_coupons_grant_admits_coupons_router():
    _seed_user("dave", "couponpass", permissions="coupons")
    client = TestClient(app)
    _use_real_auth()
    client.post("/auth/login", json={"username": "dave", "password": "couponpass"})
    assert client.get("/coupons").status_code == 200


def test_non_coupons_grant_is_403_on_coupons_router():
    # A logged-in user without the coupons grant is authenticated but forbidden:
    # 403, not 401 — the session is valid, the permission is not.
    _seed_user("sam", "accapass", permissions="accabonus")
    client = TestClient(app)
    _use_real_auth()
    client.post("/auth/login", json={"username": "sam", "password": "accapass"})
    assert client.get("/coupons").status_code == 403


def test_me_reports_only_granted_and_enabled_tools():
    _seed_user("sam", "accapass", permissions="coupons+accabonus")
    client = TestClient(app)
    _use_real_auth()
    client.post("/auth/login", json={"username": "sam", "password": "accapass"})
    body = client.get("/auth/me").json()
    assert body["permissions"] == "coupons+accabonus"
    assert body["tools"] == ["coupons", "accabonus"]  # cards not granted


def test_seed_users_persists_the_grant():
    db = TestSession()
    try:
        seed_users(db, "admin:pw:*, dave:pw:coupons")
        admin = db.query(User).filter(User.username == "admin").first()
        dave = db.query(User).filter(User.username == "dave").first()
        assert admin.permissions == "*"
        assert dave.permissions == "coupons"
    finally:
        db.close()


# --- Unit: permission helpers ---

def test_has_permission_wildcard_and_explicit():
    assert has_permission("*", "coupons")
    assert has_permission("*", "anything-future")
    assert has_permission("coupons+cards", "cards")
    assert not has_permission("coupons", "accabonus")
    assert not has_permission("", "coupons")


def test_granted_tools_intersects_grant_with_enabled(monkeypatch):
    # Both flags on in the test env, so "*" yields every tool in nav order.
    assert granted_tools("*") == ["coupons", "accabonus", "cards", "goalscorers", "pnl"]
    assert granted_tools("coupons") == ["coupons"]
    # A grant for a disabled tool yields nothing for it.
    from app import security
    monkeypatch.setattr(security.settings, "enable_cards", False)
    assert granted_tools("coupons+cards") == ["coupons"]


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
