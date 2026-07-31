"""Two ISSUES of one coupon type: coexisting, addressable, and superseded.

A type can carry more than one live issue — a GG Bonus midweek and a GG Bonus
weekend are two different bets off two different sheets — and each has to be
readable on its own. What must NOT coexist is two uploads of the same window:
there the newest photo is the coupon, and the one it replaces goes away
entirely (row + image), like the scheduled cleanup.
"""
import io
import os
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Coupon, Leg
from app.providers.mock_ocr import MockOCRProvider
from app.providers.mock_odds import MockOddsProvider

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def _set_fk_pragma(dbapi_conn, _):
    dbapi_conn.execute("PRAGMA foreign_keys = ON")


TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def _override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=test_engine)
    app.dependency_overrides[get_db] = _override_get_db
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
    app.state.session_factory = TestSession
    app.state.odds_provider = MockOddsProvider()
    from app import config as cfg
    original = cfg.settings.upload_dir
    cfg.settings.upload_dir = str(tmp_path)
    yield TestClient(app)
    cfg.settings.upload_dir = original


api = TestClient(app)


def _issue(db, period, start, end, fairs, type_code="GGB"):
    """A confirmed coupon with one unpriced-but-fair-odds leg per entry."""
    c = Coupon(type_code=type_code, period=period, match_day=start,
               period_ends=end, status="confirmed")
    db.add(c)
    db.flush()
    for i, fair in enumerate(fairs):
        db.add(Leg(coupon_id=c.id, match_name=f"{period} {i} v Opp {i}",
                   pick="GG Bonus", odds=None, fair_odds=fair, rank=i))
    db.commit()
    return c


def _ggb(db):
    """A live midweek issue and an upcoming weekend one — the reported case."""
    today = date.today()
    mid = _issue(db, "MIDWEEK", today - timedelta(days=2), today + timedelta(days=1),
                 [1.35, 1.40, 1.45, 1.50])
    wknd = _issue(db, "WEEKEND", today + timedelta(days=1), today + timedelta(days=3),
                  [1.60, 1.62, 1.70, 1.75])
    return mid, wknd


def _ggb_type():
    return next(t for t in api.get("/coupons/types").json() if t["code"] == "GGB")


# --- Issues coexist and each is readable on its own ---

def test_both_issues_are_listed(db):
    mid, wknd = _ggb(db)
    periods = {p["period"]: p for p in _ggb_type()["periods"]}
    assert set(periods) == {"MIDWEEK", "WEEKEND"}
    assert periods["MIDWEEK"]["status"] == "active"
    assert periods["WEEKEND"]["status"] == "upcoming"
    assert periods["MIDWEEK"]["coupon_id"] == mid.id
    assert periods["WEEKEND"]["coupon_id"] == wknd.id


def test_each_issue_carries_its_own_combination(db):
    """The live issue's value stays readable while a newer one exists.

    This is the bug: one figure per TYPE could only ever describe the newest
    upload, so the midweek combination — the one actually bettable — was
    unreachable the moment the weekend coupon landed.
    """
    _ggb(db)
    periods = {p["period"]: p for p in _ggb_type()["periods"]}
    mid_best = periods["MIDWEEK"]["best_combination"]
    wknd_best = periods["WEEKEND"]["best_combination"]

    assert mid_best["picks"] == 4 and wknd_best["picks"] == 4
    # Each is priced off its OWN legs, so the shorter midweek book is worth
    # far more than the weekend one — and both are visible at once.
    assert mid_best["fair"] == pytest.approx(4.1107, abs=1e-3)
    assert wknd_best["fair"] == pytest.approx(7.7112, abs=1e-3)
    assert mid_best["value"] > wknd_best["value"]


def test_a_combination_never_mixes_two_issues(db):
    """A fold is struck on one sheet; legs from two issues are not bettable."""
    _ggb(db)
    for p in _ggb_type()["periods"]:
        # Four legs per issue: an 8-fold could only come from mixing them.
        assert p["best_combination"]["picks"] <= 4


