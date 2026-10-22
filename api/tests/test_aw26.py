"""AW 26: a slip photo is read, saved, priced when its fairs arrive, settled
from its legs, and reported — with the figures reconciling to the penny."""
import io
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.aw26.legs import classify
from app.aw26.offers import expected_value
from app.aw26.service import daily_message, parse_when, summary
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import AwSlip
from app.providers.mock_ocr import MockOCRProvider
from app.providers.slip_ocr import parse_slip

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
Session = sessionmaker(bind=engine)
KEY = "test-ingest-key"


def _db():
    db = Session()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = _db
    app.state.ocr_provider = MockOCRProvider()
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(settings, "saves_ingest_key", KEY)
    yield
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=engine)


def _png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buf, "PNG")
    return buf.getvalue()


def _upload(client, offer="refund"):
    r = client.post("/aw26/slips", data={"offer": offer},
                    files={"file": ("slip.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    return r.json()


MOCK_FAIRS = [  # the mock slip's five legs, each at a fair price
    ("Arsenal", "Chelsea", "Total Match Corners", "Over 8.5", 1.28),
    ("Man City", "Liverpool", "Total Match Corners", "Over 9.5", 1.36),
    ("Everton", "Leeds", "Total Match Cards", "Over 3.5", 1.33),
    ("Brighton", "Fulham", "Total Match Corners", "Under 11.5", 1.25),
    ("Wolves", "Burnley", "Total Goals", "Over 1.5", 1.30),
]


def _post_fairs(client, rows=MOCK_FAIRS, key=KEY):
    return client.post("/aw26/fairs", headers={"X-API-Key": key}, json={"fairs": [
        {"home": h, "away": a, "market": m, "selection": s, "fair_odds": f}
        for h, a, m, s, f in rows]})


def test_a_slip_is_read_saved_and_waits_for_its_fairs():
    client = TestClient(app)
    slip = _upload(client)
    assert len(slip["legs"]) == 5 and slip["stake"] == 50
    assert slip["legs"][0]["kind"] == "corners" and slip["legs"][0]["line"] == 8.5
    assert slip["ev"] is None and slip["ev_note"] == "5 of 5 legs have no fair price yet"
    assert slip["has_image"]


def test_fairs_arriving_price_the_waiting_slip_and_fix_its_ev():
    client = TestClient(app)
    _upload(client)
    r = _post_fairs(client)
    assert r.json() == {"stored": 5, "priced": 1, "still_unpriced": 0}
    slip = client.get("/aw26/slips").json()["slips"][0]
    want = expected_value("refund", 50, [1.30, 1.33, 1.36, 1.25, 1.29],
                          [1.28, 1.36, 1.33, 1.25, 1.30])
    assert slip["ev"] == round(want, 2)
    # A later scrape at different prices does not move a fixed EV.
    _post_fairs(client, [(h, a, m, s, f + 0.2) for h, a, m, s, f in MOCK_FAIRS])
    assert client.get("/aw26/slips").json()["slips"][0]["ev"] == round(want, 2)


def test_a_slip_uploaded_after_the_scrape_prices_on_upload():
    client = TestClient(app)
    _post_fairs(client)
    assert _upload(client)["ev"] is not None


def test_one_missing_fair_leaves_the_slip_unpriced_not_partly_priced():
    client = TestClient(app)
    _post_fairs(client, MOCK_FAIRS[:4])
    slip = _upload(client)
    assert slip["ev"] is None and slip["ev_note"] == "1 of 5 legs have no fair price yet"


def test_fairs_need_the_key():
    client = TestClient(app)
    assert _post_fairs(client, key="wrong").status_code == 401


# --- slip ingest (a bet read off-server, e.g. by the desktop session) --------

INGEST_LEGS = [  # same fixtures as MOCK_FAIRS, so the fairs table also matches
    ("Arsenal v Chelsea", "Total Match Corners", "Over 8.5", 1.30, 1.28),
    ("Man City v Liverpool", "Total Match Corners", "Over 9.5", 1.36, 1.36),
    ("Everton v Leeds", "Total Match Cards", "Over 3.5", 1.33, 1.33),
    ("Brighton v Fulham", "Total Match Corners", "Under 11.5", 1.25, 1.25),
    ("Wolves v Burnley", "Total Goals", "Over 1.5", 1.30, 1.30),
]


def _ingest(client, *, offer="refund", stake=50, receipt="REF-1",
            with_fairs=True, key=KEY):
    legs = []
    for ev, m, s, odds, fair in INGEST_LEGS:
        leg = {"event": ev, "market": m, "selection": s, "odds": odds}
        if with_fairs:
            leg["fair_odds"] = fair
        legs.append(leg)
    return client.post("/aw26/slips/ingest", headers={"X-API-Key": key},
                       json={"receipt": receipt, "offer": offer, "stake": stake,
                             "legs": legs})


def test_ingested_slip_with_per_leg_fairs_is_priced_on_arrival():
    client = TestClient(app)
    r = _ingest(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["legs"] == 5 and body["unpriced_legs"] == 0
    want = expected_value("refund", 50, [1.30, 1.36, 1.33, 1.25, 1.30],
                          [1.28, 1.36, 1.33, 1.25, 1.30])
    assert body["ev"] == round(want, 2)
    slips = client.get("/aw26/slips").json()["slips"]
    assert len(slips) == 1 and slips[0]["ev"] == round(want, 2)
    assert slips[0]["stake"] == 50 and not slips[0]["has_image"]


def test_ingested_slip_without_fairs_prices_from_the_fairs_table():
    client = TestClient(app)
    _post_fairs(client)
    r = _ingest(client, with_fairs=False)
    assert r.status_code == 200, r.text
    assert r.json()["ev"] is not None and r.json()["unpriced_legs"] == 0


def test_ingested_slip_with_no_fairs_anywhere_waits_unpriced():
    client = TestClient(app)
    r = _ingest(client, with_fairs=False)
    assert r.status_code == 200
    body = r.json()
    assert body["ev"] is None and body["unpriced_legs"] == 5


def test_slip_ingest_needs_the_key():
    client = TestClient(app)
    assert _ingest(client, key="wrong").status_code == 401


def test_ingesting_the_same_receipt_twice_is_refused():
    client = TestClient(app)
    assert _ingest(client, receipt="DUP").status_code == 200
    assert _ingest(client, receipt="DUP").status_code == 409


def test_slip_ingest_rejects_an_unknown_offer():
    client = TestClient(app)
    assert _ingest(client, offer="bogus").status_code == 400


# --- auto-results seam (wired into the daily job, dormant until written) -----

# --- FlashScore results: settlement from match stats --------------------------

def _leg(event, market, selection):
    from types import SimpleNamespace
    kind, line, side = classify(market, selection)
    return SimpleNamespace(event=event, market=market, selection=selection,
                           kind=kind, line=line, side=side)


def test_result_for_leg_settles_each_market_from_stats():
    from app.aw26.results import MatchStats, result_for_leg
    st = MatchStats("Everton", "Chelsea", goals_home=2, goals_away=1,
                    corners_home=6, corners_away=5, cards_home=2, cards_away=3,
                    saves_home=4, saves_away=1)
    R = lambda m, s: result_for_leg(_leg("Everton v Chelsea", m, s), st)
    assert R("Total Corners", "Over 8.5") == "won"      # total 11
    assert R("Total Corners", "Under 12.5") == "won"
    assert R("Total Corners", "Over 11.5") == "lost"
    assert R("Total Goals", "Over 2.5") == "won"         # total 3
    assert R("Total Match Cards", "Under 4.5") == "lost"  # total 5
    assert R("Goalkeeper To Make 2 Or More Saves", "Chelsea Goalkeeper") == "lost"  # away=1
    assert R("Goalkeeper To Make 2 Or More Saves", "Everton Goalkeeper") == "won"   # home=4
    assert R("First Goalscorer", "Smith") is None        # unreadable → pending


def test_integer_total_on_the_line_is_a_push():
    from app.aw26.results import MatchStats, result_for_leg
    st = MatchStats("A", "B", corners_home=5, corners_away=5)   # total 10
    assert result_for_leg(_leg("A v B", "Total Corners", "Over 10"), st) == "void"


def _results_legs():  # a 5-leg slip whose fixtures we have stats for
    return [
        {"event": "Everton v Chelsea", "market": "Goalkeeper To Make 2 Or More Saves",
         "selection": "Chelsea Goalkeeper", "odds": 1.22, "fair_odds": 1.20},
        {"event": "Wrexham v Preston", "market": "Total Corners", "selection": "Over 8.5",
         "odds": 1.45, "fair_odds": 1.40},
        {"event": "Man City v Ipswich", "market": "Total Corners", "selection": "Under 12.5",
         "odds": 1.25, "fair_odds": 1.22},
        {"event": "Millwall v Lincoln", "market": "Total Corners", "selection": "Under 12.5",
         "odds": 1.28, "fair_odds": 1.25},
        {"event": "Venezia v Napoli", "market": "Goalkeeper To Make 2 Or More Saves",
         "selection": "Napoli Goalkeeper", "odds": 1.30, "fair_odds": 1.28},
    ]


def _post_results(client, matches, key=KEY):
    return client.post("/aw26/results", headers={"X-API-Key": key}, json={"matches": matches})


def test_results_endpoint_settles_a_slip_on_arrival():
    client = TestClient(app)
    r = client.post("/aw26/slips/ingest", headers={"X-API-Key": KEY},
                    json={"receipt": "R1", "offer": "refund", "stake": 50,
                          "legs": _results_legs()})
    assert r.status_code == 200, r.text
    # All five legs win on these stats.
    matches = [
        {"home": "Everton", "away": "Chelsea", "saves_home": 4, "saves_away": 3},
        {"home": "Wrexham", "away": "Preston", "corners_home": 6, "corners_away": 5},
        {"home": "Man City", "away": "Ipswich", "corners_home": 4, "corners_away": 4},
        {"home": "Millwall", "away": "Lincoln", "corners_home": 5, "corners_away": 3},
        {"home": "Venezia", "away": "Napoli", "saves_home": 1, "saves_away": 2},
    ]
    out = _post_results(client, matches).json()
    assert out == {"matches": 5, "resolved": 5, "settled": 1}
    slip = client.get("/aw26/slips").json()["slips"][0]
    assert slip["status"] == "won"
    assert all(leg["result"] == "won" for leg in slip["legs"])


def test_results_endpoint_needs_the_key():
    client = TestClient(app)
    assert _post_results(client, [{"home": "A", "away": "B"}], key="wrong").status_code == 401


def test_results_leave_unknown_legs_pending():
    client = TestClient(app)
    client.post("/aw26/slips/ingest", headers={"X-API-Key": KEY},
                json={"receipt": "R2", "offer": "other", "stake": 10,
                      "legs": [{"event": "A v B", "market": "First Goalscorer",
                                "selection": "Smith", "odds": 5.0, "fair_odds": 4.5}]})
    out = _post_results(client, [{"home": "A", "away": "B", "goals_home": 1, "goals_away": 0}]).json()
    assert out["resolved"] == 0
    slip = client.get("/aw26/slips").json()["slips"][0]
    assert slip["status"] == "open" and slip["legs"][0]["result"] == "pending"


def test_leg_results_settle_the_slip_and_the_summary_reconciles():
    client = TestClient(app)
    _post_fairs(client)
    slip = _upload(client)
    legs = [{**leg, "result": "won"} for leg in slip["legs"]]
    legs[2]["result"] = "lost"
    r = client.patch(f"/aw26/slips/{slip['id']}", json={"legs": legs})
    out = r.json()
    assert out["status"] == "refunded" and out["returns"] == 37.5
    body = client.get("/aw26/slips").json()
    s = body["summary"]["total"]
    assert s["profit"] == -12.5 and s["staked"] == 50 and s["slips"] == 1
    assert s["ev"] == out["ev"]


def test_the_same_receipt_twice_is_refused():
    class Twice(MockOCRProvider):
        def read_slip(self, image_bytes, media_type):
            read = super().read_slip(image_bytes, media_type)
            read.receipt = "PP-123"
            return read
    app.state.ocr_provider = Twice()
    client = TestClient(app)
    _upload(client)
    r = client.post("/aw26/slips", data={"offer": "refund"},
                    files={"file": ("slip.png", _png(), "image/png")})
    assert r.status_code == 409


def test_delete_removes_the_row_and_its_photo():
    import os
    client = TestClient(app)
    slip = _upload(client)
    db = Session()
    path = db.get(AwSlip, slip["id"]).image_path
    db.close()
    assert os.path.exists(path)
    client.delete(f"/aw26/slips/{slip['id']}")
    assert not os.path.exists(path) and client.get("/aw26/slips").json()["slips"] == []


def test_the_page_needs_the_aw26_grant():
    from app.security import user_permissions
    app.dependency_overrides[user_permissions] = lambda: "coupons"
    try:
        assert TestClient(app).get("/aw26/slips").status_code == 403
    finally:
        app.dependency_overrides[user_permissions] = lambda: "*"


# --- reading ---

@pytest.mark.parametrize("market, selection, want", [
    ("Total Match Corners", "Over 9.5", ("corners", 9.5, "over")),
    ("Match Bookings", "Under 4.5 Cards", ("cards", 4.5, "under")),
    ("Total Goals", "Over 2.5", ("goals", 2.5, "over")),
    ("Goalkeeper Saves", "Over 7.5", ("saves", 7.5, "over")),
    ("Match Result", "Arsenal", ("other", None, None)),
])
def test_classify(market, selection, want):
    assert classify(market, selection) == want


def test_parse_slip_takes_the_total_stake_and_reads_fractions():
    read = parse_slip({"stake": "£10.00", "total_stake": "£50.00", "potential_returns": "£1,250.50",
                       "selections": [{"event": "A v B", "market": "Corners", "selection": "Over 9.5",
                                       "odds": "5/6"}, {"event": None}]})
    assert read.stake == 50 and read.potential_returns == 1250.5
    assert len(read.legs) == 1 and read.legs[0].odds == pytest.approx(1.8333, abs=1e-3)


def test_parse_when_reads_the_terminals_london_time():
    ref = datetime(2026, 10, 21, 12, 0)
    assert parse_when("21/10/2026 14:32", ref) == datetime(2026, 10, 21, 13, 32)   # BST
    assert parse_when("Sat 15:00", ref) == datetime(2026, 10, 24, 14, 0)
    assert parse_when("2026-11-02T15:00:00Z", ref) == datetime(2026, 11, 2, 15, 0)
    assert parse_when("not a time", ref) is None


def test_daily_message_reports_the_day_against_the_targets():
    s = AwSlip(placed_at=datetime(2026, 10, 20, 12), user="u", offer="refund", stake=50,
               status="open", ev=15.15)
    msg = daily_message([s], date(2026, 10, 20))
    assert "EV £15 / £1,000 · 1 slips" in msg
    assert "To date: EV £15 / £1,000 (1 days)" in msg and "/ £100,000" in msg
    assert summary([s], date(2026, 10, 21))["total"]["days"] == 2


def test_an_admin_grant_sees_aw26_and_others_need_it_named():
    from app.security import granted_tools
    assert "aw26" in granted_tools("*")
    assert "aw26" not in granted_tools("coupons+pnl")
    assert "aw26" in granted_tools("coupons+aw26")


def test_the_morning_job_settles_and_posts_to_the_aw26_route():
    from app.aw26.service import run_daily
    from app.models import AwLeg

    sent = []

    class Notifier:
        def send_rows(self, tool, header, rows):
            sent.append((tool, header, [line for _, line in rows]))

    db = Session()
    slip = AwSlip(placed_at=datetime(2026, 10, 20, 12), user="u", offer="other", stake=10,
                  status="open", ev=1.0)
    slip.legs.append(AwLeg(event="A v B", odds=2.0, result="won"))
    db.add(slip)
    db.commit()
    msg = run_daily(db, Notifier(), day=date(2026, 10, 20))
    assert db.get(AwSlip, slip.id).status == "won"
    assert sent and sent[0][0] == "aw26" and sent[0][1] == msg.split("\n")[0]
    db.close()
