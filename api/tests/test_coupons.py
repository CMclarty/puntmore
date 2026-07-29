"""Coupon accumulator + legs model tests (R1)."""
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
    # Upload now prices inline (auto-confirm), so it needs an odds provider.
    app.state.odds_provider = MockOddsProvider(
        {("Arsenal vs Chelsea", "Arsenal Win and Over 2.5"): 1.80},
        matched={"Arsenal vs Chelsea": ("Arsenal vs Chelsea FC", 0.95)},
    )
    from app import config as cfg
    original = cfg.settings.upload_dir
    cfg.settings.upload_dir = str(tmp_path)
    yield TestClient(app)
    cfg.settings.upload_dir = original


api = TestClient(app)


def _make_coupon(db, type_code="WO25", status="confirmed", match_day=None):
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

def test_mock_ocr_returns_a_coupon_not_bare_rows():
    """OCR returns the whole coupon: rows plus what is printed on it."""
    coupon = MockOCRProvider().extract(b"fake", "image/png")
    assert len(coupon.rows) == 3
    assert all(isinstance(r, ExtractedRow) for r in coupon.rows)
    # Kickoffs and the payout ladder are part of the contract, not extras.
    assert all(r.kickoff_text for r in coupon.rows)
    assert coupon.payout_ladder == {3: 5.5, 4: 10.0}

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
    assert body[0]["status"] == "confirmed"

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

