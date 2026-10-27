"""GET /api/value-bets + GET /api/results — the bet feed a coordinator
(Daedalus) reads with FEED_API_KEY. Hermetic: the 365 pull is stubbed."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.aw26.results import MatchStats
from app.betfeed import service
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import FeedFixture, FeedSelection
from app.time_utils import utcnow

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

KEY = "feed-key-123"
LATER = (utcnow() + timedelta(days=2)).replace(microsecond=0)


def _db():
    db = Session()
    try:
        yield db
    finally:
        db.close()


def _event(eid, home, away, b365, pin, sport_key="soccer_epl", title="EPL", when=LATER):
    def book(key, prices):
        return {"key": key, "markets": [{"key": "h2h", "outcomes": [
            {"name": n, "price": p} for n, p in prices.items()]}]}
    return {
        "id": eid, "sport_key": sport_key, "sport_title": title,
        "home_team": home, "away_team": away, "commence_time": when.isoformat() + "Z",
        "bookmakers": [book("bet365", b365), book("pinnacle", pin)],
    }


EVENTS = [
    # bet365 well over fair on Leeds (home)
    _event("ev-leeds", "Leeds", "Spurs",
           {"Leeds": 2.6, "Spurs": 2.9, "Draw": 3.3}, {"Leeds": 2.3, "Spurs": 3.1, "Draw": 3.4}),
    # value on the draw
    _event("ev-lazio", "Lazio", "Roma",
           {"Lazio": 2.5, "Roma": 2.8, "Draw": 3.9}, {"Lazio": 2.45, "Roma": 2.85, "Draw": 3.3},
           sport_key="soccer_italy_serie_a", title="Serie A"),
    # tennis value — not football
    _event("ev-tennis", "Sinner", "Alcaraz",
           {"Sinner": 2.3, "Alcaraz": 1.7}, {"Sinner": 2.0, "Alcaraz": 1.85},
           sport_key="tennis_atp_paris", title="ATP Paris"),
    # no value anywhere: bet365 under fair on every side
    _event("ev-none", "Hull", "Stoke",
           {"Hull": 2.0, "Stoke": 3.5, "Draw": 3.2}, {"Hull": 2.1, "Stoke": 3.8, "Draw": 3.5}),
    # value but already kicked off
    _event("ev-live", "Celtic", "Hearts",
           {"Celtic": 1.9, "Hearts": 4.5, "Draw": 4.0}, {"Celtic": 1.6, "Hearts": 5.5, "Draw": 4.2},
           sport_key="soccer_spl", title="Premiership", when=utcnow() - timedelta(minutes=20)),
]


@pytest.fixture(autouse=True)
def world(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = _db
    monkeypatch.setattr("app.accabonus.feed._fetch_events", lambda client: list(EVENTS))
    monkeypatch.setattr(settings, "feed_api_key", KEY)
    monkeypatch.setattr(settings, "enable_accabonus", True)
    app.state.arbs_client = object()
    yield
    app.dependency_overrides.pop(get_db, None)
    app.state.arbs_client = None
    Base.metadata.drop_all(bind=engine)


api = TestClient(app)


def _get(path, key=KEY, **params):
    headers = {"X-API-Key": key} if key else {}
    return api.get(path, headers=headers, params=params)


# --- auth ---------------------------------------------------------------------

def test_disabled_without_a_configured_key(monkeypatch):
    monkeypatch.setattr(settings, "feed_api_key", "")
    for path in ("/value-bets", "/results"):
        assert _get(path, key="anything").status_code == 503


def test_missing_or_wrong_key_is_401():
    for path in ("/value-bets", "/results"):
        assert _get(path, key=None).status_code == 401
        assert _get(path, key="wrong").status_code == 401


def test_bearer_works_and_other_static_keys_do_not(monkeypatch):
    assert api.get("/value-bets", headers={"Authorization": f"Bearer {KEY}"}).status_code == 200
    monkeypatch.setattr(settings, "state_api_key", "state-key")
    assert _get("/value-bets", key="state-key").status_code == 401


# --- value bets ---------------------------------------------------------------

def test_value_bets_are_future_football_value_best_first():
    rows = _get("/value-bets").json()
    # Lazio's draw (+10.2%) beats Leeds (+8.6%); tennis, no-value and in-play out
    assert [r["fixture_id"] for r in rows] == ["ev-lazio", "ev-leeds"]
    pct = [r["value_pct"] for r in rows]
    assert pct == sorted(pct, reverse=True) and min(pct) > 0
    by = {r["fixture_id"]: r for r in rows}
    leeds = by["ev-leeds"]
    assert (leeds["fixture"], leeds["home"], leeds["away"]) == ("Leeds v Spurs", "Leeds", "Spurs")
    assert (leeds["selection"], leeds["side"], leeds["market"], leeds["line"]) == \
        ("Leeds", "home", "h2h", None)
    assert (leeds["sport"], leeds["competition"], leeds["competition_key"], leeds["bookmaker"]) == \
        ("football", "EPL", "soccer_epl", "bet365")
    assert leeds["odds"] == 2.6 and leeds["fair_odds"] < leeds["odds"]
    assert leeds["value_pct"] == pytest.approx((2.6 / leeds["fair_odds"] - 1) * 100, abs=0.05)
    assert leeds["id"] == "ev-leeds:h2h:home"
    assert by["ev-lazio"]["side"] == "draw" and by["ev-lazio"]["id"] == "ev-lazio:h2h:draw"
    assert leeds["kickoff"].endswith(("Z", "+00:00"))


def test_daedalus_fields_are_all_present():
    """The coordinator's DataSourceClient reads exactly these."""
    [row, *_] = _get("/value-bets").json()
    for field in ("fixture_id", "fixture", "kickoff", "sport", "market",
                  "selection", "odds", "value_pct"):
        assert row[field] is not None


