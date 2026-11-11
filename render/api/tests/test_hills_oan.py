"""BB Boost on odds-api.net: legs logged off William Hill's lines in the
snapshot, settled from /events/{id}/results. Hermetic: FakeOan answers from
the recorded responses. Target and bets are written straight to the DB with
kick-offs relative to the real clock, so the tests do not age."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.cards.oddsapinet import (OddsApiNetHillsSource, leg_id, leg_result, parse_leg_id,
                                  results_counts)
from app.cards.oddspapi import CARDS, CORNERS, GOALS, TEAM_CARDS_HOME
from app.database import Base, get_db
from app.hills import settle
from app.hills.legs import _label
from app.main import app
from app.models import HillsBet, HillsTarget
from tests.oan_fixtures import Clock, FakeOan, load, routes

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
    settle._LAST_CHECK.clear()
    yield
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=test_engine)
    app.state.hills_client = None
    settle._LAST_CHECK.clear()


def make(fake=None, **kw):
    fake = fake or FakeOan(routes())
    clock = Clock()
    opts = dict(runner=lambda fn: fn(), now=clock.now, monotonic=clock.mono, window_hours=168)
    opts.update(kw)
    return OddsApiNetHillsSource(fake, **opts), fake, clock


def _naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _target(fixture_id="oan:1417142635", home="England", away="Croatia"):
    db = TestSession()
    db.add(HillsTarget(id=1, fixture_id=fixture_id, home=home, away=away,
                       kickoff=_naive_now() + timedelta(days=2),
                       competition="Premier League", boost_pct=25.0, set_by="cal"))
    db.commit()
    db.close()


@pytest.fixture
def swept():
    """The source swept as in Task 3, on the app, England v Croatia targeted."""
    src, fake, clock = make()
    src.get_events()
    app.state.hills_client = src
    _target()
    return src, fake, clock


KAWASAKI, KASHIWA = "Kawasaki Frontale", "Kashiwa Reysol"


def _bet(fixture_id, legs, note=None, home=KAWASAKI, away=KASHIWA, ago=timedelta(hours=3)):
    """A bet on `fixture_id` straight into the DB. `legs` are (key, line, side)."""
    picked = [{"market_id": leg_id(k, line), "outcome_id": side,
               "label": _label(k, side, line, home, away), "result": None}
              for k, line, side in legs]
    text = " + ".join([p["label"] for p in picked] + ([note] if note else []))
    db = TestSession()
    db.add(HillsBet(fixture_id=fixture_id, match=f"{home} vs {away}",
                    kickoff=_naive_now() - ago, legs=text, legs_json=json.dumps(picked),
                    note=note, placed_by="cal"))
    db.commit()
    db.close()


# --- leg ids and results ------------------------------------------------------

def test_leg_ids_round_trip():
    assert leg_id(CORNERS, 9.5) == "alternate_totals_corners:9.5"
    assert parse_leg_id("alternate_totals_corners:9.5") == (CORNERS, 9.5)
    assert parse_leg_id("10803") is None and parse_leg_id("bogus:x") is None


def test_results_are_read_per_side_full_time_final():
    c = results_counts(load("results_1595650036_final.json"))
    assert {k: c[k] for k in [("corners", "home"), ("corners", "away"), ("cards", "home"),
            ("cards", "away"), ("standard", "home"), ("standard", "away")]} == {
        ("corners", "home"): 3, ("corners", "away"): 3, ("cards", "home"): 2,
        ("cards", "away"): 4, ("standard", "home"): 1, ("standard", "away"): 2}
    assert results_counts(load("results_2161612418_pending.json")) is None
    assert leg_result(c, "alternate_totals_corners:9.5", "Under") == "won"
    assert leg_result(c, "alternate_totals_cards:3.5", "Over") == "won"
    assert leg_result(c, "team_cards_home:2.5", "Over") == "lost"
    assert leg_result(c, "totals_goals:2.5", "Over") == "won"
    assert leg_result(c, "team_corners_away:3.5", "Under") == "won"
    assert leg_result(c, "totals_saves:4.5", "Over") is None
    assert leg_result(c, "alternate_totals_corners:10.0", "Over") is None


def test_results_need_the_match_finished():
    body = load("results_1595650036_final.json")
    body["result"]["event_status"] = "live"
    assert results_counts(body) is None


def test_a_match_leg_needs_both_sides_final():
    body = load("results_1595650036_final.json")
    for rec in body["result"]["score_records"]:
        if (rec["metric"], rec["side"], rec["period"]) == ("corners", "away", "full time"):
            rec["status"] = "live"
    c = results_counts(body)
    assert leg_result(c, "alternate_totals_corners:9.5", "Under") is None
    assert leg_result(c, "team_corners_home:2.5", "Over") == "won"


# --- legs ---------------------------------------------------------------------

def test_legs_on_an_odds_api_net_target(swept):
    legs = client.get("/hills/legs").json()["legs"]
    assert len(legs) == 94
    assert legs[0] == {"market_id": "alternate_totals_corners:5.5", "outcome_id": "Over",
                       "key": CORNERS, "market": "corners", "side": "Over", "line": 5.5,
                       "label": "Over 5.5 corners"}
    labels = {leg["label"] for leg in legs}
    assert {"England Over 6.5 corners", "Croatia Under 0.5 cards", "Over 2.5 goals"} <= labels


def test_the_target_board_is_fetched_when_missing():
    src, fake, _ = make()                          # never swept
    assert len(src.hills_legs("oan:1417142635", "England", "Croatia")) == 94
    assert [p for p, _ in fake.calls] == ["/events/1417142635/odds/snapshot"]
    assert src.hills_legs("oan:4229698994", "Armenia", "Cyprus") == []
    assert src.hills_legs("a", "Arsenal", "Chelsea") == [] and len(fake.calls) == 2


def test_a_fresh_target_board_is_not_fetched_again_and_a_stale_one_is():
    src, fake, clock = make()
    src.hills_legs("oan:1417142635", "England", "Croatia")
    src.hills_legs("oan:1417142635", "England", "Croatia")
    assert len(fake.calls) == 1
    clock.advance(3600)
    assert len(src.hills_legs("oan:1417142635", "England", "Croatia")) == 94
    assert len(fake.calls) == 2


def test_a_failed_target_fetch_is_held_and_answers_no_legs():
    fake = FakeOan({})                             # every path a 404
    src, fake, _ = make(fake)
    assert src.hills_legs("oan:1417142635", "England", "Croatia") == []
    assert src.hills_legs("oan:1417142635", "England", "Croatia") == []
    assert len(fake.calls) == 1                    # billed: not re-sent within the hold


def test_ownership_is_the_oan_prefix():
    src, _, _ = make()
    assert src.owns_fixture("oan:1595650036") and not src.owns_fixture("a")


def test_only_william_hills_lines_can_be_logged(swept):
    ok = client.post("/hills/bets", json={"legs": [{"market_id": "alternate_totals_corners:9.5",
                                                    "outcome_id": "Over"}]})
    assert ok.status_code == 200 and ok.json()["bet"]["text"] == "Over 9.5 corners"
    bad = {"legs": [{"market_id": "alternate_totals_corners:10.0", "outcome_id": "Over"}]}
    assert client.post("/hills/bets", json=bad).status_code == 422


# --- settling -----------------------------------------------------------------

def test_an_odds_api_net_bet_settles_from_results():
    src, fake, _ = make()
    app.state.hills_client = src
    fid = "oan:1595650036"
    _bet(fid, [(CORNERS, 9.5, "Under")])
    _bet(fid, [(CARDS, 3.5, "Over"), (TEAM_CARDS_HOME, 2.5, "Over")])
    _bet(fid, [(GOALS, 2.5, "Over")], note="Saka anytime")
    settle._LAST_CHECK.clear()
    got = {b["text"]: b for b in client.get(f"/hills/bets?fixture_id={fid}").json()["bets"]}
    assert (got["Under 9.5 corners"]["result"], got["Under 9.5 corners"]["settled_by"]) == (
        "won", "odds-api.net")
    two = got["Over 3.5 cards + Kawasaki Frontale Over 2.5 cards"]
    assert two["result"] == "lost" and [leg["result"] for leg in two["legs"]] == ["won", "lost"]
    assert got["Over 2.5 goals + Saka anytime"]["result"] == "open"
    assert got["Over 2.5 goals + Saka anytime"]["legs"][0]["result"] == "won"
    assert fake.calls.count(("/events/1595650036/results", {})) == 1
    client.get(f"/hills/bets?fixture_id={fid}")
    assert fake.calls.count(("/events/1595650036/results", {})) == 1     # within 30 minutes


def test_pending_results_and_oddspapi_bets_stay_open():
    src, fake, _ = make()
    app.state.hills_client = src
    _bet("oan:2161612418", [(CORNERS, 9.5, "Over")])
    _bet("a", [(CORNERS, 9.5, "Over")])
    assert all(b["result"] == "open"
               for b in client.get("/hills/bets?fixture_id=oan:2161612418").json()["bets"])
    assert all(b["result"] == "open" for b in client.get("/hills/bets?fixture_id=a").json()["bets"])
    assert not [p for p, _ in fake.calls if "/events/a/" in p]
    assert ("/events/2161612418/results", {}) in fake.calls


def test_a_bet_not_yet_due_spends_nothing():
    src, fake, _ = make()
    app.state.hills_client = src
    _bet("oan:1595650036", [(CORNERS, 9.5, "Under")], ago=timedelta(minutes=30))
    client.get("/hills/bets?fixture_id=oan:1595650036")
    assert fake.calls == []


def test_an_odds_api_net_bet_is_never_sent_to_oddspapi():
    class OddsPapiSource:
        class _client:
            calls = []

            @classmethod
            def get(cls, path, **params):
                cls.calls.append((path, params))
                return {}
    app.state.hills_client = OddsPapiSource()
    _bet("oan:1595650036", [(CORNERS, 9.5, "Under")])
    client.get("/hills/bets?fixture_id=oan:1595650036")
    assert OddsPapiSource._client.calls == []
