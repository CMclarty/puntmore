"""One photo of a sheet, every market on it.

A William Hill sheet prints six markets over one fixture list — six look-alike
column blocks side by side. Asking the model to find one block and ignore the
rest made it responsible for a choice it could get wrong invisibly; asking it
which team a price belonged to did the same. Neither is asked of it now: it
transcribes the whole grid keyed by each block's printed heading, the software
matches the heading it wants and assigns the sides by print order — and since
every block has been read anyway, each one becomes its own coupon.
"""
import io
import os
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Coupon, Leg
from app.providers.base import ExtractedCoupon, ExtractedRow, OCRProvider
from app.providers.claude_ocr import normalise_board_label, parse_rows
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


# The three blocks a test sheet carries, as they are printed.
TTS1 = "TEAM TO SCORE 1 OR MORE GOALS"
TTS2 = "TEAM TO SCORE 2 OR MORE GOALS"
WO15 = "WIN & 2 OR MORE GOALS"


class GridOCR(OCRProvider):
    """A sheet read whole: two fixtures, three market blocks on each row."""

    def extract(self, image_bytes, media_type, hint=None):
        return ExtractedCoupon(
            rows=[
                ExtractedRow(
                    "Kairat v Omonia Nicosia", None, None, "SAT 15:00",
                    prices={
                        normalise_board_label(TTS1): (1.30, 4.50),
                        normalise_board_label(TTS2): (2.50, 8.00),
                        normalise_board_label(WO15): (3.00, 9.00),
                    },
                ),
                ExtractedRow(
                    "Mariehamn v Lahti", None, None, "SAT 17:00",
                    prices={
                        normalise_board_label(TTS1): (1.40, 3.75),
                        normalise_board_label(TTS2): (2.75, 7.00),
                        normalise_board_label(WO15): (3.50, 8.50),
                    },
                ),
            ],
            title="TEST SHEET",
            period_text="Saturday 1st August",
        )


@pytest.fixture()
def grid_client(tmp_path):
    app.state.ocr_provider = GridOCR()
    app.state.session_factory = TestSession
    app.state.odds_provider = MockOddsProvider()
    app.state.notifier = None
    from app import config as cfg
    original = cfg.settings.upload_dir
    cfg.settings.upload_dir = str(tmp_path)
    yield TestClient(app)
    cfg.settings.upload_dir = original
    app.state.ocr_provider = None


def _upload(client, type_code="TTS2", period="SAT"):
    resp = client.post(
        "/upload",
        data={"type_code": type_code, "period": period},
        files={"file": ("sheet.png", io.BytesIO(b"img"), "image/png")},
    )
    assert resp.status_code == 200
    return resp.json()["coupon_id"]


# --- Heading matching happens in code, not in the model ---------------------

def test_headings_normalise_to_one_key():
    """The heading is transcribed from a photo, so its case, spacing and the
    &/and spelling all vary between reads of the same sheet."""
    assert normalise_board_label("WIN & 2 OR MORE GOALS") \
        == normalise_board_label("Win and 2 or more goals") \
        == normalise_board_label("  WIN &  2 OR MORE  GOALS ")
    assert normalise_board_label(None) == ""


def test_a_row_carries_every_block_it_was_printed_with():
    rows = parse_rows({"selections": [
        {"match": "A v B", "kickoff": "SAT 15:00",
         "prices": {TTS1: ["1-3", "7-2"], TTS2: ["6-4", "8-1"]}},
    ]})
    assert set(rows[0].prices) == {
        normalise_board_label(TTS1), normalise_board_label(TTS2),
    }
    assert rows[0].prices[normalise_board_label(TTS1)] == (1.3333, 4.5)


def test_an_unreadable_block_is_absent_rather_than_empty():
    """Absent reads as "not on this sheet", which is the honest answer."""
    rows = parse_rows({"selections": [
        {"match": "A v B", "prices": {TTS1: [None, None], TTS2: ["6-4", "8-1"]}},
    ]})
    assert normalise_board_label(TTS1) not in rows[0].prices
    assert normalise_board_label(TTS2) in rows[0].prices


def test_a_type_takes_only_its_own_block():
    from app.coupon_types import BY_CODE
    from app.routers.upload import _pair_for

    row = parse_rows({"selections": [
        {"match": "A v B", "prices": {TTS1: ["1-3", "7-2"], TTS2: ["6-4", "8-1"]}},
    ]})[0]
    assert _pair_for(row, BY_CODE["TTS1"]) == (1.3333, 4.5)
    assert _pair_for(row, BY_CODE["TTS2"]) == (2.5, 9.0)


def test_a_block_missing_from_the_sheet_is_never_filled_from_a_neighbour():
    """The failure this replaces: a price read out of the column next door
    looks entirely plausible and is wrong."""
    from app.coupon_types import BY_CODE
    from app.routers.upload import _pair_for

    row = parse_rows({"selections": [
        {"match": "A v B", "prices": {TTS1: ["1-3", "7-2"]}},
    ]})[0]
    assert _pair_for(row, BY_CODE["TTS3"]) == (None, None)


# --- One photo fills every market on the sheet ------------------------------