def test_sport_filter():
    tennis = _get("/value-bets", sport="tennis").json()
    assert [(r["fixture_id"], r["sport"]) for r in tennis] == [("ev-tennis", "tennis")]
    assert {r["fixture_id"] for r in _get("/value-bets", sport="all").json()} == \
        {"ev-leeds", "ev-lazio", "ev-tennis"}


def test_min_edge_reaches_below_fair():
    ids = {r["fixture_id"] for r in _get("/value-bets", min_edge=-0.2).json()}
    assert "ev-none" in ids and "ev-live" not in ids


def test_serving_records_each_fixture_and_selection_once():
    _get("/value-bets")
    _get("/value-bets")
    db = Session()
    try:
        assert sorted(f.id for f in db.query(FeedFixture)) == ["ev-lazio", "ev-leeds"]
        assert sorted(s.id for s in db.query(FeedSelection)) == \
            ["ev-lazio:h2h:draw", "ev-leeds:h2h:home"]
        assert all(f.status == "pending" for f in db.query(FeedFixture))
    finally:
        db.close()


def test_off_when_the_365_tool_is_off_or_has_no_client(monkeypatch):
    monkeypatch.setattr(settings, "enable_accabonus", False)
    assert _get("/value-bets").status_code == 503
    monkeypatch.setattr(settings, "enable_accabonus", True)
    app.state.arbs_client = None
    assert _get("/value-bets").status_code == 503


# --- results ------------------------------------------------------------------

def _served(kickoff, fid="ev-leeds", home="Leeds", away="Spurs", sides=("home",)):
    db = Session()
    db.add(FeedFixture(id=fid, sport="football", competition="EPL", competition_key="soccer_epl",
                       home=home, away=away, kickoff=kickoff))
    for side in sides:
        db.add(FeedSelection(id=f"{fid}:h2h:{side}", fixture_id=fid, bookmaker="bet365",
                             market="h2h", selection=side, side=side, odds=2.6,
                             fair_odds=2.4, value_pct=8.33))
    db.commit()
    db.close()


def test_results_are_empty_until_a_score_is_recorded():
    _served(utcnow() - timedelta(hours=3))
    assert _get("/results").json() == []


def test_flashscore_ingest_settles_served_fixtures(monkeypatch):
    monkeypatch.setattr(settings, "saves_ingest_key", "ingest-key")
    _served(utcnow() - timedelta(hours=3), sides=("home", "draw"))
    _served(utcnow() - timedelta(hours=3), fid="ev-other", home="Lazio", away="Roma")
    resp = api.post("/aw26/results", headers={"X-API-Key": "ingest-key"}, json={"matches": [
        {"home": "Leeds United", "away": "Tottenham Spurs", "goals_home": 2, "goals_away": 1},
    ]})
    assert resp.status_code == 200
    assert resp.json() == {"matches": 1, "resolved": 0, "settled": 0}   # AW 26's answer, unchanged

    [r] = _get("/results").json()
    assert (r["fixture_id"], r["status"], r["home_score"], r["away_score"]) == \
        ("ev-leeds", "finished", 2, 1)
    assert r["fixture"] == "Leeds v Spurs" and r["sport"] == "football"
    assert {s["id"]: s["outcome"] for s in r["selections"]} == \
        {"ev-leeds:h2h:home": "win", "ev-leeds:h2h:draw": "lose"}

    # `since` is strictly after the recorded time
    assert _get("/results", since=r["result_at"]).json() == []
    earlier = (utcnow() - timedelta(minutes=5)).isoformat()
    assert [x["fixture_id"] for x in _get("/results", since=earlier).json()] == ["ev-leeds"]


def test_a_score_never_settles_a_fixture_that_cannot_have_finished():
    _served(utcnow() - timedelta(minutes=30))          # still in play
    _served(utcnow() - timedelta(days=9), fid="ev-old", home="Lazio", away="Roma")
    db = Session()
    try:
        n = service.record_scores(db, [
            MatchStats(home="Leeds", away="Spurs", goals_home=0, goals_away=0),
            MatchStats(home="Lazio", away="Roma", goals_home=1, goals_away=0),
        ])
        assert n == 0
    finally:
        db.close()
    assert _get("/results").json() == []


def test_a_score_needs_both_teams_to_match():
    _served(utcnow() - timedelta(hours=3))
    db = Session()
    try:
        assert service.record_scores(db, [
            MatchStats(home="Leeds", away="Chelsea", goals_home=1, goals_away=0),
            MatchStats(home="Leeds", away="Spurs", goals_home=None, goals_away=None),
        ]) == 0
    finally:
        db.close()


@pytest.mark.parametrize("side,h,a,status,want", [
    ("home", 2, 1, "finished", "win"),
    ("away", 2, 1, "finished", "lose"),
    ("draw", 1, 1, "finished", "win"),
    ("home", 1, 1, "finished", "lose"),
    ("away", None, None, "void", "void"),
    ("home", None, None, "finished", None),
])
def test_h2h_outcome(side, h, a, status, want):
    assert service.outcome("h2h", side, None, status, h, a) == want


def test_an_unknown_market_is_never_guessed():
    assert service.outcome("first_goalscorer", None, None, "finished", 1, 0) is None


def test_selection_ids_are_stable():
    assert service.selection_id("e1", "h2h", "home", "Leeds", None) == "e1:h2h:home"
    assert service.selection_id("e1", "totals", "over", "Over 2.5", 2.5) == "e1:totals:over:2.5"
    assert service.sport_of("soccer_epl") == "football"
    assert service.sport_of("tennis_atp_paris") == "tennis"
