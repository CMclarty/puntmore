"""GET /api/value — every tool's value in one row shape, filtered by grant."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Coupon, GoalscorerAlert, Leg
from app.security import user_permissions
from app.time_utils import utcnow

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

SOON = (utcnow() + timedelta(hours=3)).replace(microsecond=0)
LATER = (utcnow() + timedelta(days=3)).replace(microsecond=0)
ISO_SOON = SOON.isoformat() + "Z"


def _db():
    db = Session()
    try:
        yield db
    finally:
        db.close()


def _paddy_row(line, edge, side="Over", market_key="alternate_totals_corners"):
    return SimpleNamespace(
        event_id="pp1", home_team="Arsenal", away_team="Chelsea",
        commence_time=ISO_SOON, sport_key="soccer_epl", market_key=market_key,
        line=line, side=side, paddy_odds=2.1, fair_odds=round(2.1 / (1 + edge), 3),
        edge=edge, basis="ladder",
    )


class _Cards:
    def get_events(self):
        return []


ACCA_EVENT = {
    "id": "b1", "sport_key": "soccer_epl", "sport_title": "EPL",
    "home_team": "Leeds", "away_team": "Spurs", "commence_time": LATER.isoformat() + "Z",
    "bookmakers": [
        {"key": "bet365", "markets": [{"key": "h2h", "outcomes": [
            {"name": "Leeds", "price": 2.4}, {"name": "Spurs", "price": 1.6}]}]},
        {"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [
            {"name": "Leeds", "price": 2.1}, {"name": "Spurs", "price": 1.8}]}]},
    ],
}


@pytest.fixture(autouse=True)
def world(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = _db
    monkeypatch.setattr("app.cards.feed.price_events", lambda events, edge: [
        _paddy_row(9.5, 0.08), _paddy_row(10.0, 0.20), _paddy_row(4.5, -0.01, "Under"),
    ])
    monkeypatch.setattr("app.accabonus.feed._fetch_events", lambda client: [ACCA_EVENT])
    app.state.cards_client = _Cards()
    app.state.arbs_client = object()

    db = Session()
    tts = Coupon(type_code="TTS1", match_day=date.today(), status="confirmed")
    tts.legs = [
        Leg(match_name="Hull v Stoke", pick="Hull to Score 1", odds=1.5, fair_odds=1.4,
            value=0.0714, kickoff=SOON, side="home", rank=0),
        Leg(match_name="Gone v Past", pick="Gone to Score 1", odds=1.5, fair_odds=1.2,
            value=0.25, kickoff=utcnow() - timedelta(hours=1), rank=1),
        Leg(match_name="Bad v Price", pick="Bad to Score 1", odds=1.2, fair_odds=1.5,
            value=-0.2, kickoff=SOON, rank=2),
    ]
    ggb = Coupon(type_code="GGB", match_day=date.today(), status="confirmed")
    ggb.legs = [Leg(match_name="X v Y", pick="GG Bonus", odds=None, fair_odds=1.5,
                    value=None, kickoff=SOON, rank=0)]
    db.add_all([tts, ggb, GoalscorerAlert(
        event_id="g1", match_name="Spurs v Leeds", commence_time=LATER, player="Kane",
        bet365_price=6.0, pinnacle_price=5.0, fair_price=5.3, edge=0.132, inplay=False)])
    db.commit()
    db.close()
    yield
    app.dependency_overrides.pop(get_db, None)
    app.state.cards_client = None
    app.state.arbs_client = None
    Base.metadata.drop_all(bind=engine)


def _get(path, perms="value", **params):
    app.dependency_overrides[user_permissions] = lambda: perms
    return TestClient(app).get(path, params=params)


def test_one_shape_across_every_source_best_edge_first():
    body = _get("/value").json()
    assert body["sources"] == ["paddy", "365", "scorers", "coupon"]
    assert body["errors"] == {}
    rows = body["selections"]
    assert {r["source"] for r in rows} == {"paddy", "365", "scorers", "coupon"}
    edges = [r["edge"] for r in rows]
    assert edges == sorted(edges, reverse=True) and min(edges) > 0
    keys = {"source", "bookmaker", "event_id", "event", "home", "away", "kickoff",
            "sport", "market", "selection", "line", "side", "odds", "fair", "edge",
            "basis", "url", "seen_at"}
    assert all(set(r) == keys for r in rows)


def test_paddy_rows_are_half_lines_only():
    rows = _get("/value/paddy").json()["selections"]
    assert [(r["line"], r["side"]) for r in rows] == [(9.5, "over")]
    r = rows[0]
    assert (r["home"], r["away"], r["bookmaker"], r["market"]) == \
        ("Arsenal", "Chelsea", "paddypower", "corners")
    assert r["kickoff"].startswith(SOON.isoformat())


def test_min_edge_can_reach_below_fair():
    rows = _get("/value/paddy", min_edge=-0.02).json()["selections"]
    assert sorted(r["line"] for r in rows) == [4.5, 9.5]


def test_365_row_carries_the_fair_and_side():
    [r] = _get("/value/365").json()["selections"]
    assert (r["selection"], r["side"], r["bookmaker"], r["market"]) == ("Leeds", "home", "bet365", "h2h")
    assert r["fair"] < r["odds"] and r["event_id"] == "b1"


def test_coupon_rows_are_live_priced_selections_only():
    rows = _get("/value/coupon").json()["selections"]
    assert [r["selection"] for r in rows] == ["Hull to Score 1"]
    assert rows[0]["market"] == "TTS1" and rows[0]["home"] == "Hull"


def test_a_tool_grant_sees_only_its_own_rows():
    body = _get("/value", perms="coupons+goalscorers").json()
    assert body["sources"] == ["scorers", "coupon"]
    assert _get("/value/paddy", perms="coupons").status_code == 403
    assert _get("/value", perms="shop").status_code == 403
    assert _get("/value/nope").status_code == 404


def test_kickoff_window_and_bookmaker_filters():
    soon = _get("/value", kickoff_within_hours=12).json()["selections"]
    assert {r["source"] for r in soon} == {"paddy", "coupon"}
    b365 = _get("/value", bookmaker="bet365").json()["selections"]
    assert {r["source"] for r in b365} == {"365", "scorers"}


def test_a_failing_source_is_named_and_the_rest_still_answer(monkeypatch):
    def boom(client):
        raise RuntimeError("vendor down")
    monkeypatch.setattr("app.accabonus.feed._fetch_events", boom)
    body = _get("/value").json()
    assert "365" in body["errors"] and "vendor down" in body["errors"]["365"]
    assert {r["source"] for r in body["selections"]} == {"paddy", "scorers", "coupon"}