def test_one_upload_creates_a_coupon_per_block(grid_client, db):
    primary = _upload(grid_client, "TTS2")
    coupons = {c.type_code: c for c in db.query(Coupon).all()}
    assert set(coupons) == {"TTS1", "TTS2", "WO15"}
    assert coupons["TTS2"].id == primary
    for c in coupons.values():
        assert c.status == "confirmed"
        # Two fixtures, two sides each.
        assert db.query(Leg).filter(Leg.coupon_id == c.id).count() == 4


def test_each_coupon_gets_its_own_blocks_prices(grid_client, db):
    _upload(grid_client, "TTS2")
    for code, (home, away) in (("TTS1", (1.30, 4.50)),
                               ("TTS2", (2.50, 8.00)),
                               ("WO15", (3.00, 9.00))):
        coupon = db.query(Coupon).filter(Coupon.type_code == code).one()
        legs = db.query(Leg).filter(Leg.coupon_id == coupon.id).order_by(Leg.rank).all()
        kairat = next(l for l in legs if l.pick.startswith("Kairat"))
        omonia = next(l for l in legs if l.pick.startswith("Omonia"))
        assert (kairat.odds, omonia.odds) == (home, away)


def test_the_left_price_lands_on_the_team_named_first(grid_client, db):
    _upload(grid_client, "TTS2")
    coupon = db.query(Coupon).filter(Coupon.type_code == "TTS1").one()
    legs = db.query(Leg).filter(Leg.coupon_id == coupon.id).order_by(Leg.rank).all()
    assert legs[0].pick == "Kairat to Score 1" and legs[0].odds == 1.30
    assert legs[1].pick == "Omonia Nicosia to Score 1" and legs[1].odds == 4.50


def test_every_coupon_off_one_sheet_shares_the_image(grid_client, db):
    _upload(grid_client, "TTS2")
    paths = {c.image_path for c in db.query(Coupon).all()}
    assert len(paths) == 1, "one photo, one stored file"
    assert os.path.exists(paths.pop())


def test_a_block_the_sheet_does_not_carry_makes_no_coupon(grid_client, db):
    _upload(grid_client, "TTS2")
    assert db.query(Coupon).filter(Coupon.type_code == "TTS3").count() == 0


def test_siblings_are_only_made_for_types_that_run_the_period(grid_client, db):
    """GG types run MIDWEEK/WEEKEND only, so a Saturday sheet cannot create
    one even if a heading somehow matched."""
    from app.routers.upload import _types_on_sheet
    extracted = GridOCR().extract(b"", "image/png")
    codes = {t.code for t in _types_on_sheet(extracted, "SAT")}
    assert codes == {"TTS1", "TTS2", "WO15"}
    assert _types_on_sheet(extracted, "MIDWEEK") == []


def test_reupload_replaces_every_coupon_off_the_sheet(grid_client, db):
    _upload(grid_client, "TTS2")
    first = {c.type_code: c.id for c in db.query(Coupon).all()}

    _upload(grid_client, "TTS2")
    db.expire_all()
    second = {c.type_code: c.id for c in db.query(Coupon).all()}
    assert set(second) == set(first)
    # Every block's coupon is the new sheet's, not the old one's.
    assert all(second[code] != first[code] for code in first)
    assert db.query(Coupon).count() == 3


def test_the_shared_image_survives_until_the_last_coupon_goes(grid_client, db):
    """Row and file go together — but the file belongs to the LAST row that
    points at it, or re-scan breaks on every sibling."""
    from app.coupon_images import delete_coupon_image

    _upload(grid_client, "TTS2")
    coupons = db.query(Coupon).all()
    path = coupons[0].image_path

    assert delete_coupon_image(db, coupons[0]) is False
    assert os.path.exists(path), "still referenced by the other two"
    db.delete(coupons[0])
    db.flush()

    assert delete_coupon_image(db, coupons[1]) is False
    assert os.path.exists(path)
    db.delete(coupons[1])
    db.flush()

    assert delete_coupon_image(db, coupons[2]) is True
    assert not os.path.exists(path), "last reference gone, file goes with it"


def test_the_daily_cleanup_removes_a_whole_sheet_and_its_image(grid_client, db):
    from datetime import timedelta
    from app.scheduler import cleanup_old_coupons
    from app.time_utils import utcnow

    _upload(grid_client, "TTS2")
    path = db.query(Coupon).first().image_path
    for c in db.query(Coupon).all():
        c.created_at = utcnow() - timedelta(days=40)
    db.commit()

    assert cleanup_old_coupons(db, cutoff_days=30) == 3
    assert db.query(Coupon).count() == 0
    assert not os.path.exists(path), "the sheet's image goes with its last row"


def test_a_single_block_response_still_works(grid_client, db):
    """A coupon photographed as one market, or an older-shaped response: the
    flat pair is used and no siblings are invented."""
    class SingleBlockOCR(OCRProvider):
        def extract(self, image_bytes, media_type, hint=None):
            return ExtractedCoupon(rows=[
                ExtractedRow("Kairat v Omonia Nicosia", None, None, "SAT 15:00",
                             home_odds=1.30, away_odds=4.50),
            ])

    app.state.ocr_provider = SingleBlockOCR()
    coupon_id = _upload(grid_client, "TTS2")
    assert db.query(Coupon).count() == 1
    legs = db.query(Leg).filter(Leg.coupon_id == coupon_id).order_by(Leg.rank).all()
    assert [l.odds for l in legs] == [1.30, 4.50]
