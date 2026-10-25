"""AW 26 settlement cadence: the FlashScore scraper reads only the leagues of
legs still pending, looked up in the value snapshots, and falls back to every
league when one is unknown."""
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.aw26 import snapshots
from app.cards.value import CardValueRow
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import AwLeg, AwSlip
from app.time_utils import utcnow

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
Session = sessionmaker(bind=engine)
KEY = "test-ingest-key"
SCRIPT = Path(__file__).resolve().parents[2] / "local" / "flashscore_results.py"


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


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("flashscore_results", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _row(home, away, sport_key):
    ko = (utcnow() - timedelta(hours=3)).isoformat() + "Z"
    return CardValueRow(event_id=home, home_team=home, away_team=away, commence_time=ko,
                        sport_key=sport_key, market_key="alternate_totals_corners",
                        market_label="", line=9.5, side="Over", paddy_odds=2.0,
                        pinnacle_odds=2.0, fair_odds=2.0, pinnacle_margin=0.05, edge=0.0)


def _slip(db, receipt, events, status="open", result="pending"):
    slip = AwSlip(placed_at=utcnow(), user="u", receipt=receipt, offer="other", stake=10,
                  status=status)
    for i, e in enumerate(events):
        slip.legs.append(AwLeg(position=i, event=e, odds=2.0, result=result))
    db.add(slip)


def _get(key=KEY):
    return TestClient(app).get("/aw26/open-leagues", headers={"X-API-Key": key})


def test_open_leagues_from_the_snapshots():
    db = Session()
    snapshots.record(db, [_row("Arsenal", "Chelsea", "soccer_epl"),
                          _row("Real Madrid", "Barcelona", "soccer_spain_la_liga"),
                          _row("Inter", "Milan", "soccer_italy_serie_a")], "paddy")
    _slip(db, "A", ["Arsenal v Chelsea", "Real Madrid v Barcelona"])
    _slip(db, "B", ["Inter v Milan"], status="lost", result="lost")      # settled: ignored
    _slip(db, "C", ["Brentford v Fulham"])                               # no snapshot knows it
    db.commit()
    db.close()
    body = _get().json()
    assert body["events"] == ["Arsenal v Chelsea", "Brentford v Fulham", "Real Madrid v Barcelona"]
    assert body["sport_keys"] == ["soccer_epl", "soccer_spain_la_liga"]
    assert body["unknown"] == ["Brentford v Fulham"]


def test_open_leagues_needs_the_key():
    assert _get("wrong").status_code == 401


def test_script_reads_only_the_pending_leagues(script):
    assert script.leagues_for({"events": ["A v B"], "sport_keys": ["soccer_epl"],
                               "unknown": []}) == ["england/premier-league"]


def test_script_reads_nothing_when_nothing_is_pending(script):
    assert script.leagues_for({"events": [], "sport_keys": [], "unknown": []}) == []


def test_script_falls_back_to_every_league(script):
    every = script.DEFAULT_LEAGUES
    assert script.leagues_for({"events": ["A v B"], "sport_keys": [],
                               "unknown": ["A v B"]}) == every
    # A league with no verified FlashScore path is read the long way too.
    assert script.leagues_for({"events": ["A v B"], "sport_keys": ["soccer_england_league1"],
                               "unknown": []}) == every


def test_every_mapped_league_is_one_the_scraper_already_reads(script):
    assert set(script.SPORT_KEY_LEAGUES.values()) <= set(script.DEFAULT_LEAGUES)
