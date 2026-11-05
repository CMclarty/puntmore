"""Fred Bundles — a sheet of ready-made win accumulators.

A third coupon shape, and the first that is not a fixture list. Instead of one
list of fixtures under a market, the sheet prints a handful of pre-built accas,
each with its own few selections and ONE price for the whole group.

Two consequences drive everything here:

  * VALUE BELONGS TO THE GROUP. The sheet prices the bundle, not the selection,
    so a leg carries no odds of its own and a per-row edge is meaningless. The
    figure that matters is the printed acca price against the product of its
    legs' fair odds — the ladder coupons' arithmetic, applied to a group the
    coupon chose rather than to the N shortest prices on the sheet.
  * IT IS NOT A RECTANGLE. The grid rules must not run on it. In particular the
    same fixture may legitimately appear in more than one bundle, which the
    one-line-per-match rule would treat as the read losing its place.
"""
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.coupon_types import bundles_on, get
from app.database import Base, get_db
from app.main import app
from app.models import Coupon, Leg
from app.providers.base import ExtractedCoupon, ExtractedRow, OCRProvider
from app.providers.claude_ocr import parse_rows
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


# A two-bundle sheet. Kairat appears in BOTH — real behaviour on a bundle
# sheet, and the case the grid's duplicate rule would destroy.
SHEET = [
    ("BUNDLE 1", 6.0, "Kairat v Omonia Nicosia", "Kairat", "SAT 15:00"),
    ("BUNDLE 1", 6.0, "Mariehamn v Lahti", "Mariehamn", "SAT 17:00"),
    ("BUNDLE 2", 15.0, "Kairat v Omonia Nicosia", "Kairat", "SAT 15:00"),
    ("BUNDLE 2", 15.0, "Arsenal v Leeds", "Arsenal", "SAT 15:00"),
    ("BUNDLE 2", 15.0, "Ajax v PSV Eindhoven", "Ajax", "SAT 19:00"),
]


class BundleOCR(OCRProvider):
    def extract(self, image_bytes, media_type, hint=None):
        return ExtractedCoupon(
            rows=[
                ExtractedRow(
                    match=match, market=team, kickoff_text=ko,
                    bundle=name, bundle_odds=price,
                )
                for name, price, match, team, ko in SHEET
            ],
            title="FRED BUNDLES",
            period_text="Saturday 22nd August",
        )


# Fair odds keyed by (match_name, pick) — MockOddsProvider's contract.
FAIR = {
    ("Kairat v Omonia Nicosia", "Kairat Win"): 2.0,
    ("Mariehamn v Lahti", "Mariehamn Win"): 2.5,
    ("Arsenal v Leeds", "Arsenal Win"): 1.5,
    ("Ajax v PSV Eindhoven", "Ajax Win"): 2.0,
}


@pytest.fixture()
def bundle_client(tmp_path):
    app.state.ocr_provider = BundleOCR()
    app.state.session_factory = TestSession
    app.state.odds_provider = MockOddsProvider(FAIR)
    app.state.notifier = None
    from app import config as cfg
    original = cfg.settings.upload_dir
    cfg.settings.upload_dir = str(tmp_path)
    yield TestClient(app)
    cfg.settings.upload_dir = original
    app.state.ocr_provider = None


