"""Hills' target match (one per team) and BB Boost's builder combinations."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.hills import builder, target
from app.main import app

client = TestClient(app)

test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                            poolclass=StaticPool)
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
    app.state.hills_client = None


def _next(weekday: int, hour: int = 15) -> datetime:
    """The next `weekday` (Mon=0) at least a day away, at `hour` UTC."""
    d = datetime.now(timezone.utc).replace(hour=hour, minute=0, second=0, microsecond=0)
    d += timedelta(days=1)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


def _fx(fid, tid, start, home="Arsenal", away="Chelsea", rnd=None):
    return {"id": fid, "home": home, "away": away, "start": start,
            "tournament_id": tid, "round": rnd}


# --- which fixtures can be picked -------------------------------------------------

def test_premier_league_weekend_fixtures_only():
    sat, wed = _next(5), _next(2)
    got = target.eligible([_fx("a", 17, sat), _fx("b", 17, wed), _fx("c", 8, sat)])
    assert [f["fixture_id"] for f in got] == ["a"]       # not midweek, not LaLiga
    assert got[0]["competition"] == "Premier League"


def test_cup_finals_by_round_name_or_as_the_only_fixture():
    sat = _next(5)
    rows = [
        _fx("sf1", 19, sat, rnd="Semi-final"), _fx("sf2", 19, sat, rnd="Semi-finals"),
        _fx("ucl", 7, sat, rnd={"name": "Final"}),
        _fx("efl", 21, sat),                                   # no round sent, only one
        _fx("x", 99, sat),                                     # not a boosted competition
    ]
    got = {f["fixture_id"]: f["competition"] for f in target.eligible(rows)}
    assert got == {"ucl": "Champions League final", "efl": "EFL Cup final"}


def test_a_cup_with_several_unnamed_rounds_lists_none_of_them():
    sat = _next(5)
    assert target.eligible([_fx("a", 19, sat), _fx("b", 19, sat, home="Leeds")]) == []


# --- the team's target ------------------------------------------------------------

class Source:
    def __init__(self, fixtures, events=()):
        self.fixtures, self._events, self.book = fixtures, list(events), "williamhill"
        self.last_sweep = {}

    def get_events(self):
        return list(self._events)


def test_one_target_for_the_whole_team():
    sat = _next(5)
    app.state.hills_client = Source([_fx("a", 17, sat), _fx("b", 17, sat, home="Leeds")])
    assert client.get("/hills/target").json() == {"target": None}
    assert [f["fixture_id"] for f in client.get("/hills/fixtures").json()["fixtures"]] == ["a", "b"]

    t = client.put("/hills/target", json={"fixture_id": "a", "boost_pct": 50}).json()["target"]
    assert (t["match"], t["boost_pct"], t["set_by"]) == ("Arsenal vs Chelsea", 50.0, "testuser")
    # Someone else changes it: there is still exactly one.
    client.put("/hills/target", json={"fixture_id": "b"})
    t = client.get("/hills/target").json()["target"]
    assert t["fixture_id"] == "b" and t["boost_pct"] == 25.0
    assert client.delete("/hills/target").json() == {"target": None}
    assert client.get("/hills/target").json() == {"target": None}


def test_only_a_pickable_fixture_or_a_known_boost_is_taken():
    app.state.hills_client = Source([_fx("a", 17, _next(2))])        # midweek
    assert client.put("/hills/target", json={"fixture_id": "a"}).status_code == 404
    app.state.hills_client = Source([_fx("a", 17, _next(5))])
    assert client.put("/hills/target", json={"fixture_id": "a", "boost_pct": 30}).status_code == 422


def test_a_target_that_has_kicked_off_is_over():
    db = TestSession()
    fixture = {"fixture_id": "a", "home": "A", "away": "B", "competition": "Premier League",
               "kickoff": (datetime.now(timezone.utc) - timedelta(minutes=5))
               .strftime("%Y-%m-%dT%H:%M:%SZ")}
    target.set_target(db, fixture, 25, "cal")
    assert target.current(db) is None
    db.close()


# --- BB Boost ---------------------------------------------------------------------

def _row(market_key, side, line, odds, fair):
    return SimpleNamespace(market_key=market_key, side=side, line=line, paddy_odds=odds,
                           fair_odds=fair, edge=odds / fair - 1,
                           home_team="Arsenal", away_team="Chelsea")


ROWS = [
    _row("alternate_totals_corners", "Over", 9.5, 1.90, 1.80),
    _row("alternate_totals_corners", "Over", 10.5, 2.50, 2.40),
    _row("team_corners_home", "Over", 4.5, 1.80, 1.85),
    _row("alternate_totals_cards", "Over", 3.5, 1.70, 1.75),
    _row("alternate_totals_goals", "Over", 2.5, 2.00, 1.95),
]


def test_builders_take_one_leg_per_stat():
    got = builder.combos(ROWS, min_fair=1.0)
    for c in got:
        stats = [builder.FAMILY[leg["market"]] for leg in c["legs"]]
        assert len(stats) == len(set(stats))               # never two corners lines
        assert 2 <= len(c["legs"]) <= 3
    # 3 corners legs x 1 cards x 1 goals: 3+3+1 doubles, 3 trebles.
    assert len(got) == 10


def test_fair_floor_and_order():
    got = builder.combos(ROWS, min_fair=6.0)
    assert got and all(c["fair"] >= 6.0 for c in got)
    ratio = [c["singles"] / c["fair"] for c in got]
    assert ratio == sorted(ratio, reverse=True)
    top = got[0]
    assert top["fair"] == round(1.80 * 1.75 * 1.95, 2)    # O9.5 corners, cards, goals
    assert top["singles"] == round(1.90 * 1.70 * 2.00, 2)


def test_builder_endpoint_needs_a_target():
    assert client.get("/hills/builder").json() == {"target": None, "legs": 0, "combos": []}


def test_builder_prices_the_target_events_william_hill_lines():
    sat = _next(5)

    def market(key, over, under):
        return {"key": key, "outcomes": [{"name": "Over", "point": 3.5, "price": over},
                                         {"name": "Under", "point": 3.5, "price": under}]}
    event = {"id": "a", "home_team": "Arsenal", "away_team": "Chelsea",
             "commence_time": sat.isoformat(), "sport_key": "soccer_epl", "bookmakers": [
                 {"key": "pinnacle", "markets": [market("alternate_totals_corners", 1.95, 1.85),
                                                 market("alternate_totals_cards", 1.90, 1.90)]},
                 {"key": "williamhill", "markets": [market("alternate_totals_corners", 2.10, 1.70),
                                                    market("alternate_totals_cards", 2.00, 1.75)]},
             ]}
    app.state.hills_client = Source([_fx("a", 17, sat)], [event])
    client.put("/hills/target", json={"fixture_id": "a"})
    body = client.get("/hills/builder?min_fair=1.0").json()
    assert body["target"]["fixture_id"] == "a" and body["legs"] >= 2
    assert body["combos"] and all(len(c["legs"]) == 2 for c in body["combos"])
    assert {leg["market"] for leg in body["combos"][0]["legs"]} == {"corners", "cards"}
