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


@pytest.fixture(autouse=True)
def world(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = _db
    monkeypatch.setattr("app.cards.feed.price_events", lambda events, edge, book="paddypower": [
        _paddy_row(9.5, 0.08), _paddy_row(10.0, 0.20), _paddy_row(4.5, -0.01, "Under"),
    ])
    app.state.cards_client = _Cards()

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
    Base.metadata.drop_all(bind=engine)


def _get(path, perms="value", **params):
    app.dependency_overrides[user_permissions] = lambda: perms
    return TestClient(app).get(path, params=params)


def test_one_shape_across_every_source_best_edge_first():
    body = _get("/value").json()
    # 2up is listed (ENABLE_TWOUP defaults on) but has no feed without an
    # OddsPapi key, so it adds no rows and no error here. Goalscorers parked.
    assert body["sources"] == ["paddy", "coupon", "2up"]
    assert body["errors"] == {}
    rows = body["selections"]
    assert {r["source"] for r in rows} == {"paddy", "coupon"}
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


def test_coupon_rows_are_live_priced_selections_only():
    rows = _get("/value/coupon").json()["selections"]
    assert [r["selection"] for r in rows] == ["Hull to Score 1"]
    assert rows[0]["market"] == "TTS1" and rows[0]["home"] == "Hull"


def test_a_tool_grant_sees_only_its_own_rows():
    body = _get("/value", perms="coupons+goalscorers").json()
    assert body["sources"] == ["coupon"]   # goalscorers parked
    assert _get("/value/paddy", perms="coupons").status_code == 403
    assert _get("/value", perms="shop").status_code == 403
    assert _get("/value/nope").status_code == 404


def test_kickoff_window_and_bookmaker_filters():
    soon = _get("/value", kickoff_within_hours=12).json()["selections"]
    assert {r["source"] for r in soon} == {"paddy", "coupon"}
    b365 = _get("/value", bookmaker="bet365").json()["selections"]
    assert {r["source"] for r in b365} == set()   # goalscorers parked; no bet365 source live


def test_a_failing_source_is_named_and_the_rest_still_answer(monkeypatch):
    def boom(events, edge, book="paddypower"):
        raise RuntimeError("vendor down")
    monkeypatch.setattr("app.cards.feed.price_events", boom)
    body = _get("/value").json()
    assert "paddy" in body["errors"] and "vendor down" in body["errors"]["paddy"]
    assert {r["source"] for r in body["selections"]} == {"coupon"}


# --- Finder: a private source (Finder plan, Task 5) ----------------------------

from contextlib import contextmanager  # noqa: E402


@contextmanager
def grant(perms):
    app.dependency_overrides[user_permissions] = lambda: perms
    try:
        yield
    finally:
        app.dependency_overrides[user_permissions] = lambda: "*"


def _finder_tip(**over):
    tip = {"tip_id": "f1", "origin": "tipster-a", "sport": "football", "bookmaker": "bet365",
           "event": "A v B", "home": "A", "away": "B", "kickoff": SOON.replace(tzinfo=timezone.utc),
           "market": "Match Result", "selection": "A", "odds": 3.0, "fair": 2.5, "status": "offered"}
    tip.update(over)
    return tip


@pytest.fixture
def db_with_finder_row():
    from app.finder import rows
    db = Session()
    try:
        rows.upsert(db, [_finder_tip(),
                         _finder_tip(tip_id="f2", selection="B", fair=None),       # asked: never served
                         _finder_tip(tip_id="f3", selection="Draw", bookmaker="paddypower",
                                     odds=2.0, fair=2.0)],                          # offered at fair
                    datetime.now(timezone.utc))
    finally:
        db.close()


def test_finder_is_private_to_its_grant_and_absent_from_the_combined_feed(db_with_finder_row):
    with grant("value"):
        assert "finder" not in TestClient(app).get("/value").json()["sources"]
        assert TestClient(app).get("/value/finder").status_code == 403
    with grant("finder"):
        body = TestClient(app).get("/value/finder").json()
        [r] = [r for r in body["selections"] if r["selection"] == "A"]
        assert (r["source"], r["bookmaker"], r["selection"], r["edge"]) == ("finder", "bet365", "A", pytest.approx(0.2))
        assert "finder" not in TestClient(app).get("/value").json()["sources"]
    with grant("*"):
        assert "finder" not in TestClient(app).get("/value").json()["sources"]
        assert TestClient(app).get("/value/finder").status_code == 200


def test_finder_serves_every_offered_row_and_filters_on_the_rows_own_bookmaker(db_with_finder_row):
    with grant("finder"):
        body = TestClient(app).get("/value/finder").json()
        assert body["sources"] == ["finder"] and body["errors"] == {}
        # Every offered row (the edge floor is the tip's business, not this
        # feed's), never the asked one.
        assert sorted(r["selection"] for r in body["selections"]) == ["A", "Draw"]
        r = next(r for r in body["selections"] if r["selection"] == "A")
        assert (r["home"], r["away"], r["sport"], r["market"], r["basis"]) == \
            ("A", "B", "football", "Match Result", "tip")
        assert r["kickoff"].startswith(SOON.isoformat()) and r["seen_at"]
        pp = TestClient(app).get("/value/finder", params={"bookmaker": "paddypower"}).json()
        assert [r["selection"] for r in pp["selections"]] == ["Draw"]
        assert TestClient(app).get("/value/finder", params={"min_edge": 0.1}).json()["selections"][0]["selection"] == "A"
        assert len(TestClient(app).get("/value/finder", params={"min_edge": 0.1}).json()["selections"]) == 1


def test_finder_is_not_a_source_with_its_flag_off(monkeypatch, db_with_finder_row):
    from app.config import settings
    monkeypatch.setattr(settings, "enable_finder", False)
    with grant("*"):
        assert TestClient(app).get("/value/finder").status_code == 404
