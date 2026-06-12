"""Coupon accumulator + legs model tests (R1)."""
import io
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Coupon, Leg
from app.comparison import compute_value
from app.providers.base import MatchQuery, ExtractedRow, OCRProvider
from app.providers.mock_ocr import MockOCRProvider
from app.providers.mock_odds import MockOddsProvider

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
    yield
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture()
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def upload_client(tmp_path):
    app.state.ocr_provider = MockOCRProvider()
    from app import config as cfg
    original = cfg.settings.upload_dir
    cfg.settings.upload_dir = str(tmp_path)
    yield TestClient(app)
    cfg.settings.upload_dir = original


api = TestClient(app)


def _make_coupon(db, type_code="WO25", status="live", match_day=None):
    c = Coupon(
        name="Test Acca",
        type_code=type_code,
        match_day=match_day or date.today(),
        status=status,
    )
    db.add(c)
    db.flush()
    return c


# --- compute_value ---

def test_compute_value_positive():
    assert abs(compute_value(2.0, 1.80) - 0.1111) < 0.001

def test_compute_value_negative():
    assert abs(compute_value(1.80, 2.0) - (-0.10)) < 0.001

def test_compute_value_even():
    assert compute_value(2.0, 2.0) == 0.0


# --- MockOddsProvider ---

def test_mock_provider_returns_fair_odds():
    p = MockOddsProvider({("Arsenal vs Chelsea", "Over 2.5"): 1.78})
    r = p.get_fair_odds([MatchQuery(1, "Arsenal vs Chelsea", "Over 2.5")])
    assert r[0].fair_odds == 1.78

def test_mock_provider_null_for_unknown():
    p = MockOddsProvider()
    r = p.get_fair_odds([MatchQuery(1, "Unknown FC", "Win and Over 2.5")])
    assert r[0].fair_odds is None


# --- OCR provider ---

def test_mock_ocr_returns_rows():
    rows = MockOCRProvider().extract(b"fake", "image/png")
    assert len(rows) == 3
    assert all(isinstance(r, ExtractedRow) for r in rows)

def test_ocr_provider_is_abstract():
    with pytest.raises(TypeError):
        OCRProvider()


# --- Coupon model ---

def test_coupon_leg_count_property(db):
    c = _make_coupon(db)
    db.add(Leg(coupon_id=c.id, match_name="Arsenal", pick="Win", odds=1.85, rank=0))
    db.add(Leg(coupon_id=c.id, match_name="PSG", pick="HT/FT", odds=2.20, rank=1))
    db.commit()
    db.refresh(c)
    assert c.leg_count == 2

def test_coupon_cascade_deletes_legs(db):
    c = _make_coupon(db)
    db.add(Leg(coupon_id=c.id, match_name="Arsenal", pick="Win", odds=1.85, rank=0))
    db.commit()
    db.delete(c)
    db.commit()
    assert db.query(Leg).count() == 0


# --- GET /coupons ---

def test_list_coupons_empty():
    resp = api.get("/coupons")
    assert resp.status_code == 200
    assert resp.json() == []

def test_list_coupons_includes_leg_count(db):
    c = _make_coupon(db)
    db.add(Leg(coupon_id=c.id, match_name="Arsenal", pick="Win", odds=1.85, rank=0))
    db.commit()
    resp = api.get("/coupons")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["leg_count"] == 1
    assert body[0]["type_code"] == "WO25"
    assert body[0]["status"] == "live"

def test_list_coupons_ordered_by_match_day_desc(db):
    c1 = _make_coupon(db, match_day=date.today() - timedelta(days=3))
    c2 = _make_coupon(db, match_day=date.today())
    db.commit()
    resp = api.get("/coupons")
    ids = [c["id"] for c in resp.json()]
    assert ids[0] == c2.id


# --- GET /coupons/{id}/legs ---

def test_get_legs_sorted_by_value_desc(db):
    c = _make_coupon(db)
    db.add(Leg(coupon_id=c.id, match_name="Arsenal", pick="Win", odds=1.85, rank=0, value=0.05))
    db.add(Leg(coupon_id=c.id, match_name="PSG", pick="HT/FT", odds=2.20, rank=1, value=0.12))
    db.commit()
    resp = api.get(f"/coupons/{c.id}/legs")
    assert resp.status_code == 200
    legs = resp.json()
    assert legs[0]["match_name"] == "PSG"    # higher value first
    assert legs[1]["match_name"] == "Arsenal"

def test_get_legs_null_value_last(db):
    c = _make_coupon(db)
    db.add(Leg(coupon_id=c.id, match_name="A", pick="Win", odds=1.85, rank=0, value=None))
    db.add(Leg(coupon_id=c.id, match_name="B", pick="Win", odds=2.00, rank=1, value=0.05))
    db.commit()
    resp = api.get(f"/coupons/{c.id}/legs")
    assert resp.json()[0]["match_name"] == "B"

