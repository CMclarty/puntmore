"""AW 26: a leg is married to the snapshot line it came from — the nearest
snapshot before the slip was struck, same fixture, kind, line and side — and
that line prices it when Bookie Bashing has no fair."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.aw26 import snapshots
from app.aw26.marry import find_line, marry_slip
from app.aw26.offers import expected_value
from app.cards.value import CardValueRow
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import AwLeg, AwLegLine, AwSlip

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
Session = sessionmaker(bind=engine)
KEY = "test-ingest-key"
PLACED = datetime(2026, 10, 25, 13, 0)          # UTC
KO = "2026-10-25T15:00:00Z"


def _db():
    db = Session()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = _db
    monkeypatch.setattr(settings, "saves_ingest_key", KEY)
    snapshots._last.clear()
    yield
    snapshots._last.clear()
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=engine)


def row(line=9.5, side="Over", market_key="alternate_totals_corners", odds=2.10,
        fair=2.00, home="Arsenal", away="Chelsea", event_id="e1"):
    return CardValueRow(
        event_id=event_id, home_team=home, away_team=away, commence_time=KO,
        sport_key="soccer_epl", market_key=market_key, market_label="", line=line,
        side=side, paddy_odds=odds, pinnacle_odds=fair, fair_odds=fair,
        pinnacle_margin=0.05, edge=odds / fair - 1, basis="ladder")


def snap(rows, at):
    db = Session()
    snapshots.record(db, rows, "paddy", at)
    db.close()


def leg(event="Arsenal v Chelsea", market="Total Match Corners", selection="Over 9.5",
        kind="corners", line=9.5, side="over", odds=2.05):
    return AwLeg(position=0, event=event, market=market, selection=selection, odds=odds,
                 kickoff=datetime(2026, 10, 25, 15, 0), kind=kind, line=line, side=side,
                 result="pending")


def test_nearest_snapshot_before_placed_at_wins():
    snap([row(odds=2.20)], PLACED - timedelta(minutes=50))
    snap([row(odds=2.10)], PLACED - timedelta(minutes=5))
    snap([row(odds=1.90)], PLACED + timedelta(minutes=10))     # after: never used
    db = Session()
    line, s, score = find_line(db, leg(), PLACED)
    assert line.offered_odds == 2.10 and s.at == PLACED - timedelta(minutes=5)
    assert score == 1.0
    db.close()


def test_line_side_and_kind_must_match_exactly():
    snap([row(line=10.5), row(side="Under"), row(market_key="alternate_totals_cards")],
         PLACED - timedelta(minutes=5))
    db = Session()
    assert find_line(db, leg(), PLACED) is None
    db.close()


def test_a_snapshot_without_the_line_falls_back_to_an_earlier_one():
    snap([row(odds=2.30)], PLACED - timedelta(minutes=40))
    snap([row(line=10.5)], PLACED - timedelta(minutes=5))      # line dropped off
    db = Session()
    line, _, _ = find_line(db, leg(), PLACED)
    assert line.offered_odds == 2.30
    db.close()


def test_too_old_a_snapshot_is_not_used():
    snap([row()], PLACED - timedelta(hours=4))
    db = Session()
    assert find_line(db, leg(), PLACED) is None
    db.close()


def test_team_names_are_matched_loosely_but_must_both_match():
    snap([row(home="Manchester United", away="Chelsea")], PLACED - timedelta(minutes=5))
    db = Session()
    assert find_line(db, leg(event="Man Utd v Chelsea"), PLACED) is not None
    assert find_line(db, leg(event="Man Utd v Liverpool"), PLACED) is None
    db.close()


def test_team_leg_marries_only_its_teams_line():
    snap([row(line=4.5),                                          # a match line
          row(line=4.5, market_key="team_corners_home", odds=1.90, fair=1.80),
          row(line=4.5, market_key="team_corners_away", odds=2.50, fair=2.40)],
         PLACED - timedelta(minutes=5))
    db = Session()
    home = find_line(db, leg(market="Team Corners", selection="Arsenal Over 4.5", line=4.5), PLACED)
    away = find_line(db, leg(market="Team Corners", selection="Chelsea Over 4.5", line=4.5), PLACED)
    match = find_line(db, leg(selection="Over 4.5", line=4.5), PLACED)
    assert (home[0].market, home[0].team) == ("team_corners", "home")
    assert (away[0].market, away[0].team) == ("team_corners", "away")
    assert (match[0].market, match[0].team) == ("corners", None)
    db.close()


def test_other_legs_never_marry():
    snap([row()], PLACED - timedelta(minutes=5))
    db = Session()
    assert find_line(db, leg(kind="other", line=None, side=None), PLACED) is None
    db.close()


def test_marry_slip_copies_the_prices_and_survives_prune():
    snap([row(odds=2.10, fair=2.00)], PLACED - timedelta(minutes=5))
    db = Session()
    slip = AwSlip(placed_at=PLACED, user="u", offer="other", stake=10, status="open")
    slip.legs.append(leg())
    assert marry_slip(db, slip) == 1
    db.add(slip)
    db.commit()
    link = db.query(AwLegLine).one()
    assert (link.offered_odds_at_snapshot, link.fair_odds, link.source) == (2.10, 2.00, "paddy")
    assert slip.legs[0].offered_odds == 2.10          # offered; the leg's odds (2.05) were taken
    snapshots.prune(db, keep_days=0, now=PLACED + timedelta(days=1))
    db.expire_all()
    link = db.query(AwLegLine).one()
    assert link.line_id is None and link.fair_odds == 2.00
    db.close()


# --- through the endpoints ---------------------------------------------------


def _ingest(client, legs, offer="other", stake=10, receipt="R1"):
    return client.post("/aw26/slips/ingest", headers={"X-API-Key": KEY}, json={
        "receipt": receipt, "placed_at": PLACED.isoformat() + "Z", "offer": offer,
        "stake": stake, "legs": legs})


def _leg_in(selection="Over 9.5", odds=2.05, fair=None):
    d = {"event": "Arsenal v Chelsea", "market": "Total Match Corners",
         "selection": selection, "odds": odds, "kickoff": KO}
    if fair:
        d["fair_odds"] = fair
    return d


def test_ingest_without_a_bb_fair_is_priced_off_the_married_line():
    snap([row(odds=2.10, fair=2.00)], PLACED - timedelta(minutes=5))
    client = TestClient(app)
    body = _ingest(client, [_leg_in()]).json()
    assert body["ev"] == round(expected_value("other", 10, [2.05], [2.00]), 2)
    slip = client.get("/aw26/slips").json()["slips"][0]
    assert slip["ev_note"] == "fair from the paddy snapshot on 1 of 1 legs"
    assert slip["legs"][0]["offered_odds"] == 2.10
    assert slip["legs"][0]["fair_odds"] == 2.00


def test_a_bb_fair_still_wins_and_the_note_stays_clean():
    snap([row(odds=2.10, fair=2.00)], PLACED - timedelta(minutes=5))
    client = TestClient(app)
    body = _ingest(client, [_leg_in(fair=1.95)]).json()
    assert body["ev"] == round(expected_value("other", 10, [2.05], [1.95]), 2)
    slip = client.get("/aw26/slips").json()["slips"][0]
    assert slip["ev_note"] is None
    assert slip["legs"][0]["offered_odds"] == 2.10       # still married


def test_unmarried_leg_without_a_fair_stays_unpriced():
    client = TestClient(app)
    body = _ingest(client, [_leg_in()]).json()
    assert body["ev"] is None


def test_an_edit_re_marries_and_deleting_the_slip_removes_the_link():
    snap([row(odds=2.10, fair=2.00), row(line=10.5, odds=2.60, fair=2.50)],
         PLACED - timedelta(minutes=5))
    client = TestClient(app)
    sid = _ingest(client, [_leg_in()]).json()["id"]
    slip = client.get("/aw26/slips").json()["slips"][0]
    leg_id = slip["legs"][0]["id"]
    r = client.patch(f"/aw26/slips/{sid}", json={"legs": [{
        "id": leg_id, "event": "Arsenal v Chelsea", "market": "Total Match Corners",
        "selection": "Over 10.5", "odds": 2.55}]})
    assert r.status_code == 200, r.text
    assert r.json()["legs"][0]["offered_odds"] == 2.60
    assert r.json()["ev"] == round(expected_value("other", 10, [2.55], [2.50]), 2)
    assert client.delete(f"/aw26/slips/{sid}").status_code == 200
    db = Session()
    assert db.query(AwLegLine).count() == 0
    db.close()
