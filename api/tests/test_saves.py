"""Goalkeeper saves: Paddy's line (OddsPapi) against Bookie Bashing's fair
price, sent in by the local script — see cards/saves.py."""
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.cards import saves
from app.cards.oddspapi import SAVES
from app.config import settings
from app.routers import saves_ingest

KO = datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _empty_store():
    saves.STORE.clear()
    yield
    saves.STORE.clear()


def _event(home="Arsenal", away="Chelsea", over=2.1, under=1.75, line=7.5, ko="2026-10-18T15:00:00Z"):
    return {
        "id": "fx1", "home_team": home, "away_team": away, "commence_time": ko,
        "sport_key": "soccer_epl",
        "bookmakers": [{"key": "paddypower", "markets": [{"key": SAVES, "outcomes": [
            {"name": "Over", "point": line, "price": over},
            {"name": "Under", "point": line, "price": under},
        ]}]}],
    }


def test_a_missing_side_is_the_complement_and_a_vigged_pair_is_refused():
    over, under = saves.complete(2.0, None)
    assert (over, round(under, 4)) == (2.0, 2.0)
    assert saves.complete(None, 1.25)[0] == pytest.approx(5.0)
    assert saves.complete(1.9, 1.9) is None          # 105% book: not fair
    assert saves.complete(None, None) is None


def test_paddy_is_valued_against_the_fair_price_on_the_matching_fixture():
    saves.store([saves.FairSaves("Arsenal FC", "Chelsea", KO, 7.5, 1.9, 2.111)])
    rows = saves.saves_value([_event()], min_edge=0.0)
    assert [(r.side, r.line, r.edge) for r in rows] == [("Over", 7.5, round(2.1 / 1.9 - 1, 4))]
    assert rows[0].market_key == SAVES and rows[0].pinnacle_margin == 0.0


def test_a_fair_price_for_another_day_or_other_teams_is_not_joined():
    saves.store([
        saves.FairSaves("Arsenal", "Chelsea", datetime(2026, 10, 25, 15, tzinfo=timezone.utc), 7.5, 1.5, 3.0),
        saves.FairSaves("Arsenal", "Everton", KO, 7.5, 1.5, 3.0),
        saves.FairSaves("Arsenal U21", "Chelsea U21", KO, 7.5, 1.5, 3.0),
    ])
    assert saves.saves_value([_event()], min_edge=-1.0) == []


def test_a_line_paddy_does_not_quote_gives_no_row():
    saves.store([saves.FairSaves("Arsenal", "Chelsea", KO, 8.5, 1.5, 3.0)])
    assert saves.saves_value([_event(line=7.5)], min_edge=-1.0) == []


def test_saves_rows_reach_every_consumer_through_price_events():
    from app.routers.cards import price_events
    saves.store([saves.FairSaves("Arsenal", "Chelsea", KO, 7.5, 1.9, 2.111)])
    rows = price_events([_event()], 0.0)
    assert [r.market_key for r in rows] == [SAVES]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "saves_ingest_key", "k-123")
    app = FastAPI()
    app.include_router(saves_ingest.router)
    return TestClient(app)


def test_the_ingest_endpoint_needs_its_key(client, monkeypatch):
    body = {"prices": []}
    assert client.post("/cards/saves-fair", json=body).status_code == 401
    assert client.post("/cards/saves-fair", json=body, headers={"X-API-Key": "nope"}).status_code == 401
    monkeypatch.setattr(settings, "saves_ingest_key", "")
    assert client.post("/cards/saves-fair", json=body, headers={"X-API-Key": "k-123"}).status_code == 503


def test_the_ingest_endpoint_stores_good_rows_and_says_why_others_were_refused(client):
    r = client.post("/cards/saves-fair", headers={"X-API-Key": "k-123"}, json={"prices": [
        {"home": "Arsenal", "away": "Chelsea", "kickoff": "2026-10-18T15:00:00Z", "line": 7.5, "over": 1.9},
        {"home": "Leeds", "away": "Fulham", "kickoff": "not a time", "line": 6.5, "over": 1.9},
        {"home": "Spurs", "away": "Villa", "kickoff": "2026-10-18T15:00:00", "line": 6.5,
         "over": 1.8, "under": 1.8},
    ]})
    assert r.status_code == 200
    out = r.json()
    assert out["stored"] == 1
    assert [x["reason"] for x in out["rejected"]] == [
        "kickoff unreadable", "no fair price, or over/under not a fair pair"]
    assert len(saves.STORE) == 1