def test_get_legs_404_missing():
    resp = api.get("/coupons/9999/legs")
    assert resp.status_code == 404


# --- POST /upload ---

def test_upload_creates_pending_coupon(upload_client):
    resp = upload_client.post(
        "/upload",
        data={"type_code": "WO25", "match_day": date.today().isoformat()},
        files={"file": ("test.png", io.BytesIO(b"img"), "image/png")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "coupon_id" in body
    assert len(body["rows"]) == 3
    assert body["rows"][0]["pick"] == "Win and Over 2.5"

def test_upload_rejects_bad_mime(upload_client):
    resp = upload_client.post(
        "/upload",
        data={"type_code": "WO25", "match_day": date.today().isoformat()},
        files={"file": ("doc.pdf", io.BytesIO(b"data"), "application/pdf")},
    )
    assert resp.status_code == 400

def test_upload_auto_names_coupon(upload_client, db):
    resp = upload_client.post(
        "/upload",
        data={"type_code": "WO25", "match_day": date(2026, 6, 14).isoformat()},
        files={"file": ("t.png", io.BytesIO(b"img"), "image/png")},
    )
    coupon_id = resp.json()["coupon_id"]
    c = db.get(Coupon, coupon_id)
    assert c.name == "WO25 · Sun 14 Jun"


# --- POST /upload/{id}/confirm ---

def test_confirm_creates_legs_and_sets_live(upload_client, db):
    up = upload_client.post(
        "/upload",
        data={"type_code": "WO25", "match_day": date.today().isoformat()},
        files={"file": ("t.png", io.BytesIO(b"img"), "image/png")},
    )
    coupon_id = up.json()["coupon_id"]

    confirm = upload_client.post(
        f"/upload/{coupon_id}/confirm",
        json={"rows": [
            {"match": "Arsenal vs Chelsea", "pick": "Win and Over 2.5", "odds": 1.85},
            {"match": "PSG vs Lyon", "pick": "HT/FT PSG/PSG", "odds": 2.20},
        ]},
    )
    assert confirm.status_code == 200
    assert confirm.json()["legs_saved"] == 2

    db.expire_all()
    coupon = db.get(Coupon, coupon_id)
    assert coupon.status == "live"
    legs = db.query(Leg).filter(Leg.coupon_id == coupon_id).all()
    assert len(legs) == 2

def test_confirm_404_missing(upload_client):
    resp = upload_client.post(
        "/upload/9999/confirm",
        json={"rows": [{"match": "X", "pick": "Win", "odds": 1.5}]},
    )
    assert resp.status_code == 404


# --- POST /coupons/{id}/refresh ---

def test_refresh_updates_fair_odds_and_value(db):
    c = _make_coupon(db)
    db.add(Leg(coupon_id=c.id, match_name="Arsenal vs Chelsea", pick="Over 2.5", odds=2.0, rank=0))
    db.commit()

    app.state.odds_provider = MockOddsProvider({("Arsenal vs Chelsea", "Over 2.5"): 1.80})
    resp = api.post(f"/coupons/{c.id}/refresh")
    assert resp.status_code == 200
    leg = resp.json()[0]
    assert leg["fair_odds"] == 1.80
    assert abs(leg["value"] - 0.1111) < 0.001
    assert leg["fair_odds_updated_at"] is not None


def test_refresh_unmatched_pick_stays_null(db):
    c = _make_coupon(db)
    db.add(Leg(coupon_id=c.id, match_name="PSG vs Lyon", pick="HT/FT PSG/PSG", odds=2.2, rank=0))
    db.commit()

    app.state.odds_provider = MockOddsProvider()
    resp = api.post(f"/coupons/{c.id}/refresh")
    assert resp.status_code == 200
    leg = resp.json()[0]
    assert leg["fair_odds"] is None
    assert leg["value"] is None


def test_refresh_404_missing():
    app.state.odds_provider = MockOddsProvider()
    assert api.post("/coupons/9999/refresh").status_code == 404


# --- Auth guard ---

def test_upload_requires_auth(upload_client):
    from app.security import require_user
    app.dependency_overrides.pop(require_user, None)
    try:
        resp = upload_client.post(
            "/upload",
            data={"type_code": "WO25", "match_day": date.today().isoformat()},
            files={"file": ("t.png", io.BytesIO(b"img"), "image/png")},
        )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides[require_user] = lambda: "testuser"

def test_coupons_requires_auth():
    from app.security import require_user
    app.dependency_overrides.pop(require_user, None)
    try:
        assert api.get("/coupons").status_code == 401
    finally:
        app.dependency_overrides[require_user] = lambda: "testuser"
