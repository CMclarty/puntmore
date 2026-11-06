"""The scheduled coupon cleanup: rows AND image files, together, after
`Settings.coupon_cleanup_days` (PLAN.md item 4, AGENTS.md "coupon cleanup")."""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base
from app.models import Coupon
from app.scheduler import cleanup_old_coupons
from app.time_utils import utcnow

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)


@event.listens_for(engine, "connect")
def _fk(dbapi_conn, _):
    dbapi_conn.execute("PRAGMA foreign_keys = ON")


Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture()
def db():
    Base.metadata.create_all(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        Base.metadata.drop_all(bind=engine)


def _coupon(db, days_old, image=None):
    c = Coupon(type_code="WO25", period="Sat 1 Nov", match_day=date(2026, 11, 1),
               period_ends=date(2026, 11, 1), status="confirmed",
               image_path=str(image) if image else None)
    db.add(c)
    db.flush()
    c.created_at = utcnow() - timedelta(days=days_old)
    db.commit()
    return c.id


class Notes:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)


def test_default_is_eight_days():
    assert settings.coupon_cleanup_days == 8


def test_rows_and_their_photos_go_together_after_the_window(db, tmp_path):
    old_img, new_img = tmp_path / "old.jpg", tmp_path / "new.jpg"
    old_img.write_bytes(b"x")
    new_img.write_bytes(b"y")
    old = _coupon(db, days_old=9, image=old_img)
    edge = _coupon(db, days_old=7)                      # inside the window: stays
    new = _coupon(db, days_old=1, image=new_img)
    notes = Notes()
    assert cleanup_old_coupons(db, notifier=notes) == 1
    assert [c.id for c in db.query(Coupon).order_by(Coupon.id)] == [edge, new]
    assert not old_img.exists() and new_img.exists()
    assert notes.sent and "8 days" in notes.sent[0] and "1 image file" in notes.sent[0]
    assert old not in [c.id for c in db.query(Coupon)]


def test_a_photo_shared_by_siblings_goes_with_the_last_of_them(db, tmp_path):
    """One photo of a sheet backs a coupon per market block; the file goes with
    the last row pointing at it (coupon_images.delete_coupon_image)."""
    img = tmp_path / "sheet.jpg"
    img.write_bytes(b"x")
    _coupon(db, days_old=20, image=img)
    _coupon(db, days_old=20, image=img)
    assert cleanup_old_coupons(db) == 2
    assert not img.exists()


def test_a_shared_photo_stays_while_a_live_sibling_still_points_at_it(db, tmp_path):
    img = tmp_path / "sheet.jpg"
    img.write_bytes(b"x")
    _coupon(db, days_old=20, image=img)
    _coupon(db, days_old=2, image=img)
    assert cleanup_old_coupons(db) == 1
    assert img.exists()


def test_the_window_is_a_setting(db, monkeypatch):
    _coupon(db, days_old=5)
    monkeypatch.setattr(settings, "coupon_cleanup_days", 3)
    assert cleanup_old_coupons(db) == 1
