"""Hills' target match: one per team, picked from the boosted fixtures."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.hills import target
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


# --- BB Boost's bet log -------------------------------------------------------------

def test_the_team_sees_every_builder_logged_on_the_target():
    from app.security import require_user
    sat = _next(5)
    app.state.hills_client = Source([_fx("a", 17, sat), _fx("b", 17, sat, home="Leeds")])
    assert client.post("/hills/bets", json={"legs": "O9.5 corners"}).status_code == 409   # no target
    client.put("/hills/target", json={"fixture_id": "a"})
    for i, who in enumerate(("cal", "chris", "cal")):
        app.dependency_overrides[require_user] = lambda who=who: who
        client.post("/hills/bets", json={"legs": f"builder {i}"})
    app.dependency_overrides[require_user] = lambda: "testuser"
    got = client.get("/hills/bets").json()["bets"]
    assert [b["legs"] for b in got] == ["builder 2", "builder 1", "builder 0"]   # newest first
    assert {b["placed_by"] for b in got} == {"cal", "chris"}
    assert all(b["result"] == "open" and b["match"] == "Arsenal vs Chelsea" for b in got)
    assert set(got[0]) == {"id", "fixture_id", "match", "kickoff", "legs", "result",
                           "placed_by", "placed_at"}                      # no prices, no fairs
    # A new target starts its own list; the old one is still there by id.
    client.put("/hills/target", json={"fixture_id": "b"})
    assert client.get("/hills/bets").json()["bets"] == []
    assert len(client.get("/hills/bets?fixture_id=a").json()["bets"]) == 3


def test_results_are_set_by_hand_and_anyone_removes_a_bet():
    from app.security import require_user
    app.state.hills_client = Source([_fx("a", 17, _next(5))])
    client.put("/hills/target", json={"fixture_id": "a"})
    bet = client.post("/hills/bets", json={"legs": "BTTS + O2.5"}).json()["bet"]
    assert client.patch(f"/hills/bets/{bet['id']}", json={"result": "won"}).json()["bet"]["result"] == "won"
    assert client.patch(f"/hills/bets/{bet['id']}", json={"result": "void"}).status_code == 422
    assert client.post("/hills/bets", json={"legs": "  "}).status_code == 422
    app.dependency_overrides[require_user] = lambda: "someone-else"
    assert client.delete(f"/hills/bets/{bet['id']}").status_code == 200
    app.dependency_overrides[require_user] = lambda: "testuser"
    assert client.get("/hills/bets").json()["bets"] == []
    assert client.delete(f"/hills/bets/{bet['id']}").status_code == 404