def test_selections_can_be_scoped_to_one_issue(db):
    mid, wknd = _ggb(db)
    everything = api.get("/coupons/types/GGB/selections").json()
    assert len({r["coupon_id"] for r in everything}) == 2

    just_weekend = api.get(
        f"/coupons/types/GGB/selections?coupon_id={wknd.id}"
    ).json()
    assert len(just_weekend) == 4
    assert {r["coupon_id"] for r in just_weekend} == {wknd.id}
    assert all(r["period_label"] == "Weekend" for r in just_weekend)

    just_midweek = api.get(
        f"/coupons/types/GGB/selections?coupon_id={mid.id}"
    ).json()
    assert {r["coupon_id"] for r in just_midweek} == {mid.id}


def test_scoping_to_another_types_issue_is_404(db):
    """A stale link must say so rather than read as an empty coupon."""
    tts = _issue(db, "MON_WED", date.today(), date.today(), [1.5], type_code="TTS2")
    assert api.get(f"/coupons/types/GGB/selections?coupon_id={tts.id}").status_code == 404
    assert api.get("/coupons/types/GGB/selections?coupon_id=9999").status_code == 404


# --- One issue per window: the newest upload is the coupon ---

def _upload(client, type_code, period):
    resp = client.post(
        "/upload",
        data={"type_code": type_code, "period": period},
        files={"file": ("c.png", io.BytesIO(b"img"), "image/png")},
    )
    assert resp.status_code == 200
    return resp.json()["coupon_id"]


def test_reupload_of_a_window_replaces_the_previous(upload_client, db):
    first = _upload(upload_client, "TTS2", "SAT")
    first_image = db.get(Coupon, first).image_path
    assert os.path.exists(first_image)

    second = _upload(upload_client, "TTS2", "SAT")

    assert db.get(Coupon, first) is None, "the replaced upload should be gone"
    assert db.get(Coupon, second) is not None
    # Row and image go together — never one without the other.
    assert not os.path.exists(first_image)
    # Its legs went with it, so the list is the new coupon's alone.
    assert db.query(Leg).filter(Leg.coupon_id == first).count() == 0


def test_a_different_window_of_the_same_type_survives(upload_client, db):
    """The whole point: two issues of one type still coexist."""
    saturday = _upload(upload_client, "TTS2", "SAT")
    sunday = _upload(upload_client, "TTS2", "SUN")
    assert db.get(Coupon, saturday) is not None
    assert db.get(Coupon, sunday) is not None


def test_a_different_type_in_the_same_window_survives(upload_client, db):
    tts = _upload(upload_client, "TTS2", "SAT")
    wo = _upload(upload_client, "WO25", "SAT")
    assert db.get(Coupon, tts) is not None
    assert db.get(Coupon, wo) is not None


def test_a_failed_scan_does_not_take_the_live_coupon_with_it(upload_client, db):
    """An old issue beats no issue — a bad photo must not delete a good coupon."""
    good = _upload(upload_client, "TTS2", "SAT")

    class BrokenOCR:
        def extract(self, *_args, **_kwargs):
            raise RuntimeError("unreadable photo")

    app.state.ocr_provider = BrokenOCR()
    try:
        failed = _upload(upload_client, "TTS2", "SAT")
    finally:
        app.state.ocr_provider = MockOCRProvider()

    assert db.get(Coupon, good) is not None, "a failed scan must not supersede"
    assert db.get(Coupon, failed).status == "failed"


def test_an_empty_scan_does_not_supersede(upload_client, db):
    """Rows read is the test, not status: a coupon with nothing on it is no
    replacement for one with fixtures."""
    good = _upload(upload_client, "TTS2", "SAT")

    class EmptyOCR:
        def extract(self, *_args, **_kwargs):
            from app.providers.base import ExtractedCoupon
            return ExtractedCoupon(rows=[])

    app.state.ocr_provider = EmptyOCR()
    try:
        empty = _upload(upload_client, "TTS2", "SAT")
    finally:
        app.state.ocr_provider = MockOCRProvider()

    assert db.get(Coupon, good) is not None
    assert db.get(Coupon, empty) is not None


def test_replacement_is_reported_on_the_notification(upload_client, db):
    sent = []

    class Notifier:
        def send(self, message):
            sent.append(message)

    app.state.notifier = Notifier()
    try:
        _upload(upload_client, "TTS2", "SAT")
        _upload(upload_client, "TTS2", "SAT")
    finally:
        app.state.notifier = None

    assert "Replaced" not in sent[0]
    assert "Replaced the previous Saturday upload." in sent[1]
