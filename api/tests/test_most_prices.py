"""Paddy's "most corners / most cards" prices from the desktop scraper, joined
to the team split's fair three-way (cards/most.py, routers/saves_ingest.py)."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.cards import most
from app.cards.most import PaddyMost, _paddy_for, most_fairs, most_value, store
from app.cards.oddspapi import TEAM_CORNERS_AWAY, TEAM_CORNERS_HOME
from app.config import settings
from app.main import app

KEY = "test-ingest-key"
PIN = {8.5: (1.44, 2.75), 9.5: (1.74, 2.10), 10.5: (2.20, 1.68), 11.5: (2.95, 1.42)}


def _iso(hours):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _mk(key, lines):
    outcomes = []
    for line, (over, under) in sorted(lines.items()):
        outcomes.append({"name": "Over", "point": line, "price": over})
        outcomes.append({"name": "Under", "point": line, "price": under})
    return {"key": key, "outcomes": outcomes}


def _event():
    return {
        "id": "e1", "home_team": "Arsenal", "away_team": "Chelsea",
        "commence_time": _iso(3), "sport_key": "soccer_epl",
        "bookmakers": [
            {"key": "pinnacle", "markets": [_mk("alternate_totals_corners", PIN),
                                           _mk(TEAM_CORNERS_HOME, {5.5: (1.90, 1.95)}),
                                           _mk(TEAM_CORNERS_AWAY, {4.5: (1.90, 1.95)})]},
            {"key": "paddypower", "markets": []},
        ],
    }


@pytest.fixture(autouse=True)
def _clear_store():
    most.STORE.clear()
    yield
    most.STORE.clear()


def _quote(home="Arsenal", away="Chelsea", hours=3, market="most_corners", prices=(2.0, 7.0, 2.4)):
    return PaddyMost(home, away, datetime.now(timezone.utc) + timedelta(hours=hours), market, *prices)


def test_a_quote_is_matched_by_team_names_and_kickoff_day():
    event = _event()
    assert _paddy_for(event, [_quote()]).keys() == {"most_corners"}
    assert _paddy_for(event, [_quote(home="Arsenal FC")]).keys() == {"most_corners"}   # fuzzy
    assert _paddy_for(event, [_quote(home="Real Madrid")]) == {}
    assert _paddy_for(event, [_quote(hours=40)]) == {}                                # wrong day


def test_a_fixture_with_a_price_becomes_value_rows_measured_against_the_band():
    event = _event()
    fair = next(f for f in most_fairs([event]) if f.market_key == "most_corners")
    # Prices exactly at the point fair are NOT value: the edge is measured
    # against the band's far end, so they read below fair — as a line would.
    at_fair = _quote(prices=(fair.fair("home"), fair.fair("tie"), fair.fair("away")))
    rows = most_value([event], -0.5, entries=[at_fair])
    assert {r.side for r in rows} == {"Home", "Tie", "Away"}
    assert all(r.edge < 0 for r in rows) and all(r.basis == "hybrid" for r in rows)
    assert all(r.line == 0.0 and r.market_key == "most_corners" for r in rows)
    assert next(r for r in rows if r.side == "Home").fair_odds == fair.fair("home")
    # A price 10% above the band's far end is +10% value, on that outcome only.
    generous = _quote(prices=(fair.fair_high("home") * 1.10, 7.0, 2.4))
    rows = most_value([event], 0.0, entries=[generous])
    assert [r.side for r in rows] == ["Home"] and rows[0].edge == pytest.approx(0.10, abs=0.01)
    # No quote, no row — the fair stays a fair-only row on the feed.
    assert most_value([event], -0.5, entries=[]) == []


def test_the_feed_carries_a_priced_three_way_as_bets_not_fairs():
    from app.routers.cards import build_feed

    class _Client:
        def get_events(self):
            return [_event()]

    before = next(i for i in build_feed(_Client(), -0.5) if i.event_id == "e1")
    assert {f.market for f in before.fairs} == {"most_corners"} and len(before.fairs) == 3
    assert not any(b.market == "most_corners" for b in before.bets)

    store([_quote()])
    after = next(i for i in build_feed(_Client(), -0.5) if i.event_id == "e1")
    picks = {b.pick for b in after.bets if b.market == "most_corners"}
    assert picks == {"Arsenal most corners", "Tie most corners", "Chelsea most corners"}
    assert not any(f.market == "most_corners" for f in after.fairs)


def test_the_ingest_endpoint_stores_what_it_can_read(monkeypatch):
    monkeypatch.setattr(settings, "saves_ingest_key", KEY)
    client = TestClient(app)
    r = client.post("/paddy/most-prices", headers={"X-API-Key": KEY}, json={"prices": [
        {"home": "Arsenal", "away": "Chelsea", "kickoff": _iso(3), "market": "most_corners",
         "home_price": 2.0, "tie_price": 7.0, "away_price": 2.4},
        {"home": "Arsenal", "away": "Chelsea", "kickoff": "not a time", "market": "most_cards",
         "home_price": 2.0, "tie_price": 7.0, "away_price": 2.4},
    ]})
    assert r.status_code == 200, r.text
    assert r.json()["stored"] == 1 and r.json()["rejected"][0]["reason"] == "kickoff unreadable"
    assert len(most.STORE.values()) == 1
    assert client.post("/paddy/most-prices", headers={"X-API-Key": "wrong"},
                       json={"prices": []}).status_code == 401
    bad = client.post("/paddy/most-prices", headers={"X-API-Key": KEY}, json={"prices": [
        {"home": "A", "away": "B", "kickoff": _iso(3), "market": "most_goals",
         "home_price": 2.0, "tie_price": 7.0, "away_price": 2.4}]})
    assert bad.status_code == 422
