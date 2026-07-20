"""Tests for GET /state — whole-app snapshot behind a static API key."""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import Coupon, Leg

TEST_DB_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def set_fk_pragma(dbapi_conn, _):
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
    app.state.arbs_client = None
    app.state.cards_client = None
    yield
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setattr(settings, "state_api_key", "test-key-123")
    return "test-key-123"


client = TestClient(app)


# --- Auth ---

def test_state_disabled_without_configured_key():
    # settings.state_api_key is "" by default → endpoint disabled
    assert client.get("/state").status_code == 503
    assert client.get("/state", headers={"X-API-Key": "anything"}).status_code == 503


def test_state_rejects_missing_or_wrong_key(api_key):
    assert client.get("/state").status_code == 401
    assert client.get("/state", headers={"X-API-Key": "wrong"}).status_code == 401


def test_state_no_session_required(api_key):
    # Unlike the UI routers, a valid API key alone is enough (no cookie).
    from app.security import require_user
    app.dependency_overrides.pop(require_user, None)
    try:
        resp = client.get("/state", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
    finally:
        app.dependency_overrides[require_user] = lambda: "testuser"


# --- Payload ---

def test_state_shape_when_empty(api_key):
    body = client.get("/state", headers={"X-API-Key": api_key}).json()
    assert set(body) == {"generated_at", "coupons", "accabonus", "cards"}
    assert body["coupons"] == []
    assert body["accabonus"] == []
    assert body["cards"] == []


def test_state_includes_coupons_with_legs_sorted_by_value(api_key):
    db = TestSession()
    coupon = Coupon(name="Saturday Acca", type_code="TTS2", match_day=date(2026, 6, 13), status="confirmed")
    coupon.legs = [
        Leg(match_name="PSG v Lyon", pick="PSG HT/FT", odds=2.30, value=0.02, rank=0),
        Leg(match_name="Bayern v Leipzig", pick="BTTS — Yes", odds=1.72, value=0.055, rank=1),
        Leg(match_name="Porto v Benfica", pick="Draw", odds=3.20, value=None, rank=2),
    ]
    db.add(coupon)
    db.commit()
    db.close()

    body = client.get("/state", headers={"X-API-Key": api_key}).json()
    assert len(body["coupons"]) == 1
    c = body["coupons"][0]
    assert c["name"] == "Saturday Acca"
    assert c["leg_count"] == 3
    # Legs sorted best value first, None last
    values = [leg["value"] for leg in c["legs"]]
    assert values == [0.055, 0.02, None]
