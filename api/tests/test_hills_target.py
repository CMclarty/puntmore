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


from app.cards import oddspapi as op  # noqa: E402
from app.hills import settle  # noqa: E402

# Corners 9.5 (market 10803: outcomes 10803 Over / 10804 Under) and cards 3.5.
INDEX = {"10803": (op.CORNERS, 9.5, {"10803": "Over", "10804": "Under"}),
         "20001": (op.CARDS, 3.5, {"1": "Over", "2": "Under"}),
         "30001": (op.CORNERS, 10.0, {"5": "Over", "6": "Under"})}       # integer: never offered


def _p(price):
    return {"players": {"0": {"price": price}}}


def _board(fid):
    return {"fixtureId": fid, "bookmakerOdds": {"williamhill": {"markets": {
        "10803": {"outcomes": {"10803": _p(1.9), "10804": _p(1.9)}},
        "20001": {"outcomes": {"1": _p(2.1), "2": _p(1.7)}},
        "30001": {"outcomes": {"5": _p(1.9), "6": _p(1.9)}}}}}}


class FakeOddsPapi:
    def __init__(self, body=None):
        self.body, self.calls = body or {}, 0

    def get(self, path, **params):
        self.calls += 1
        assert path == "/settlements" and params == {"fixtureId": "a"}
        return self.body


def _hills(start, client=None):
    src = Source([_fx("a", 17, start)])
    src.boards, src.market_index, src.priced_book = {"a": _board("a")}, INDEX, "williamhill"
    src._client = client
    app.state.hills_client = src
    client_ = TestClient(app)
    client_.put("/hills/target", json={"fixture_id": "a"})
    return client_


def test_legs_are_william_hills_half_lines_on_the_target():
    c = _hills(_next(5))
    got = c.get("/hills/legs").json()["legs"]
    assert [g["label"] for g in got] == ["Over 9.5 corners", "Under 9.5 corners",
                                         "Over 3.5 cards", "Under 3.5 cards"]
    assert got[0]["market_id"] == "10803" and got[0]["outcome_id"] == "10803"


def test_the_team_sees_every_builder_logged_on_the_target():
    from app.security import require_user
    c = _hills(_next(5))
    for who, legs in (("cal", [("10803", "10803")]), ("chris", [("10803", "10803"), ("20001", "1")])):
        app.dependency_overrides[require_user] = lambda who=who: who
        c.post("/hills/bets", json={"legs": [{"market_id": m, "outcome_id": o} for m, o in legs]})
    app.dependency_overrides[require_user] = lambda: "testuser"
    c.post("/hills/bets", json={"note": "Saka anytime"})                    # a leg not on the list
    got = c.get("/hills/bets").json()["bets"]
    assert [b["text"] for b in got] == ["Saka anytime", "Over 9.5 corners + Over 3.5 cards",
                                        "Over 9.5 corners"]
    assert {b["placed_by"] for b in got} == {"cal", "chris", "testuser"}
    assert all(b["result"] == "open" for b in got)
    assert not any(k in got[0] for k in ("price", "stake", "fair"))     # no prices kept


def test_only_offered_legs_and_something_to_log():
    c = _hills(_next(5))
    bad = {"legs": [{"market_id": "30001", "outcome_id": "5"}]}            # the integer line
    assert c.post("/hills/bets", json=bad).status_code == 422
    assert c.post("/hills/bets", json={"legs": [], "note": "  "}).status_code == 422


def test_settlement_results_read_by_market_and_outcome():
    body = {"fixtureId": "a", "markets": {
        "10803": {"outcomes": {"10803": {"players": {"0": {"result": "WIN"}}},
                               "10804": {"players": {"0": {"result": "LOSE"}}}}},
        "20001": {"outcomes": {"1": {"players": {"0": {"result": "UNDECIDED"}}},
                               "2": {"players": {"0": {"result": "PUSH"}}}}}}}
    assert settle.leg_results(body) == {("10803", "10803"): "won", ("10803", "10804"): "lost",
                                        ("20001", "2"): "void"}
    assert settle.bet_result([{"result": "won"}, {"result": "lost"}], None) == "lost"
    assert settle.bet_result([{"result": "won"}], None) == "won"
    assert settle.bet_result([{"result": "won"}], "Saka anytime") == "open"   # note: by hand
    assert settle.bet_result([{"result": "won"}, {"result": None}], None) == "open"


def test_bets_settle_themselves_once_the_match_is_over():
    body = {"markets": {
        "10803": {"outcomes": {"10803": {"players": {"0": {"result": "WIN"}}}}},
        "20001": {"outcomes": {"1": {"players": {"0": {"result": "LOSE"}}}}}}}
    fake = FakeOddsPapi(body)
    c = _hills(_next(5), fake)
    leg = lambda m, o: {"market_id": m, "outcome_id": o}   # noqa: E731
    c.post("/hills/bets", json={"legs": [leg("10803", "10803")]})
    c.post("/hills/bets", json={"legs": [leg("10803", "10803"), leg("20001", "1")]})
    c.post("/hills/bets", json={"legs": [leg("10803", "10803")], "note": "Saka anytime"})
    assert fake.calls == 0                      # not kicked off: no request spent
    from app.models import HillsBet
    db = TestSession()
    for b in db.query(HillsBet):
        b.kickoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)
    db.commit()
    db.close()
    settle._LAST_CHECK.clear()
    got = {b["text"]: b for b in c.get("/hills/bets?fixture_id=a").json()["bets"]}
    assert fake.calls == 1
    assert got["Over 9.5 corners"]["result"] == "won"
    assert got["Over 9.5 corners"]["settled_by"] == "oddspapi"
    assert got["Over 9.5 corners + Over 3.5 cards"]["result"] == "lost"
    assert got["Over 9.5 corners + Over 3.5 cards"]["legs"][1] == {"label": "Over 3.5 cards",
                                                                   "result": "lost"}
    assert got["Over 9.5 corners + Saka anytime"]["result"] == "open"       # its note is by hand
    c.get("/hills/bets?fixture_id=a")
    assert fake.calls == 1                      # within 30 minutes: not asked again


def test_results_by_hand_and_anyone_removes_a_bet():
    from app.security import require_user
    c = _hills(_next(5))
    bet = c.post("/hills/bets", json={"note": "BTTS + O2.5"}).json()["bet"]
    out = c.patch(f"/hills/bets/{bet['id']}", json={"result": "won"}).json()["bet"]
    assert (out["result"], out["settled_by"]) == ("won", "testuser")
    assert c.patch(f"/hills/bets/{bet['id']}", json={"result": "void"}).status_code == 422
    app.dependency_overrides[require_user] = lambda: "someone-else"
    assert c.delete(f"/hills/bets/{bet['id']}").status_code == 200
    app.dependency_overrides[require_user] = lambda: "testuser"
    assert c.get("/hills/bets").json()["bets"] == []
    assert c.delete(f"/hills/bets/{bet['id']}").status_code == 404