def _upload(client, period="WEEKEND"):
    resp = client.post(
        "/upload",
        data={"type_code": "FREDB", "period": period},
        files={"file": ("sheet.png", io.BytesIO(b"img"), "image/png")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["coupon_id"]


# --- the type ---

def test_fred_bundles_prices_the_bundle_not_the_selection():
    """prices_each_selection is False on purpose.

    Any per-row number would be invented: the sheet prints one price for the
    whole acca and none against the fixtures inside it.
    """
    t = get("FREDB")
    assert t.bundles is True
    assert t.prices_each_selection is False
    assert t.pays_ladder is False
    assert t.selection == "{team} Win"
    # Betfred's moving midweek/weekend issues, like the GG coupons.
    assert t.period_codes == ("MIDWEEK", "WEEKEND")


# --- OCR carries the grouping ---

def test_ocr_reads_the_bundle_and_its_price():
    rows = parse_rows({"selections": [
        {"match": "Arsenal v Leeds", "market": "Arsenal", "kickoff": "SAT 15:00",
         "bundle": "BUNDLE 1", "bundle_odds": "5-1"},
    ]})
    assert rows[0].bundle == "BUNDLE 1"
    assert rows[0].bundle_odds == 6.0        # 5/1 fractional -> 6.0 decimal
    # The sheet prices the bundle, so the selection carries no price.
    assert rows[0].your_odds is None


# --- ingest ---

def test_upload_groups_selections_into_bundles(bundle_client, db):
    coupon_id = _upload(bundle_client)
    legs = db.query(Leg).filter(Leg.coupon_id == coupon_id).order_by(Leg.rank).all()

    assert len(legs) == 5
    assert [l.bundle for l in legs] == [
        "BUNDLE 1", "BUNDLE 1", "BUNDLE 2", "BUNDLE 2", "BUNDLE 2",
    ]
    # The market comes from the TYPE; the team from the printed selection.
    assert legs[0].pick == "Kairat Win"
    assert legs[3].pick == "Arsenal Win"
    # The bundle's price is on the leg; the leg itself has none.
    assert legs[0].bundle_odds == 6.0 and legs[0].odds is None
    assert legs[2].bundle_odds == 15.0


def test_a_fixture_in_two_bundles_survives(bundle_client, db):
    """The rule that would have deleted it.

    On a grid, one line per match is a property of the sheet and a repeat is
    the read losing its place. On a bundle sheet the same fixture appearing in
    two bundles is the coupon doing its job, and dropping the second occurrence
    would silently shorten every bundle after the first.
    """
    coupon_id = _upload(bundle_client)
    kairat = (
        db.query(Leg)
        .filter(Leg.coupon_id == coupon_id, Leg.match_name == "Kairat v Omonia Nicosia")
        .all()
    )
    assert len(kairat) == 2
    assert {l.bundle for l in kairat} == {"BUNDLE 1", "BUNDLE 2"}


def test_selections_with_no_bundle_are_dropped_not_kept_loose(bundle_client, db):
    """A selection outside a bundle is not a bet on this sheet.

    It belongs to an acca whose heading the read lost, so keeping it would
    either put it in the wrong group or invent a one-leg bundle.
    """
    from app.routers.upload import _build_legs
    extracted = ExtractedCoupon(rows=[
        ExtractedRow(match="A v B", market="A", bundle="BUNDLE 1", bundle_odds=4.0),
        ExtractedRow(match="C v D", market="C", bundle=None, bundle_odds=None),
    ])
    from datetime import date
    legs = _build_legs(1, extracted, get("FREDB"), date(2026, 8, 22))
    assert len(legs) == 1 and legs[0].bundle == "BUNDLE 1"


# --- pricing: the acca, straight win ---

def test_bundle_is_priced_as_the_product_of_its_legs(bundle_client, db):
    """Bundle 1 = Kairat (2.0) x Mariehamn (2.5) = 5.0 fair, printed at 6.0."""
    coupon_id = _upload(bundle_client)
    legs = db.query(Leg).filter(Leg.coupon_id == coupon_id).order_by(Leg.rank).all()
    found = {b["name"]: b for b in bundles_on(legs)}

    one = found["BUNDLE 1"]
    assert one["picks"] == 2
    assert one["fair"] == pytest.approx(5.0)
    assert one["payout"] == 6.0
    assert one["value"] == pytest.approx(0.2)       # 6.0 / 5.0 - 1

    # Bundle 2 = 2.0 x 1.5 x 2.0 = 6.0 fair, printed at 15.0.
    two = found["BUNDLE 2"]
    assert two["picks"] == 3
    assert two["fair"] == pytest.approx(6.0)
    assert two["value"] == pytest.approx(1.5)


def test_an_unpriced_leg_makes_the_whole_bundle_unvaluable():
    """An acca is only as knowable as its worst leg.

    Valuing a four-fold off the three legs that priced overstates it every
    time, so the answer is None rather than a number.
    """
    legs = [
        Leg(match_name="A v B", pick="A Win", rank=0,
            bundle="BUNDLE 1", bundle_odds=6.0, fair_odds=2.0),
        Leg(match_name="C v D", pick="C Win", rank=1,
            bundle="BUNDLE 1", bundle_odds=6.0, fair_odds=None),
    ]
    (one,) = bundles_on(legs)
    assert one["value"] is None
    assert one["fair"] is None
    assert one["unpriced"] == 1
    # Still listed — the selections are real and worth seeing.
    assert one["picks"] == 2


def test_a_bundle_with_no_readable_price_is_listed_without_value():
    legs = [
        Leg(match_name="A v B", pick="A Win", rank=0,
            bundle="BUNDLE 1", bundle_odds=None, fair_odds=2.0),
    ]
    (one,) = bundles_on(legs)
    assert one["payout"] is None and one["value"] is None
    assert one["fair"] == pytest.approx(2.0)


def test_the_price_is_taken_from_whichever_row_carried_it():
    """OCR repeats the bundle price on every row; one fumbled row must not
    cost the bundle its price."""
    legs = [
        Leg(match_name="A v B", pick="A Win", rank=0,
            bundle="B1", bundle_odds=None, fair_odds=2.0),
        Leg(match_name="C v D", pick="C Win", rank=1,
            bundle="B1", bundle_odds=6.0, fair_odds=2.0),
    ]
    (one,) = bundles_on(legs)
    assert one["payout"] == 6.0
    assert one["value"] == pytest.approx(0.5)       # 6.0 / 4.0 - 1


def test_bundles_rank_best_value_first_with_unvaluable_last():
    legs = [
        Leg(match_name="A v B", pick="A Win", rank=0,
            bundle="LOW", bundle_odds=4.1, fair_odds=4.0),
        Leg(match_name="C v D", pick="C Win", rank=1,
            bundle="HIGH", bundle_odds=8.0, fair_odds=4.0),
        Leg(match_name="E v F", pick="E Win", rank=2,
            bundle="BLANK", bundle_odds=None, fair_odds=4.0),
    ]
    assert [b["name"] for b in bundles_on(legs)] == ["HIGH", "LOW", "BLANK"]


# --- the API ---

def test_bundles_endpoint_returns_priced_accas(bundle_client):
    coupon_id = _upload(bundle_client)
    resp = bundle_client.get(f"/coupons/types/FREDB/bundles?coupon_id={coupon_id}")
    assert resp.status_code == 200
    body = resp.json()

    assert [b["name"] for b in body] == ["BUNDLE 2", "BUNDLE 1"]   # best first
    assert body[0]["picks"] == 3
    assert body[0]["value"] == pytest.approx(1.5)
    assert body[0]["coupon_id"] == coupon_id
    # The legs ride along so the acca can be read without a second request.
    assert [l["pick"] for l in body[0]["legs"]] == [
        "Kairat Win", "Arsenal Win", "Ajax Win",
    ]


def test_bundles_endpoint_refuses_a_non_bundle_type(bundle_client):
    resp = bundle_client.get("/coupons/types/TTS2/bundles")
    assert resp.status_code == 400


def test_type_listing_carries_the_flag_and_the_best_bundle(bundle_client):
    _upload(bundle_client)
    types = {t["code"]: t for t in bundle_client.get("/coupons/types").json()}
    fredb = types["FREDB"]
    assert fredb["bundles"] is True
    assert fredb["best_bundle"]["name"] == "BUNDLE 2"
    assert fredb["best_bundle"]["value"] == pytest.approx(1.5)
    # The headline figure only — the legs are several hundred bytes nothing
    # on the landing page reads.
    assert "legs" not in fredb["best_bundle"]
    # A non-bundle type must not claim to be one.
    assert types["TTS2"]["bundles"] is False