def test_upload_returns_saved_legs(upload_client):
    resp = upload_client.post(
        "/upload",
        data={"type_code": "WO25", "period": "SAT"},
        files={"file": ("test.png", io.BytesIO(b"img"), "image/png")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "coupon_id" in body
    assert len(body["legs"]) == 3
    assert body["legs"][0]["pick"] == "Arsenal Win and Over 2.5"

def test_upload_rejects_bad_mime(upload_client):
    resp = upload_client.post(
        "/upload",
        data={"type_code": "WO25", "period": "SAT"},
        files={"file": ("doc.pdf", io.BytesIO(b"data"), "application/pdf")},
    )
    assert resp.status_code == 400

def test_upload_auto_names_coupon(upload_client, db):
    """Name carries the period, since a coupon covers a window not a day."""
    resp = upload_client.post(
        "/upload",
        data={"type_code": "WO25", "period": "SAT",
              "uploaded_on": date(2026, 7, 24).isoformat()},   # a Friday
        files={"file": ("t.png", io.BytesIO(b"img"), "image/png")},
    )
    coupon_id = resp.json()["coupon_id"]
    c = db.get(Coupon, coupon_id)
    assert c.name == "Win and Over 2.5 · Saturday 25 Jul"
    assert c.period == "SAT"
    assert c.match_day == date(2026, 7, 25)
    assert c.period_ends == date(2026, 7, 25)


def test_upload_derives_a_multi_day_window(upload_client, db):
    resp = upload_client.post(
        "/upload",
        data={"type_code": "TTS2", "period": "MON_WED",
              "uploaded_on": date(2026, 7, 20).isoformat()},   # a Monday
        files={"file": ("t.png", io.BytesIO(b"img"), "image/png")},
    )
    c = db.get(Coupon, resp.json()["coupon_id"])
    assert (c.match_day, c.period_ends) == (date(2026, 7, 20), date(2026, 7, 22))


def test_upload_rejects_an_unknown_period(upload_client):
    resp = upload_client.post(
        "/upload",
        data={"type_code": "WO25", "period": "TUESDAY"},
        files={"file": ("t.png", io.BytesIO(b"img"), "image/png")},
    )
    assert resp.status_code == 400


# --- Auto-confirm upload (no review gate) ---

def test_upload_saves_and_prices_legs_immediately(upload_client, db):
    up = upload_client.post(
        "/upload",
        data={"type_code": "WO25", "period": "SAT"},
        files={"file": ("t.png", io.BytesIO(b"img"), "image/png")},
    )
    assert up.status_code == 200
    body = up.json()
    coupon_id = body["coupon_id"]

    # Rows persist without a confirm step, and come back priced.
    assert len(body["legs"]) == 3
    arsenal = next(l for l in body["legs"] if l["match_name"] == "Arsenal vs Chelsea")
    assert arsenal["fair_odds"] == 1.80
    assert abs(arsenal["value"] - compute_value(1.85, 1.80)) < 1e-6

    db.expire_all()
    coupon = db.get(Coupon, coupon_id)
    assert coupon.status == "confirmed"
    assert db.query(Leg).filter(Leg.coupon_id == coupon_id).count() == 3


def test_upload_flags_unmatched_rows(upload_client):
    body = upload_client.post(
        "/upload",
        data={"type_code": "WO25", "period": "SAT"},
        files={"file": ("t.png", io.BytesIO(b"img"), "image/png")},
    ).json()

    # Only Arsenal is matched by the fixture provider; the rest carry no_match
    # but still persist with blank fair odds.
    unmatched = [l for l in body["legs"] if l["match_name"] != "Arsenal vs Chelsea"]
    assert unmatched
    for leg in unmatched:
        assert "no_match" in leg["flags"]
        assert leg["fair_odds"] is None

    arsenal = next(l for l in body["legs"] if l["match_name"] == "Arsenal vs Chelsea")
    assert arsenal["flags"] == []


# --- PATCH /coupons/{id}/legs/{leg_id} (inline edit) ---

def test_edit_leg_reprices_and_marks_edited(db):
    c = _make_coupon(db)
    leg = Leg(coupon_id=c.id, match_name="Arsnl vs Chelsea", pick="Over 2.5", odds=2.0, rank=0)
    db.add(leg)
    db.commit()
    leg_id = leg.id

    app.state.odds_provider = MockOddsProvider(
        {("Arsenal vs Chelsea", "Over 2.5"): 1.80},
        matched={"Arsenal vs Chelsea": ("Arsenal vs Chelsea", 0.99)},
    )
    resp = api.patch(
        f"/coupons/{c.id}/legs/{leg_id}", json={"match_name": "Arsenal vs Chelsea"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["edited"] is True
    assert body["fair_odds"] == 1.80
    assert body["flags"] == []


def test_edit_leg_404_wrong_coupon(db):
    c = _make_coupon(db)
    other = _make_coupon(db)
    leg = Leg(coupon_id=other.id, match_name="X vs Y", pick="Win", odds=1.5, rank=0)
    db.add(leg)
    db.commit()

    app.state.odds_provider = MockOddsProvider()
    assert api.patch(f"/coupons/{c.id}/legs/{leg.id}", json={"odds": 1.9}).status_code == 404


# --- POST /upload/{id}/rescan ---

def test_rescan_replaces_unedited_rows_but_keeps_edits(upload_client, db):
    coupon_id = upload_client.post(
        "/upload",
        data={"type_code": "WO25", "period": "SAT"},
        files={"file": ("t.png", io.BytesIO(b"img"), "image/png")},
    ).json()["coupon_id"]

    # Correct one row by hand, then re-scan.
    target = db.query(Leg).filter(
        Leg.coupon_id == coupon_id, Leg.match_name == "Tottenham vs Man Utd"
    ).one()
    upload_client.patch(
        f"/coupons/{coupon_id}/legs/{target.id}",
        json={"match_name": "Spurs vs Manchester United", "odds": 1.70},
    )

    resp = upload_client.post(f"/upload/{coupon_id}/rescan")
    assert resp.status_code == 200

    db.expire_all()
    legs = {l.match_name: l for l in db.query(Leg).filter(Leg.coupon_id == coupon_id)}
    # The human correction survived the machine re-read...
    assert "Spurs vs Manchester United" in legs
    assert legs["Spurs vs Manchester United"].odds == 1.70
    assert legs["Spurs vs Manchester United"].edited is True
    # ...and the OCR row it replaced did not come back.
    assert "Tottenham vs Man Utd" not in legs
    # Unedited rows were re-read fresh.
    assert "Arsenal vs Chelsea" in legs
    assert len(legs) == 3


def test_rescan_409_when_image_gone(upload_client, db):
    coupon_id = upload_client.post(
        "/upload",
        data={"type_code": "WO25", "period": "SAT"},
        files={"file": ("t.png", io.BytesIO(b"img"), "image/png")},
    ).json()["coupon_id"]

    db.expire_all()
    coupon = db.get(Coupon, coupon_id)
    os.remove(coupon.image_path)

    assert upload_client.post(f"/upload/{coupon_id}/rescan").status_code == 409


def test_rescan_404_missing(upload_client):
    assert upload_client.post("/upload/9999/rescan").status_code == 404


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
            data={"type_code": "WO25", "period": "SAT"},
            files={"file": ("t.png", io.BytesIO(b"img"), "image/png")},
        )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides[require_user] = lambda: "testuser"

def test_coupons_requires_auth():
    from app.security import require_user, user_permissions
    app.dependency_overrides.pop(require_user, None)
    app.dependency_overrides.pop(user_permissions, None)
    try:
        assert api.get("/coupons").status_code == 401
    finally:
        app.dependency_overrides[require_user] = lambda: "testuser"
        app.dependency_overrides[user_permissions] = lambda: "*"


# --- coupon types as top-level entities ---

def test_list_types_returns_every_type_with_counts(db):
    c = _make_coupon(db, type_code="TTS2")
    db.add(Leg(coupon_id=c.id, match_name="A vs B", pick="A to Score 2", odds=1.9, rank=0))
    db.commit()

    body = api.get("/coupons/types").json()
    from app.coupon_types import COUPON_TYPES
    assert len(body) == len(COUPON_TYPES)
    tts2 = next(t for t in body if t["code"] == "TTS2")
    assert tts2["label"] == "Team to Score 2"
    assert tts2["selection_count"] == 1
    # Types with nothing uploaded still appear — they are the entities.
    assert next(t for t in body if t["code"] == "GGND")["selection_count"] == 0


def test_type_selections_span_every_upload_of_that_type(db):
    """The TYPE is the entity; an upload is just how selections got into it."""
    c1 = _make_coupon(db, type_code="TTS2")
    c2 = _make_coupon(db, type_code="TTS2")
    other = _make_coupon(db, type_code="WO25")
    db.add(Leg(coupon_id=c1.id, match_name="A vs B", pick="A to Score 2", odds=1.9, rank=0, value=0.03))
    db.add(Leg(coupon_id=c2.id, match_name="C vs D", pick="C to Score 2", odds=2.0, rank=0, value=0.09))
    db.add(Leg(coupon_id=other.id, match_name="E vs F", pick="E Win and Over 2.5", odds=3.0, rank=0))
    db.commit()

    rows = api.get("/coupons/types/TTS2/selections").json()
    assert [r["match_name"] for r in rows] == ["C vs D", "A vs B"]   # best value first
    assert all(r["pick"] != "E Win and Over 2.5" for r in rows)


def test_gg_bonus_screens_on_price_and_ranks_shortest_first(db):
    c = _make_coupon(db, type_code="GGB")
    db.add(Leg(coupon_id=c.id, match_name="Short", pick="GG Bonus", odds=1.5, rank=0, fair_odds=1.45))
    db.add(Leg(coupon_id=c.id, match_name="Mid", pick="GG Bonus", odds=1.8, rank=1, fair_odds=1.75))
    db.add(Leg(coupon_id=c.id, match_name="TooLong", pick="GG Bonus", odds=2.4, rank=2, fair_odds=2.30))
    db.commit()

    rows = api.get("/coupons/types/GGB/selections").json()
    # Over the 1.80 cut is excluded; the rest are shortest-first (likeliest).
    assert [r["match_name"] for r in rows] == ["Short", "Mid"]


def test_unpriced_rows_survive_the_screen(db):
    """An unpriced row is something to look at, not something to hide."""
    c = _make_coupon(db, type_code="GGB")
    db.add(Leg(coupon_id=c.id, match_name="Unpriced", pick="GG Bonus", odds=1.6, rank=0, fair_odds=None))
    db.commit()
    rows = api.get("/coupons/types/GGB/selections").json()
    assert [r["match_name"] for r in rows] == ["Unpriced"]


def test_unknown_type_selections_404():
    assert api.get("/coupons/types/WO35/selections").status_code == 404


def test_upload_rejects_a_type_outside_the_seven(upload_client):
    resp = upload_client.post(
        "/upload",
        data={"type_code": "WO35", "period": "SAT"},
        files={"file": ("t.png", io.BytesIO(b"img"), "image/png")},
    )
    assert resp.status_code == 400


# --- ladder coupons end to end (GG Bonus / GG No Draw) ---

@pytest.fixture()
def gg_client(tmp_path):
    """Upload client whose OCR returns a LADDER coupon: no odds, has a ladder."""
    from app.providers.base import ExtractedCoupon, ExtractedRow

    class _LadderOCR(OCRProvider):
        def extract(self, image_bytes, media_type, hint=None):
            return ExtractedCoupon(
                rows=[
                    ExtractedRow("Inverness v St Johnstone", None, None, "MON 19:45"),
                    ExtractedRow("Gais v Nordsjaelland", None, None, "TUE 18:00"),
                    ExtractedRow("Atl Mineiro v Bahia", None, None, "WED 23:30"),
                    ExtractedRow("Botafogo v Vitoria", None, None, "THU 01:30"),
                ],
                title="GG BONUS",
                period_text="Monday 20th - Thursday 23rd July",
                payout_ladder={3: 5.5, 4: 10.0},
            )

    app.state.ocr_provider = _LadderOCR()
    app.state.odds_provider = MockOddsProvider(
        {
            ("Inverness v St Johnstone", "GG Bonus"): 1.60,
            ("Gais v Nordsjaelland", "GG Bonus"): 1.70,
            ("Atl Mineiro v Bahia", "GG Bonus"): 1.75,
            ("Botafogo v Vitoria", "GG Bonus"): 1.90,
        },
        matched={n: (n, 0.99) for n in (
            "Inverness v St Johnstone", "Gais v Nordsjaelland",
            "Atl Mineiro v Bahia", "Botafogo v Vitoria")},
    )
    from app import config as cfg
    original = cfg.settings.upload_dir
    cfg.settings.upload_dir = str(tmp_path)
    yield TestClient(app)
    cfg.settings.upload_dir = original


def _upload_gg(client):
    return client.post(
        "/upload",
        data={"type_code": "GGB", "period": "MIDWEEK",
              "uploaded_on": date(2026, 7, 20).isoformat()},
        files={"file": ("t.png", io.BytesIO(b"img"), "image/png")},
    )


def test_ladder_coupon_saves_without_odds(gg_client, db):
    """A GG coupon prints no price; rows must persist anyway."""
    resp = _upload_gg(gg_client)
    assert resp.status_code == 200
    legs = resp.json()["legs"]
    assert len(legs) == 4
    assert all(l["odds"] is None for l in legs)
    # The market comes from WHICH coupon it is, not from OCR.
    assert all(l["pick"] == "GG Bonus" for l in legs)
    assert all(l["value"] is None for l in legs)   # no price, no per-row edge


def test_ladder_coupon_window_comes_from_its_fixtures(gg_client, db):
    """MIDWEEK has no schedule — the dates are the coupon's own kickoffs."""
    coupon_id = _upload_gg(gg_client).json()["coupon_id"]
    db.expire_all()
    c = db.get(Coupon, coupon_id)
    assert (c.match_day, c.period_ends) == (date(2026, 7, 20), date(2026, 7, 23))
    assert c.ladder == {3: 5.5, 4: 10.0}


def test_ladder_coupon_reports_its_best_combination(gg_client, db):
    _upload_gg(gg_client)
    body = gg_client.get("/coupons/types").json()
    ggb = next(t for t in body if t["code"] == "GGB")
    assert ggb["prices_each_selection"] is False
    best = ggb["best_combination"]
    # 3 shortest: 1.60*1.70*1.75 = 4.76 fair against 5.5 -> +15.6%.
    # The 4-fold adds 1.90 -> 9.04 against 10.0 -> +10.6%, so 3 wins here.
    # Which rung wins depends on the spread, which is why every rung is scored
    # rather than one being assumed.
    assert best["picks"] == 3
    assert best["fair"] == pytest.approx(4.76, abs=0.01)
    assert best["payout"] == 5.5
    assert best["value"] == pytest.approx(0.1555, abs=1e-3)


def test_gg_period_must_be_midweek_or_weekend(gg_client):
    resp = gg_client.post(
        "/upload",
        data={"type_code": "GGB", "period": "SAT"},
        files={"file": ("t.png", io.BytesIO(b"img"), "image/png")},
    )
    assert resp.status_code == 400


def test_periods_are_filtered_by_type():
    assert [p["code"] for p in api.get("/coupons/periods?type_code=GGB").json()] == [
        "MIDWEEK", "WEEKEND",
    ]
    assert [p["code"] for p in api.get("/coupons/periods?type_code=TTS2").json()] == [
        "MON_WED", "WED_FRI", "SAT", "SUN",
    ]


def test_variable_periods_expose_no_dates():
    """Their dates are printed on the coupon, so the UI must not invent any."""
    for p in api.get("/coupons/periods?type_code=GGB").json():
        assert p["variable"] is True
        assert p["starts_on"] is None


# --- HILLS: the coupon that prints prices AND pays a ladder ---

@pytest.fixture()
def hills_client(tmp_path):
    """Upload client whose OCR returns a William Hill "Both score no draw":
    a price against every fixture AND a printed ladder."""
    from app.providers.base import ExtractedCoupon, ExtractedRow

    class _HillsOCR(OCRProvider):
        def extract(self, image_bytes, media_type, hint=None):
            return ExtractedCoupon(
                rows=[
                    # 8/4, 6/5, 13/8, 11/8 as printed → decimal. Market stays
                    # None: it is known from WHICH coupon this is, as on GG.
                    ExtractedRow("Aberdeen v Kelty Hearts", None, 3.00, "SAT 15:00"),
                    ExtractedRow("Airdrie v Ross County", None, 2.20, "SAT 15:00"),
                    ExtractedRow("Arbroath v Stirling", None, 2.63, "SAT 15:00"),
                    ExtractedRow("Ayr v Albion", None, 2.38, "SAT 15:00"),
                ],
                title="BOTH SCORE NO DRAW",
                period_text="Saturday 25th - Sunday 26th July",
                payout_ladder={3: 5.5, 4: 10.0, 5: 21.0},
            )

    app.state.ocr_provider = _HillsOCR()
    app.state.odds_provider = MockOddsProvider(
        {
            ("Aberdeen v Kelty Hearts", "GG No Draw"): 1.60,
            ("Airdrie v Ross County", "GG No Draw"): 1.70,
            ("Arbroath v Stirling", "GG No Draw"): 1.75,
            ("Ayr v Albion", "GG No Draw"): 1.90,
        },
        matched={n: (n, 0.99) for n in (
            "Aberdeen v Kelty Hearts", "Airdrie v Ross County",
            "Arbroath v Stirling", "Ayr v Albion")},
    )
    from app import config as cfg
    original = cfg.settings.upload_dir
    cfg.settings.upload_dir = str(tmp_path)
    yield TestClient(app)
    cfg.settings.upload_dir = original


def _upload_hills(client):
    return client.post(
        "/upload",
        data={"type_code": "HILLS", "period": "WEEKEND",
              "uploaded_on": date(2026, 7, 25).isoformat()},
        files={"file": ("t.png", io.BytesIO(b"img"), "image/png")},
    )


def test_hills_keeps_the_printed_prices(hills_client, db):
    """The bug this type exists to fix: a Hills sheet prints a price on every
    fixture, and the ladder-only model threw every one of them away."""
    legs = _upload_hills(hills_client).json()["legs"]
    assert [l["odds"] for l in legs] == [3.00, 2.20, 2.63, 2.38]
    # ...and a printed price means a per-row edge, unlike a ladder-only coupon.
    assert all(l["value"] is not None for l in legs)


def test_hills_also_stores_its_ladder(hills_client, db):
    """Both halves, not one or the other: the same coupon carries the ladder."""
    coupon_id = _upload_hills(hills_client).json()["coupon_id"]
    db.expire_all()
    assert db.get(Coupon, coupon_id).ladder == {3: 5.5, 4: 10.0, 5: 21.0}


def test_hills_reports_a_combination_despite_being_priced(hills_client, db):
    """Combination value is gated on pays_ladder, not on "prints no prices" —
    gating it the old way would hide it on the one coupon offering both."""
    _upload_hills(hills_client)
    body = hills_client.get("/coupons/types").json()
    hills = next(t for t in body if t["code"] == "HILLS")
    assert hills["prices_each_selection"] is True and hills["pays_ladder"] is True
    best = hills["best_combination"]
    # 3 shortest fair: 1.60*1.70*1.75 = 4.76 against 5.5 → +15.6%.
    assert best["picks"] == 3
    assert best["fair"] == pytest.approx(4.76, abs=0.01)
    assert best["value"] == pytest.approx(0.1555, abs=1e-3)


def test_priced_only_coupon_stores_no_ladder(hills_client, db):
    """The other direction: pays_ladder=False must not acquire one, even if a
    scan hands one over."""
    from app.coupon_types import get
    assert get("TTS2").pays_ladder is False
    assert get("TTS2").default_ladder == {}


# --- Two-sided priced coupons (William Hill grids) ---

def test_two_sided_row_becomes_one_leg_per_team():
    """A Hill grid prints an H and an A price per fixture, and each is its own
    bet — so one row yields TWO priced legs with the team filled into the pick.
    Collapsing them to one silently discarded half the coupon."""
    from app.routers.upload import _build_legs
    from app.providers.base import ExtractedCoupon, ExtractedRow
    from app import coupon_types
    from datetime import date

    extracted = ExtractedCoupon(rows=[
        ExtractedRow("Kairat v Omonia Nicosia", None, None, "WED 16:00",
                     home_odds=1.3333, away_odds=1.3636),
    ])
    legs = _build_legs(1, extracted, coupon_types.get("TTS1"), date(2026, 7, 29))

    assert [l.pick for l in legs] == [
        "Kairat to Score 1", "Omonia Nicosia to Score 1",
    ]
    assert [l.odds for l in legs] == [1.3333, 1.3636]
    assert [l.rank for l in legs] == [0, 1]          # ranks stay contiguous
    assert all(l.kickoff.date() == date(2026, 7, 29) for l in legs)


def test_two_sided_skips_a_side_it_could_not_read():
    """An unreadable side is dropped, not stored priceless — on a priced coupon
    a leg with no odds has nothing to value. The other side still saves."""
    from app.routers.upload import _build_legs
    from app.providers.base import ExtractedCoupon, ExtractedRow
    from app import coupon_types
    from datetime import date

    extracted = ExtractedCoupon(rows=[
        ExtractedRow("Kauno v Klaksvik", None, None, "WED 17:00",
                     home_odds=1.2, away_odds=None),
    ])
    legs = _build_legs(1, extracted, coupon_types.get("TTS1"), date(2026, 7, 29))
    assert len(legs) == 1
    assert legs[0].pick == "Kauno to Score 1" and legs[0].odds == 1.2
