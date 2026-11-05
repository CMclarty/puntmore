"""Upload a SHEET, not a market — see coupon_types.UPLOAD_SHEETS.

William Hill prints its priced markets two sheets at a time, and one photo of
either fills every market on it. The form offers "Win And coupon" and "Team To
Score" instead of asking which of six look-alike markets the photo is "of".
"""
import io

import pytest
from fastapi.testclient import TestClient

from app import coupon_types
from app.coupon_types import SHEET_BY_CODE, UPLOAD_SHEETS, resolve_upload, upload_options
from app.main import app
from app.models import Coupon
from app.providers.base import ExtractedCoupon, ExtractedRow, OCRProvider
from app.providers.claude_ocr import normalise_board_label
from app.providers.mock_odds import MockOddsProvider

from tests.test_grid_ingest import TestSession, setup_db, db  # noqa: F401  (fixtures)

W2, W3, WTN = "WIN & 2 OR MORE GOALS", "WIN & 3 OR MORE GOALS", "WIN TO NIL"
T1, T2, T3 = ("TEAM TO SCORE 1 OR MORE GOALS", "TEAM TO SCORE 2 OR MORE GOALS",
              "TEAM TO SCORE 3 OR MORE GOALS")


class SheetOCR(OCRProvider):
    def __init__(self, blocks):
        self.blocks = blocks          # {heading: (left, right)} per row

    def extract(self, image_bytes, media_type, hint=None):
        rows = [
            ExtractedRow(match, None, None, ko, prices={
                normalise_board_label(h): pair for h, pair in self.blocks.items()
            })
            for match, ko in (("Kairat v Omonia Nicosia", "SAT 15:00"),
                              ("Mariehamn v Lahti", "SAT 17:00"))
        ]
        return ExtractedCoupon(rows=rows, title="MATCH WINNER", period_text="Sat")


@pytest.fixture()
def client_for(tmp_path):
    from app import config as cfg
    original = cfg.settings.upload_dir
    cfg.settings.upload_dir = str(tmp_path)
    app.state.session_factory = TestSession
    app.state.odds_provider = MockOddsProvider()
    app.state.notifier = None

    def make(blocks):
        app.state.ocr_provider = SheetOCR(blocks)
        return TestClient(app)
    yield make
    cfg.settings.upload_dir = original
    app.state.ocr_provider = None


def _upload(client, code):
    resp = client.post(
        "/upload", data={"type_code": code, "period": "SAT"},
        files={"file": ("sheet.png", io.BytesIO(b"img"), "image/png")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["coupon_id"]


# --- what the form offers -------------------------------------------------------

def test_the_form_offers_two_sheets_in_place_of_six_markets():
    codes = [c for c, _, _ in upload_options()]
    labels = [l for _, l, _ in upload_options()]
    assert codes[:2] == ["SHEET_WIN_AND", "SHEET_TTS"]
    assert "Win And coupon" in labels and "Team To Score" in labels
    for single in ("TTS1", "TTS2", "TTS3", "WO15", "WO25", "WTN"):
        assert single not in codes, single


def test_the_single_market_sheets_are_still_offered():
    codes = [c for c, _, _ in upload_options()]
    for own_sheet in ("GGB", "GGND", "HILLS", "FREDB"):
        assert own_sheet in codes


def test_every_type_on_a_sheet_is_a_real_priced_grid_type():
    for sheet in UPLOAD_SHEETS:
        for code in sheet.types:
            t = coupon_types.get(code)
            assert t is not None and t.board_label and t.two_sided


def test_the_endpoint_and_the_periods_accept_a_sheet(client_for):
    c = client_for({})
    opts = c.get("/coupons/upload-options").json()
    win_and = next(o for o in opts if o["code"] == "SHEET_WIN_AND")
    assert win_and["types"] == ["WO15", "WO25", "WTN"]
    assert c.get("/coupons/periods?type_code=SHEET_TTS").status_code == 200


def test_resolve_upload():
    t, sheet = resolve_upload("SHEET_TTS")
    assert t.code == "TTS1" and sheet is SHEET_BY_CODE["SHEET_TTS"]
    t, sheet = resolve_upload("GGB")
    assert t.code == "GGB" and sheet is None
    assert resolve_upload("NOPE") == (None, None)


# --- one photo, every market ---------------------------------------------------

def test_one_win_and_photo_fills_all_three_markets(client_for, db):
    c = client_for({W2: (3.0, 9.0), W3: (5.0, 15.0), WTN: (4.0, 11.0)})
    cid = _upload(c, "SHEET_WIN_AND")
    db.expire_all()
    types = sorted(x.type_code for x in db.query(Coupon).all())
    assert types == ["WO15", "WO25", "WTN"]
    assert db.get(Coupon, cid).type_code == "WO15"
    assert db.get(Coupon, cid).status == "confirmed"


def test_one_team_to_score_photo_fills_all_three_markets(client_for, db):
    c = client_for({T1: (1.3, 4.5), T2: (2.5, 8.0), T3: (6.0, 21.0)})
    _upload(c, "SHEET_TTS")
    db.expire_all()
    assert sorted(x.type_code for x in db.query(Coupon).all()) == ["TTS1", "TTS2", "TTS3"]


def test_the_upload_becomes_the_first_market_that_actually_read(client_for, db):
    """Win & 2 unreadable everywhere: the upload must not become an EMPTY
    Win and Over 1.5 coupon — it becomes the first market that did read."""
    c = client_for({W2: (None, None), W3: (5.0, 15.0), WTN: (4.0, 11.0)})
    cid = _upload(c, "SHEET_WIN_AND")
    db.expire_all()
    assert db.get(Coupon, cid).type_code == "WO25"
    assert sorted(x.type_code for x in db.query(Coupon).all()) == ["WO25", "WTN"]
    assert all(x.legs for x in db.query(Coupon).all())


def test_a_photo_of_the_other_sheet_fails_and_says_so(client_for, db):
    """Team To Score blocks uploaded as the Win And sheet: nothing of the sheet
    was read, so the upload fails with a reason rather than saving empty."""
    c = client_for({T1: (1.3, 4.5), T2: (2.5, 8.0)})
    cid = _upload(c, "SHEET_WIN_AND")
    db.expire_all()
    coupon = db.get(Coupon, cid)
    assert coupon.status == "failed"
    assert "Win And coupon" in coupon.scan_audit
    assert not coupon.legs


def test_a_single_market_type_still_uploads_as_before(client_for, db):
    c = client_for({T1: (1.3, 4.5), T2: (2.5, 8.0), T3: (6.0, 21.0)})
    cid = _upload(c, "TTS2")          # an old client posting a type still works
    db.expire_all()
    assert db.get(Coupon, cid).type_code == "TTS2"
    assert sorted(x.type_code for x in db.query(Coupon).all()) == ["TTS1", "TTS2", "TTS3"]
